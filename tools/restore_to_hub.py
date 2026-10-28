#!/usr/bin/env python3
"""Put an "Oasis Hub export" (Admin -> Download Everything) back into a Hub install.

Ships inside every export as Restore/restore_to_hub.py, and is what Admin -> Download Everything ->
Upload runs. Python 3 standard library only. The export can be the downloaded .zip itself or the
unzipped folder (default: the folder this script is in).

  Whole Hub (a new or replaced install; the current database is saved first):
    python3 restore_to_hub.py /home/occnj/occ_hub --replace-database

  Add items to a Hub that already has content (new ids; existing content is never changed, and
  records the Hub already has are skipped, so running it twice adds nothing twice):
    python3 restore_to_hub.py /home/occnj/occ_hub --merge --dry-run          # see what would happen
    python3 restore_to_hub.py /home/occnj/occ_hub --merge --only "Sermon Notes"
    python3 restore_to_hub.py /home/occnj/occ_hub --merge --export oasis-hub-export-2026-10-28.zip

  Only the pictures and documents:
    python3 restore_to_hub.py /home/occnj/occ_hub --files

Files keep their names (they are unique), so the "uploads/<name>" values stored in the records
stay valid; an existing file with the same name is never overwritten. After any change, restart
the Hub: sudo systemctl restart oasis-hub
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile


class Export:
    """An export, read either from its .zip or from the unzipped folder."""

    def __init__(self, path):
        self.path = path
        self.zf = None
        if os.path.isfile(path) and zipfile.is_zipfile(path):
            self.zf = zipfile.ZipFile(path)
            manifests = sorted((n for n in self.zf.namelist() if n.endswith('manifest.json')), key=len)
            if not manifests:
                raise ValueError('This ZIP is not an Oasis Hub download (it has no manifest.json).')
            self.root = manifests[0][:-len('manifest.json')]
            self.names = set(self.zf.namelist())
        elif os.path.isfile(os.path.join(path, 'manifest.json')):
            self.root = ''
        else:
            raise ValueError(f'{path} is not an Oasis Hub download (no manifest.json).')
        self.manifest = json.loads(self.read('manifest.json'))
        if self.manifest.get('format') != 'oasis-hub-export':
            raise ValueError('This is not an Oasis Hub download (unknown format).')

    def read(self, rel):
        if self.zf:
            return self.zf.read(self.root + rel)
        with open(os.path.join(self.path, rel), 'rb') as f:
            return f.read()

    def exists(self, rel):
        return (self.root + rel) in self.names if self.zf else os.path.exists(os.path.join(self.path, rel))

    def copy_to(self, rel, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + '.restoring'
        if self.zf:
            with self.zf.open(self.root + rel) as src, open(tmp, 'wb') as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
        else:
            shutil.copy2(os.path.join(self.path, rel), tmp)
        os.replace(tmp, dest)


def new_report():
    return {'added': [], 'already': [], 'skipped': [], 'files_copied': 0, 'files_kept': 0, 'files_missing': []}


def copy_file(export, uploads_dir, name, dry_run, report):
    info = export.manifest['uploads'].get(name)
    dest = os.path.join(uploads_dir, name)
    if os.path.exists(dest):
        report['files_kept'] += 1
    elif not info or not export.exists(info['zip_path']):
        report['files_missing'].append(name)
    else:
        if not dry_run:
            export.copy_to(info['zip_path'], dest)
        report['files_copied'] += 1


def copy_all_files(export, uploads_dir, dry_run, report):
    for name in export.manifest['uploads']:
        copy_file(export, uploads_dir, name, dry_run, report)


def selected(item, section, only):
    if not only:
        return True
    for want in only:
        w = want.strip('/').lower()
        if section['name'].lower() == w or item['folder'].lower() == w or item['folder'].lower().startswith(w + '/'):
            return True
    return False


def replace_database(export, db_path, dry_run=False):
    """Copy the export's database over db_path with SQLite's backup API, which is safe even while
    the Hub is running. The current database is saved first. Returns the saved copy's path."""
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    saved = None
    if os.path.exists(db_path):
        saved = f'{db_path}.before-restore-{stamp}'
        if not dry_run:
            src, dst = sqlite3.connect(db_path), sqlite3.connect(saved)
            src.backup(dst)
            src.close()
            dst.close()
    if dry_run:
        return saved
    fd, tmp = tempfile.mkstemp(suffix='.db', dir=os.path.dirname(os.path.abspath(db_path)))
    os.close(fd)
    try:
        with open(tmp, 'wb') as f:
            f.write(export.read(export.manifest['database']))
        src, dst = sqlite3.connect(tmp), sqlite3.connect(db_path)
        src.backup(dst)
        src.close()
        dst.close()
    finally:
        os.remove(tmp)
    return saved


