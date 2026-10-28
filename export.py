"""Download Everything (/admin/export): one ZIP with the database and every upload, sorted so
each item's text sits next to its own pictures and documents. The same page takes such a ZIP back
(Upload): it arrives in pieces, is checked and previewed, then either adds what this Hub is
missing or replaces the whole Hub (tools/restore_to_hub.py does the work, reading from the ZIP).

  START HERE.html        open in a browser: every section and item, its text and pictures
  <Section>/<NNN Title>/ text.txt plus the files that item uses (photos, PDFs, documents)
  Not used by anything/  uploads no item refers to
  Spreadsheets/          every table as a .csv (opens in Excel / Google Sheets)
  Database/oasis.db      a consistent snapshot of the whole database, for restoring the Hub

The ZIP is built in a background thread into exports/ (outside static/, so it is never public)
and survives until the next build or until an admin deletes it. Progress is kept in a small
JSON file so either gunicorn worker can show it.
"""
from core import *
import csv
import io
import sys
import threading

sys.path.insert(0, os.path.join(BASE_DIR, 'tools'))
import restore_to_hub  # noqa: E402

EXPORT_DIR = os.path.join(BASE_DIR, 'exports')
_STATUS = os.path.join(EXPORT_DIR, 'status.json')
_UPLOAD_RE = re.compile(r'uploads/([A-Za-z0-9._\-]+)')
_STALE_SECONDS = 180          # a build that stopped reporting (worker restarted) has failed

# Friendly section names; any other table is named from its table name.
SECTIONS = {
    'hub_cards': 'Hub Cards', 'sermon_notes': 'Sermon Notes', 'newsletters': 'Volunteer Newsletter',
    'events': 'Events', 'leaders': 'Leadership', 'behind_scenes': 'Oasis Crew - Teams',
    'behind_scene_people': 'Oasis Crew - People', 'missions': 'Mission', 'beyond_walls': 'Beyond the Walls',
    'beliefs': 'Beliefs', 'values_items': 'Values', 'ministries': 'Ministries', 'serve_categories': 'Serve',
    'submissions': 'Connect Cards', 'prayer_requests': 'Prayer Requests',
    'special_event_requests': 'Special Event Requests', 'app_feedback': 'App Feedback', 'settings': 'Site Settings',
}
# Only in the spreadsheets (no text or pictures of their own worth a folder).
SPREADSHEET_ONLY = {'admin_users', 'analytics', 'rate_events', 'banned_ips'}
# Never exported in readable form.
SECRET_COLUMNS = {('admin_users', 'password')}
_TITLE_COLUMNS = ('title', 'name', 'full_name', 'question', 'label', 'slug', 'subject', 'key')
_DATE_COLUMNS = ('note_date', 'event_date', 'issue_date', 'submitted_at', 'created_at')
# Where each kind of item is seen on the Hub ({id} is the item's id), for the import manifest.
ROUTES = {
    'sermon_notes': ('/sermon-notes/{id}', '/admin/sermon-notes/{id}/edit'),
    'newsletters': ('/newsletter/{id}', '/admin/newsletter/{id}/edit'),
    'missions': ('/mission/{id}', '/admin/missions/{id}/edit'),
    'beyond_walls': ('/beyond-the-walls/{id}', '/admin/beyond-the-walls/{id}/edit'),
    'behind_scenes': ('/behind-the-scene/{id}', '/admin/behind-the-scene/{id}/edit'),
    'hub_cards': ('/hub-cards/{id}', '/admin/hub-cards/{id}/edit'),
    'leaders': ('/leadership', '/admin/leaders/{id}/edit'),
    'beliefs': ('/beliefs', '/admin/beliefs/{id}/edit'),
    'values_items': ('/values', '/admin/values/{id}/edit'),
    'ministries': ('/ministries', '/admin/ministries/{id}/edit'),
    'events': ('/calendar', '/admin/events/{id}/edit'),
}
_STORED_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.pdf', '.docx', '.mp4', '.mov', '.zip'}


def _section_name(table):
    return SECTIONS.get(table) or table.replace('_', ' ').title()


def _safe(text, limit=60):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', str(text or '')).strip(' .')
    return re.sub(r'\s+', ' ', text)[:limit].strip() or 'Untitled'


