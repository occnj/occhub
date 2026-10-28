#!/usr/bin/env python3
"""Put an "Oasis Hub export" (Admin -> Download Everything) back into a Hub install.

Ships inside every export as Restore/restore_to_hub.py. Python 3 standard library only.
Run it from anywhere; it finds the export next to it (or pass --export).

  Whole Hub (a new or replaced install; stop the Hub first):
    python3 restore_to_hub.py /home/occnj/occ_hub --replace-database

  Add items to a Hub that already has content (new ids; nothing existing is changed):
    python3 restore_to_hub.py /home/occnj/occ_hub --merge --dry-run          # see what would happen
    python3 restore_to_hub.py /home/occnj/occ_hub --merge --only "Sermon Notes"
    python3 restore_to_hub.py /home/occnj/occ_hub --merge --only "Sermon Notes/003 2026-10-18 Invitation"

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


def load(export_root):
    with open(os.path.join(export_root, 'manifest.json'), encoding='utf-8') as f:
        return json.load(f)


def copy_file(export_root, hub, info, dry_run, report):
    src = os.path.join(export_root, info['zip_path'])
    dest = os.path.join(hub, info['hub_path'])
    if os.path.exists(dest):
        report['files_kept'] += 1
        return
    if not os.path.exists(src):
        report['files_missing'].append(info['zip_path'])
        return
    if not dry_run:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
    report['files_copied'] += 1


def selected(item, section, only):
    if not only:
        return True
    for want in only:
        w = want.strip('/').lower()
        if section['name'].lower() == w or item['folder'].lower() == w or item['folder'].lower().startswith(w + '/'):
            return True
    return False


def replace_database(export_root, hub, manifest, dry_run):
    target = os.path.join(hub, 'oasis.db')
    source = os.path.join(export_root, manifest['database'])
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    if os.path.exists(target):
        backup = f'{target}.before-restore-{stamp}'
        print(f'Saving the current database as {backup}')
        if not dry_run:
            src, dst = sqlite3.connect(target), sqlite3.connect(backup)
            src.backup(dst)
            src.close()
            dst.close()
    print(f'Putting {manifest["database"]} in place as {target}')
    if not dry_run:
        for extra in ('-wal', '-shm'):
            if os.path.exists(target + extra):
                os.remove(target + extra)        # stale write-ahead files would be replayed onto the restored copy
        shutil.copy2(source, target)


def merge(export_root, hub, manifest, only, include_settings, dry_run, report):
    conn = sqlite3.connect(os.path.join(hub, 'oasis.db'))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = {t: [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')] for t in tables}
    fks = {t: {fk[3]: fk[2] for fk in conn.execute(f'PRAGMA foreign_key_list("{t}")')} for t in tables}
    id_map, self_links, files = {}, [], []

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
    seen = set()
    for f in files:
        if f['name'] not in seen:
            seen.add(f['name'])
            copy_file(export_root, hub, manifest['uploads'][f['name']], dry_run, report)


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('hub', help='the Hub install folder (the one with oasis.db and static/), e.g. /home/occnj/occ_hub')
    ap.add_argument('--export', default=here, help='the export folder (default: the one this script is in)')
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
    manifest = load(args.export)
    report = {'added': [], 'skipped': [], 'files_copied': 0, 'files_kept': 0, 'files_missing': []}

    if args.replace_database:
        replace_database(args.export, hub, manifest, args.dry_run)
        for info in manifest['uploads'].values():
            copy_file(args.export, hub, info, args.dry_run, report)
    elif args.files:
        for info in manifest['uploads'].values():
            copy_file(args.export, hub, info, args.dry_run, report)
    else:
        if not os.path.exists(os.path.join(hub, 'oasis.db')):
            sys.exit('That Hub has no oasis.db yet. Start it once (so it creates its database), or use --replace-database.')
        merge(args.export, hub, manifest, args.only, args.include_settings, args.dry_run, report)

    print(('DRY RUN, nothing changed. ' if args.dry_run else '') + 'Summary:')
    for line in report['added']:
        print('  added   ', line)
    for line in report['skipped']:
        print('  skipped ', line)
    print(f"  files: {report['files_copied']} copied, {report['files_kept']} already there"
          + (f", {len(report['files_missing'])} missing from the export" if report['files_missing'] else ''))
    if not args.dry_run:
        print('Restart the Hub to see the changes: sudo systemctl restart oasis-hub')
    return report


if __name__ == '__main__':
    main()