def merge(export, db_path, uploads_dir, only=(), include_settings=False, dry_run=False, report=None):
    report = report if report is not None else new_report()
    manifest = export.manifest
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = {t: [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')] for t in tables}
    fks = {t: {fk[3]: fk[2] for fk in conn.execute(f'PRAGMA foreign_key_list("{t}")')} for t in tables}
    id_map, self_links, files = {}, [], []

    def same_row(table, rec):
        """The id of a row that already holds exactly this record, if any."""
        keep = [c for c in cols[table] if c != 'id' and c in rec]
        if not keep:
            return None
        where = ' AND '.join(f'"{c}" IS ?' for c in keep)
        row = conn.execute(f'SELECT id FROM "{table}" WHERE {where} LIMIT 1', [rec[c] for c in keep]).fetchone()
        return row[0] if row else None

    def insert(table, rec):
        keep = [c for c in cols[table] if c != 'id' and c in rec]
        cur = conn.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in keep)}) '
                           f'VALUES ({", ".join("?" * len(keep))})', [rec[c] for c in keep])
        return cur.lastrowid

    for section in manifest['sections']:
        for item in section['items']:
            if not selected(item, section, only):
                continue
            table = item['table']
            if table not in tables:
                report['skipped'].append(f"{item['folder']}: this Hub has no {table} table")
                continue
            if 'id' not in cols[table]:                 # settings-like tables: only on request, never overwriting
                if include_settings:
                    for rec in item['records']:
                        keep = [c for c in cols[table] if c in rec]
                        conn.execute(f'INSERT OR IGNORE INTO "{table}" ({", ".join(keep)}) VALUES ({", ".join("?" * len(keep))})',
                                     [rec[c] for c in keep])
                    files += item['files']
                    report['added'].append(f"{item['folder']} (only keys this Hub did not have)")
                continue
            rec = dict(item['records'][0])
            missing_link = None
            for col, ref in fks[table].items():
                if ref == table or rec.get(col) in (None, ''):
                    continue
                if (ref, rec[col]) in id_map:
                    rec[col] = id_map[(ref, rec[col])]
                else:
                    missing_link = f'it points to {ref} #{rec[col]}, which was not added in this run'
            if missing_link:
                report['skipped'].append(f"{item['folder']}: {missing_link}")
                continue
            existing = same_row(table, rec)
            if existing:
                id_map[(table, item['id'])] = existing
                report['already'].append(item['folder'])
                continue
            try:
                new_id = insert(table, rec)
            except sqlite3.IntegrityError as e:
                report['skipped'].append(f"{item['folder']}: already on this Hub ({e})")
                continue
            id_map[(table, item['id'])] = new_id
            for col, ref in fks[table].items():
                if ref == table and rec.get(col) not in (None, ''):
                    self_links.append((table, col, new_id, rec[col]))
            for child in item['children']:
                if child['table'] not in tables:
                    continue
                crec = dict(child['record'])
                crec[child['parent_column']] = new_id
                try:
                    insert(child['table'], crec)
                except sqlite3.IntegrityError as e:
                    report['skipped'].append(f"{item['folder']}: a {child['table']} row ({e})")
                    continue
                files += child['files']
            files += item['files']
            route = item['public_route_pattern'].format(id=new_id) if item.get('public_route_pattern') else ''
            report['added'].append(f"{item['folder']} -> {table} #{new_id}" + (f'  {route}' if route else ''))

    for table, col, new_id, old_parent in self_links:   # e.g. a hub card inside a group card
        conn.execute(f'UPDATE "{table}" SET "{col}"=? WHERE id=?', (id_map.get((table, old_parent)), new_id))

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()
    for name in dict.fromkeys(f['name'] for f in files):
        copy_file(export, uploads_dir, name, dry_run, report)
    return report


def print_report(report, dry_run):
    print(('DRY RUN, nothing changed. ' if dry_run else '') + 'Summary:')
    for line in report['added']:
        print('  added   ', line)
    if report['already']:
        print(f"  already here (left as is): {len(report['already'])} item(s)")
    for line in report['skipped']:
        print('  skipped ', line)
    print(f"  files: {report['files_copied']} copied, {report['files_kept']} already there"
          + (f", {len(report['files_missing'])} missing from the export" if report['files_missing'] else ''))
    if not dry_run:
        print('Restart the Hub to see the changes: sudo systemctl restart oasis-hub')


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('hub', help='the Hub install folder (the one with oasis.db and static/), e.g. /home/occnj/occ_hub')
    ap.add_argument('--export', default=here, help='the export: its .zip or unzipped folder (default: the folder this script is in)')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--replace-database', action='store_true', help='whole Hub: replace oasis.db (old one kept) and copy every file')
    mode.add_argument('--merge', action='store_true', help='add items to the Hub with new ids')
    mode.add_argument('--files', action='store_true', help='only copy pictures and documents')
    ap.add_argument('--only', action='append', default=[], help='with --merge: a section ("Sermon Notes") or an item folder; repeatable')
    ap.add_argument('--include-settings', action='store_true', help='with --merge: also add site settings the Hub does not have yet')
    ap.add_argument('--dry-run', action='store_true', help='show what would happen; change nothing')
    args = ap.parse_args(argv)

    hub = os.path.abspath(args.hub)
    if not os.path.isdir(os.path.join(hub, 'static')):
        sys.exit(f'{hub} does not look like a Hub install (no static/ folder).')
    try:
        export = Export(args.export)
    except ValueError as e:
        sys.exit(str(e))
    db_path, uploads_dir = os.path.join(hub, 'oasis.db'), os.path.join(hub, 'static', 'uploads')
    report = new_report()

    if args.replace_database:
        saved = replace_database(export, db_path, args.dry_run)
        if saved:
            print(f'The previous database is saved as {saved}')
        copy_all_files(export, uploads_dir, args.dry_run, report)
        report['added'].append('the whole database')
    elif args.files:
        copy_all_files(export, uploads_dir, args.dry_run, report)
    else:
        if not os.path.exists(db_path):
            sys.exit('That Hub has no oasis.db yet. Start it once (so it creates its database), or use --replace-database.')
        merge(export, db_path, uploads_dir, args.only, args.include_settings, args.dry_run, report)
    print_report(report, args.dry_run)
    return report


if __name__ == '__main__':
    main()