def _plain(value):
    """Rich text (sermon notes, descriptions) as readable plain text."""
    text = str(value)
    if '<' in text and '>' in text:
        text = re.sub(r'(?i)<br\s*/?>|</(p|li|h[1-6]|div|blockquote)>', '\n', text)
        text = re.sub(r'(?i)<li[^>]*>', '• ', text)
        text = html.unescape(re.sub(r'<[^>]+>', '', text))
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def _label(col):
    return col.replace('_', ' ').capitalize()


def _item_title(table, row, cols):
    if 'first_name' in cols and (row['first_name'] or row.get('last_name')):
        return f"{row['first_name'] or ''} {row.get('last_name') or ''}".strip()
    for c in _TITLE_COLUMNS:
        if c in cols and row[c]:
            return _plain(row[c]).split('\n')[0]
    return ''


# ---- what goes where ------------------------------------------------------------------------

def plan_export(conn):
    """Sections -> items, each with its text fields and the upload names it uses. Child tables
    (photo galleries, team members, next steps) are folded into their parent item."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    columns = {t: [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')] for t in tables}
    parent_of = {}
    for t in tables:
        fks = [(fk[2], fk[3]) for fk in conn.execute(f'PRAGMA foreign_key_list("{t}")') if fk[2] != t]
        if len(fks) == 1 and t not in SPREADSHEET_ONLY:
            parent_of[t] = fks[0]          # (parent table, column in this table)

    def rows(t):
        order = 'sort_order, rowid' if 'sort_order' in columns[t] else 'rowid'
        return [dict(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY {order}')]

    def fields(t, row):
        out = []
        for c in columns[t]:
            v = row[c]
            if c == 'id' or (t, c) in SECRET_COLUMNS or v is None or v == '':
                continue
            if t in parent_of and c == parent_of[t][1]:
                continue
            if isinstance(v, str) and v[:1] in '[{':
                continue                   # internal data (e.g. rendered page lists); its files are listed anyway
            out.append((_label(c), _plain(_UPLOAD_RE.sub(r'\1', v) if isinstance(v, str) else v)))
        return out

    def uploads_in(t, row):
        names = []
        for c in columns[t]:
            if (t, c) not in SECRET_COLUMNS and isinstance(row[c], str):
                names += [n for n in _UPLOAD_RE.findall(row[c]) if n not in names]
        return names

    def file_refs(t, row):
        """[{name, column}] — which field of the record points at each file."""
        refs = []
        for c in columns[t]:
            if (t, c) not in SECRET_COLUMNS and isinstance(row[c], str):
                refs += [{'name': n, 'column': c} for n in _UPLOAD_RE.findall(row[c])]
        return refs

    def record(t, row):
        return {c: row[c] for c in columns[t] if (t, c) not in SECRET_COLUMNS}

    sections = []
    items_by_key = {}
    for t in tables:
        if t in SPREADSHEET_ONLY or t in parent_of:
            continue
        items = []
        if 'id' not in columns[t]:                 # e.g. settings: one item for the whole table
            all_rows = rows(t)
            item = {'title': _section_name(t), 'fields': [], 'files': [], 'children': [],
                    'table': t, 'rows': [], 'file_refs': []}
            for r in all_rows:
                key_col = columns[t][0]
                rest = [_plain(r[c]) for c in columns[t][1:] if r[c] not in (None, '')]
                if rest:
                    item['fields'].append((str(r[key_col]), ' · '.join(rest)))
                item['files'] += [n for n in uploads_in(t, r) if n not in item['files']]
                item['rows'].append(record(t, r))
                item['file_refs'] += file_refs(t, r)
            if item['fields'] or item['files']:
                items.append(item)
        else:
            for r in rows(t):
                title = _item_title(t, r, columns[t])
                date_ = next((str(r[c])[:10] for c in _DATE_COLUMNS if c in columns[t] and r[c]), '')
                item = {'title': title or f'#{r["id"]}', 'date': date_, 'id': r['id'],
                        'fields': fields(t, r), 'files': uploads_in(t, r), 'children': [],
                        'table': t, 'rows': [record(t, r)], 'file_refs': file_refs(t, r)}
                items.append(item)
                items_by_key[(t, r['id'])] = item
        if items:
            sections.append({'table': t, 'name': _section_name(t), 'items': items})

    for t, (parent, col) in parent_of.items():
        for r in rows(t):
            parent_item = items_by_key.get((parent, r[col]))
            if not parent_item:
                continue
            title = _item_title(t, r, columns[t])
            parent_item['children'].append({'kind': _section_name(t), 'title': title,
                                            'fields': fields(t, r), 'files': uploads_in(t, r),
                                            'table': t, 'parent_column': col, 'row': record(t, r),
                                            'file_refs': file_refs(t, r)})

    for s in sections:
        for n, item in enumerate(s['items'], 1):
            label = f"{item['date']} {item['title']}" if item.get('date') else item['title']
            item['folder'] = f"{s['name']}/{n:03d} {_safe(label)}"
    sections.sort(key=lambda s: list(SECTIONS).index(s['table']) if s['table'] in SECTIONS else 99)
    return sections, tables, columns


# ---- building the ZIP -----------------------------------------------------------------------

def _write_status(**status):
    status['updated'] = time.time()
    tmp = _STATUS + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(status, f)
    os.replace(tmp, _STATUS)


def export_status():
    try:
        with open(_STATUS) as f:
            status = json.load(f)
    except (OSError, ValueError):
        return {'state': 'none'}
    if status.get('state') == 'building' and time.time() - status.get('updated', 0) > _STALE_SECONDS:
        status = {'state': 'failed', 'error': 'The server restarted while the download was being prepared. Please try again.'}
    if status.get('state') == 'ready' and not os.path.exists(os.path.join(EXPORT_DIR, status.get('file', ''))):
        return {'state': 'none'}
    return status


def _uploads_size():
    total = 0
    if os.path.isdir(UPLOAD_FOLDER):
        for name in os.listdir(UPLOAD_FOLDER):
            try:
                total += os.path.getsize(os.path.join(UPLOAD_FOLDER, name))
            except OSError:
                pass
    return total


def space_problem():
    """None, or why there is not enough room on the server for the ZIP."""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    need = _uploads_size() + 2 * os.path.getsize(DB) + 50 * 1024 * 1024
    free = shutil.disk_usage(EXPORT_DIR).free
    if free < need:
        return (f'Not enough free space on the server: the download needs about {need // (1024 * 1024)} MB '
                f'and only {free // (1024 * 1024)} MB is free.')
    return None


def _item_text(item):
    lines = [item['title'], '=' * min(len(item['title']), 60), '']
    for label, value in item['fields']:
        lines += [f'{label}:', value, ''] if '\n' in value or len(value) > 70 else [f'{label}: {value}']
    for child in item['children']:
        lines += ['', f"--- {child['kind']}{': ' + child['title'] if child['title'] else ''} ---"]
        lines += [f'{label}: {value}' for label, value in child['fields']]
    files = item['files'] + [f for c in item['children'] for f in c['files']]
    if files:
        lines += ['', 'Files in this folder:'] + [f'  {f}' for f in files]
    return '\n'.join(lines).strip() + '\n'


def _item_manifest(item, uploads):
    """Everything needed to add this item to another Hub: its database record(s), which field
    points at which file, where that file goes, and the item's pages."""
    def files(refs):
        return [{'name': r['name'], 'column': r['column'], 'zip_path': uploads.get(r['name'], {}).get('zip_path'),
                 'hub_path': f"static/uploads/{r['name']}", 'db_value': f"uploads/{r['name']}"} for r in refs]
    public, admin_ = ROUTES.get(item['table'], (None, None))
    has_id = item.get('id') is not None
    return {
        'title': item['title'], 'table': item['table'], 'folder': item['folder'], 'id': item.get('id'),
        'public_route': public.format(id=item['id']) if public and has_id else None,
        'public_route_pattern': public if public and has_id else None,
        'admin_route': admin_.format(id=item['id']) if admin_ and has_id else None,
        'records': item['rows'], 'files': files(item['file_refs']),
        'children': [{'table': c['table'], 'parent_column': c['parent_column'], 'record': c['row'],
                      'files': files(c['file_refs'])} for c in item['children']],
    }


_HOW_TO_IMPORT = """# How to put this export into a Hub

This folder is a complete copy of an Oasis Hub (occnj/occhub), made by Admin -> Download Everything.
It is written so a person *or Claude Code* can restore it, fully or item by item.

## What is where

| Path | What it is |
|---|---|
| `START HERE.html` | Human view: every item with its text and pictures. |
| `manifest.json` | Machine view: every item, its database record(s), its files and its routes. |
| `<Section>/<NNN Title>/item.json` | The same entry for one item. |
| `<Section>/<NNN Title>/` | That item's `text.txt` and its own files (pictures, PDFs, documents). |
| `Not used by anything/` | Uploads no record refers to. |
| `Database/oasis.db` | The whole database (SQLite), a consistent snapshot. |
| `Spreadsheets/*.csv` | Every table as CSV (passwords left out). |
| `Restore/restore_to_hub.py` | Restore tool, Python 3 standard library only. |

## How records and files fit together

* Each `item.json` has `table`, `id`, `records` (the row(s) exactly as stored, minus password hashes),
  `children` (rows of child tables such as gallery photos or Next Steps, with `parent_column` naming
  the column that holds the parent's id), and `files`.
* Each file entry has `name`, `column` (the field that refers to it), `zip_path` (where it is in this
  export), `hub_path` (where it goes in a Hub install: `static/uploads/<name>`) and `db_value` (the text
  stored in the record: `uploads/<name>`). Names are unique, so copying a file to its `hub_path` makes
  the record's existing value correct with no rewriting.
* `public_route` / `admin_route` are the item's pages on the Hub (`public_route_pattern` with `{id}` for
  the id it gets on a new Hub). Site settings (`settings` table) are one item with many `records`.
* `manifest.json` -> `uploads` maps every file name to its `zip_path`, `hub_path` and size;
  `hub_schema_version` is the Hub's `PRAGMA user_version` at export time.

## Restoring

Easiest: on the Hub, Admin -> Download & Upload -> **Upload a download**, choose this ZIP, check the
preview, then **Add what is missing** (per section) or **Replace this whole Hub**.

Or run on the Hub's machine (`--export` takes the .zip itself or this unzipped folder) (the folder with `oasis.db` and `static/`), then restart the Hub
(`sudo systemctl restart oasis-hub`):

* Whole Hub (new install, or replace everything; stop the Hub first; the old database is kept as
  `oasis.db.before-restore-<time>`):
  `python3 "Restore/restore_to_hub.py" /home/occnj/occ_hub --replace-database`
* Add items to a Hub that already has content (new ids, nothing existing changed, files never overwritten):
  `python3 "Restore/restore_to_hub.py" /home/occnj/occ_hub --merge --dry-run` first, then without `--dry-run`.
  Narrow it with `--only "Sermon Notes"` or `--only "<Section>/<NNN Title>"` (repeatable);
  add `--include-settings` to also copy site settings the Hub does not have yet.
* Only pictures and documents: `--files`.

If the target Hub is a newer version, start it once first so it creates/updates its tables, then merge;
columns the target does not have are ignored. Doing it by hand: insert each record without its `id`,
set child rows' `parent_column` to the new id, and copy each file to its `hub_path`.
"""


_IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def _start_here_html(sections, file_paths, unused, stamp):
    e = html.escape
    out = [f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Oasis Hub export {e(stamp)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:20px;color:#1d1d1f;background:#f5f5f7}}
h1{{font-size:1.6rem}}h2{{margin-top:40px;color:#13677A;border-bottom:2px solid #13677A;padding-bottom:6px}}
nav a{{display:inline-block;margin:0 10px 6px 0;color:#13677A}}
.item{{background:#fff;border-radius:14px;padding:16px 18px;margin:14px 0;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.item h3{{margin:0 0 4px}}.folder{{font-size:.8rem;color:#6e6e73;margin-bottom:10px}}
dl{{display:grid;grid-template-columns:minmax(110px,max-content) 1fr;gap:4px 14px;margin:0;font-size:.9rem}}
dt{{color:#6e6e73}}dd{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}}
.child{{border-left:3px solid #e5e5ea;padding-left:12px;margin-top:12px}}.child b{{font-size:.85rem;color:#F2541B}}
.files{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}.files a{{font-size:.85rem;color:#13677A}}
.files img{{height:110px;border-radius:8px;display:block;background:#eee}}</style></head><body>
<h1>Oasis Hub export &middot; {e(stamp)}</h1>
<p>Every item with its text and the pictures or documents attached to it. Each item also has its own
folder (shown under its title) holding a <b>text.txt</b> and those same files.
The full database is in <b>Database/oasis.db</b>; every table is also in <b>Spreadsheets/</b>.</p><nav>''']
    out += [f'<a href="#s{i}">{e(s["name"])} ({len(s["items"])})</a>' for i, s in enumerate(sections)]
    if unused:
        out.append(f'<a href="#unused">Not used by anything ({len(unused)})</a>')
    out.append('</nav>')

    def files_html(names):
        parts = []
        for n in names:
            path = file_paths.get(n)
            if not path:
                parts.append(f'<span style="color:#c0392b;font-size:.85rem">{e(n)} (missing on the server)</span>')
            elif os.path.splitext(n)[1].lower() in _IMAGE_EXT:
                parts.append(f'<a href="{e(path)}"><img src="{e(path)}" alt="{e(n)}" loading="lazy"></a>')
            else:
                parts.append(f'<a href="{e(path)}">{e(n)}</a>')
        return f'<div class="files">{"".join(parts)}</div>' if parts else ''

    def fields_html(fields):
        return '<dl>' + ''.join(f'<dt>{e(l)}</dt><dd>{e(v)}</dd>' for l, v in fields) + '</dl>' if fields else ''

    for i, s in enumerate(sections):
        out.append(f'<h2 id="s{i}">{e(s["name"])}</h2>')
        for item in s['items']:
            out.append(f'<div class="item"><h3>{e(item["title"])}</h3><div class="folder">{e(item["folder"])}/</div>')
            out.append(fields_html(item['fields']) + files_html(item['files']))
            for c in item['children']:
                out.append(f'<div class="child"><b>{e(c["kind"])}</b>{fields_html(c["fields"])}{files_html(c["files"])}</div>')
            out.append('</div>')
    if unused:
        out.append('<h2 id="unused">Not used by anything</h2><p>Uploads no item refers to any more.</p>')
        out.append(files_html(unused))
    out.append('</body></html>')
    return '\n'.join(out)


def build_export(stamp=None):
    """Build exports/oasis-hub-export-<stamp>.zip. Returns its file name."""
    stamp = stamp or datetime.now().strftime('%Y-%m-%d')
    os.makedirs(EXPORT_DIR, exist_ok=True)
    root = f'Oasis Hub export {stamp}'
    name = f'oasis-hub-export-{stamp}.zip'
    part = os.path.join(EXPORT_DIR, name + '.part')
    snapshot = os.path.join(EXPORT_DIR, 'oasis-snapshot.db')

    src = sqlite3.connect(DB)
    dst = sqlite3.connect(snapshot)
    src.backup(dst)
    src.close()
    dst.close()
    conn = sqlite3.connect(snapshot)
    conn.row_factory = sqlite3.Row
    try:
        sections, tables, columns = plan_export(conn)
        on_disk = set(os.listdir(UPLOAD_FOLDER)) if os.path.isdir(UPLOAD_FOLDER) else set()
        file_paths, placements = {}, []        # upload name -> path in the ZIP (stored once)
        for s in sections:
            for item in s['items']:
                for n in item['files'] + [f for c in item['children'] for f in c['files']]:
                    if n in on_disk and n not in file_paths:
                        file_paths[n] = f"{item['folder']}/{n}"
                        placements.append((n, file_paths[n]))
        unused = sorted(on_disk - set(file_paths))
        for n in unused:
            file_paths[n] = f'Not used by anything/{n}'
            placements.append((n, file_paths[n]))

        total = len(placements) + len(tables) + 2
        done = 0
        _write_status(state='building', done=0, total=total, file=name)
        with zipfile.ZipFile(part, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr(f'{root}/START HERE.html', _start_here_html(sections, file_paths, unused, stamp))
            uploads = {n: {'zip_path': path, 'hub_path': f'static/uploads/{n}',
                           'bytes': os.path.getsize(os.path.join(UPLOAD_FOLDER, n))} for n, path in placements}
            manifest = {
                'format': 'oasis-hub-export', 'version': 1, 'exported_at': datetime.now().isoformat(timespec='seconds'),
                'hub_schema_version': conn.execute('PRAGMA user_version').fetchone()[0],
                'guide': 'HOW TO IMPORT.md', 'database': 'Database/oasis.db', 'restore_script': 'Restore/restore_to_hub.py',
                'uploads': uploads, 'unused_uploads': unused,
                'sections': [{'name': s['name'], 'table': s['table'],
                              'items': [_item_manifest(i, uploads) for i in s['items']]} for s in sections],
            }
            zf.writestr(f'{root}/manifest.json', json.dumps(manifest, indent=1, ensure_ascii=False, default=str))
            zf.writestr(f'{root}/HOW TO IMPORT.md', _HOW_TO_IMPORT)
            zf.write(os.path.join(BASE_DIR, 'tools', 'restore_to_hub.py'), f'{root}/Restore/restore_to_hub.py')
            for s, ms in zip(sections, manifest['sections']):
                for item, mi in zip(s['items'], ms['items']):
                    zf.writestr(f"{root}/{item['folder']}/text.txt", _item_text(item))
                    zf.writestr(f"{root}/{item['folder']}/item.json",
                                json.dumps(mi, indent=1, ensure_ascii=False, default=str))
            for n, path in placements:
                compress = zipfile.ZIP_STORED if os.path.splitext(n)[1].lower() in _STORED_EXT else zipfile.ZIP_DEFLATED
                zf.write(os.path.join(UPLOAD_FOLDER, n), f'{root}/{path}', compress_type=compress)
                done += 1
                if done % 20 == 0:
                    _write_status(state='building', done=done, total=total, file=name)
            for t in tables:
                buf = io.StringIO()
                w = csv.writer(buf)
                cols = [c for c in columns[t] if (t, c) not in SECRET_COLUMNS]
                w.writerow(cols)
                for r in conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"'):
                    w.writerow(['' if v is None else v for v in r])
                zf.writestr(f'{root}/Spreadsheets/{_section_name(t)}.csv', '﻿' + buf.getvalue())
                done += 1
            zf.write(snapshot, f'{root}/Database/oasis.db')
    finally:
        conn.close()
        try:
            os.remove(snapshot)
        except OSError:
            pass
    for old in os.listdir(EXPORT_DIR):              # keep only the newest export
        if old.startswith('oasis-hub-export-') and old.endswith('.zip') and old != name:
            os.remove(os.path.join(EXPORT_DIR, old))
    os.replace(part, os.path.join(EXPORT_DIR, name))
    _write_status(state='ready', file=name, size=os.path.getsize(os.path.join(EXPORT_DIR, name)),
                  built=datetime.now().strftime('%b %-d, %Y %-I:%M %p'))
    return name


def _build_in_background(user):
    try:
        name = build_export()
        app.logger.info(f'export: {user} built {name}')
    except Exception as e:
        app.logger.error(f'export failed: {e}')
        _write_status(state='failed', error='Something went wrong while preparing the download. The details are in error.log.')
        for f in os.listdir(EXPORT_DIR):
            if f.endswith('.part'):
                os.remove(os.path.join(EXPORT_DIR, f))


# ---- admin pages ----------------------------------------------------------------------------

@app.route('/admin/export')
@superadmin_required
def admin_export():
    return render_template('admin/export.html', status=export_status(), imp=import_status(),
                           uploads_bytes=_uploads_size(), db_bytes=os.path.getsize(DB))


@app.route('/admin/export/status')
@superadmin_required
def admin_export_status():
    return export_status()


@app.route('/admin/export/build', methods=['POST'])
@superadmin_required
def admin_export_build():
    if export_status().get('state') == 'building':
        return redirect(url_for('admin_export'))
    problem = space_problem()
    if problem:
        flash(problem, 'info')
        return redirect(url_for('admin_export'))
    _write_status(state='building', done=0, total=0)
    threading.Thread(target=_build_in_background, args=(session.get('admin_user'),), daemon=True).start()
    return redirect(url_for('admin_export'))


@app.route('/admin/export/download')
@superadmin_required
def admin_export_download():
    status = export_status()
    if status.get('state') != 'ready':
        return redirect(url_for('admin_export'))
    app.logger.info(f"export: {session.get('admin_user')} downloaded {status['file']}")
    return send_from_directory(EXPORT_DIR, status['file'], as_attachment=True, conditional=True)


@app.route('/admin/export/delete', methods=['POST'])
@superadmin_required
def admin_export_delete():
    if export_status().get('state') != 'building' and os.path.isdir(EXPORT_DIR):
        for f in os.listdir(EXPORT_DIR):
            if f.startswith(('oasis-hub-export-', 'status.json')):
                os.remove(os.path.join(EXPORT_DIR, f))
        flash('The download was deleted from the server.', 'info')
    return redirect(url_for('admin_export'))


# ---- Upload: putting a download back -------------------------------------------------------

_IMPORT_STATUS_NAME = 'import.json'
_IMPORT_ZIP_NAME = 'upload.zip'
CHUNK_MAX = 16 * 1024 * 1024


def _import_paths():
    return (os.path.join(EXPORT_DIR, _IMPORT_STATUS_NAME), os.path.join(EXPORT_DIR, _IMPORT_ZIP_NAME))


def _write_import_status(**status):
    path, _ = _import_paths()
    os.makedirs(EXPORT_DIR, exist_ok=True)
    status['updated'] = time.time()
    with open(path + '.tmp', 'w') as f:
        json.dump(status, f, default=str)
    os.replace(path + '.tmp', path)


def import_status():
    path, zip_path = _import_paths()
    try:
        with open(path) as f:
            status = json.load(f)
    except (OSError, ValueError):
        status = {'state': 'none'}
    if status.get('state') == 'running' and time.time() - status.get('updated', 0) > _STALE_SECONDS:
        status = dict(status, state='failed', error='The server restarted during the upload. Nothing after that point was saved; you can run it again.')
    if status.get('state') in ('uploaded', 'running') and not os.path.exists(zip_path):
        return {'state': 'none'}
    return status


def _preview(zip_path):
    """What the ZIP holds and, per section, how much of it this Hub already has."""
    export = restore_to_hub.Export(zip_path)
    m = export.manifest
    dry = restore_to_hub.merge(export, DB, UPLOAD_FOLDER, dry_run=True)
    sections = []
    for sec in m['sections']:
        if all(i.get('id') is None for i in sec['items']):
            continue                               # site settings: the separate "also add settings" box
        prefix = sec['name'] + '/'
        sections.append({'name': sec['name'], 'count': len(sec['items']),
                         'new': sum(1 for a in dry['added'] if a.startswith(prefix)),
                         'already': sum(1 for a in dry['already'] if a.startswith(prefix))})
    return {'exported_at': m.get('exported_at', ''), 'hub_schema_version': m.get('hub_schema_version'),
            'sections': sections, 'files': len(m['uploads']),
            'files_new': dry['files_copied'], 'bytes': sum(u.get('bytes', 0) for u in m['uploads'].values()),
            'has_database': export.exists(m['database'])}


@app.route('/admin/import/status')
@superadmin_required
def admin_import_status():
    return import_status()


@app.route('/admin/import/chunk', methods=['POST'])
@superadmin_required
def admin_import_chunk():
    """One piece of the uploaded ZIP. Pieces must arrive in order; a 409 tells the page where to
    carry on (so a dropped connection resumes instead of starting over)."""
    _, zip_path = _import_paths()
    part = zip_path + '.part'
    try:
        offset, total = int(request.form.get('offset', -1)), int(request.form.get('total', 0))
    except ValueError:
        return {'error': 'Bad upload.'}, 400
    chunk = request.files.get('chunk')
    if not chunk or total <= 0 or offset < 0:
        return {'error': 'Bad upload.'}, 400
    if offset == 0:
        if import_status().get('state') == 'running':
            return {'error': 'An upload is being applied right now. Wait for it to finish.'}, 409
        os.makedirs(EXPORT_DIR, exist_ok=True)
        need = 2 * total + 50 * 1024 * 1024        # the ZIP, plus the files it adds
        free = shutil.disk_usage(EXPORT_DIR).free
        if free < need:
            return {'error': f'Not enough free space on the server: about {need // (1024 * 1024)} MB is needed '
                             f'and {free // (1024 * 1024)} MB is free.'}, 507
        for p in (zip_path, part):
            if os.path.exists(p):
                os.remove(p)
        open(part, 'wb').close()
        _write_import_status(state='uploading', total=total)
    size = os.path.getsize(part) if os.path.exists(part) else 0
    if size != offset:
        return {'error': 'resume', 'offset': size}, 409
    data = chunk.read(CHUNK_MAX + 1)
    if len(data) > CHUNK_MAX or size + len(data) > total:
        return {'error': 'Bad upload.'}, 400
    with open(part, 'ab') as f:
        f.write(data)
    size += len(data)
    if size < total:
        return {'offset': size}
    os.replace(part, zip_path)
    try:
        summary = _preview(zip_path)
    except Exception as e:
        os.remove(zip_path)
        msg = str(e) if isinstance(e, ValueError) else 'That file could not be read as an Oasis Hub download.'
        app.logger.warning(f'import: rejected upload: {e}')
        _write_import_status(state='failed', error=msg)
        return {'error': msg}, 400
    _write_import_status(state='uploaded', summary=summary, name=request.form.get('name', '')[:200])
    return {'done': True}


def _run_import(mode, sections, include_settings, user):
    _, zip_path = _import_paths()
    status = import_status()
    done = threading.Event()

    def heartbeat():
        while not done.wait(30):
            _write_import_status(**dict(import_status(), state='running'))
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        export = restore_to_hub.Export(zip_path)
        report = restore_to_hub.new_report()
        saved = None
        if mode == 'replace':
            saved = restore_to_hub.replace_database(export, DB)
            restore_to_hub.copy_all_files(export, UPLOAD_FOLDER, False, report)
            from schema import init_db
            init_db()                              # bring an older download's tables up to date
        else:
            restore_to_hub.merge(export, DB, UPLOAD_FOLDER, sections, include_settings, False, report)
        app.logger.info(f'import: {user} applied an upload ({mode}): {len(report["added"])} added, '
                        f'{report["files_copied"]} files')
        done.set()
        _write_import_status(state='done', mode=mode, summary=status.get('summary'),
                             saved=os.path.basename(saved) if saved else '',
                             added=report['added'][:500], added_count=len(report['added']),
                             already_count=len(report['already']), skipped=report['skipped'][:200],
                             files_copied=report['files_copied'], files_kept=report['files_kept'],
                             files_missing=len(report['files_missing']))
    except Exception as e:
        done.set()
        app.logger.error(f'import failed: {e}')
        _write_import_status(state='failed', error='Something went wrong while applying the upload. The details are in error.log.')


@app.route('/admin/import/run', methods=['POST'])
@superadmin_required
def admin_import_run():
    if import_status().get('state') != 'uploaded':
        return redirect(url_for('admin_export') + '#upload')
    mode = 'replace' if request.form.get('mode') == 'replace' else 'merge'
    if mode == 'replace' and request.form.get('confirm', '').strip().upper() != 'REPLACE':
        flash('To replace the whole Hub, type REPLACE in the box.', 'info')
        return redirect(url_for('admin_export') + '#upload')
    sections = request.form.getlist('sections')
    if mode == 'merge' and not sections:
        flash('Tick at least one section to add.', 'info')
        return redirect(url_for('admin_export') + '#upload')
    _write_import_status(**dict(import_status(), state='running', mode=mode))
    threading.Thread(target=_run_import, args=(mode, sections, bool(request.form.get('include_settings')),
                                               session.get('admin_user')), daemon=True).start()
    return redirect(url_for('admin_export') + '#upload')


@app.route('/admin/import/discard', methods=['POST'])
@superadmin_required
def admin_import_discard():
    if import_status().get('state') != 'running':
        status_path, zip_path = _import_paths()
        for p in (zip_path, zip_path + '.part', status_path):
            if os.path.exists(p):
                os.remove(p)
    return redirect(url_for('admin_export') + '#upload')
