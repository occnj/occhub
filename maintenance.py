"""Housekeeping that used to happen as a side effect of page loads, or not at all.

  * purge_past_events()      delete finished one-time events marked "auto delete"
  * scan_uploads()           which files in static/uploads are still referenced by the database
  * delete_unused_uploads()  remove the ones that are not

Run it by hand or from a timer:   python maintenance.py [--clean-uploads] [--dry-run]
The admin "Storage" page (superadmin) uses the same functions.
"""
from core import *

# A file younger than this is never reported as unused: an admin may have just uploaded it
# in a form they have not saved yet.
UPLOAD_GRACE_SECONDS = 24 * 3600
_UPLOAD_REF_RE = re.compile(r'uploads/([A-Za-z0-9._\-]+)')
_SKIP_TABLES = {'analytics', 'rate_events', 'banned_ips'}   # large, and never hold upload paths


def purge_past_events(conn=None):
    """Remove past one-time events the admin marked auto-delete. Returns how many."""
    own = conn is None
    conn = conn or get_db()
    try:
        cur = conn.execute(
            "DELETE FROM events WHERE auto_delete=1 AND recurrence='none' AND event_date < ?",
            (date.today().isoformat(),))
        conn.commit()
        return cur.rowcount
    finally:
        if own:
            conn.close()


def referenced_uploads(conn):
    """Every filename the database mentions as `uploads/<name>`, in ANY text column of ANY table.
    Scanning generically means a new table or column can never be forgotten here."""
    names = set()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if table in _SKIP_TABLES:
            continue
        for col in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
            if (col[2] or '').upper() not in ('TEXT', ''):
                continue
            for (value,) in conn.execute(
                    f'SELECT "{col[1]}" FROM "{table}" WHERE "{col[1]}" LIKE \'%uploads/%\''):
                if isinstance(value, str):
                    names.update(_UPLOAD_REF_RE.findall(value))
    return names


def scan_uploads(conn=None, now=None):
    own = conn is None
    conn = conn or get_db()
    try:
        used_names = referenced_uploads(conn)
    finally:
        if own:
            conn.close()
    now = now or time.time()
    used, unused, recent = [], [], []
    if os.path.isdir(UPLOAD_FOLDER):
        for name in sorted(os.listdir(UPLOAD_FOLDER)):
            path = os.path.join(UPLOAD_FOLDER, name)
            if name.startswith('.') or not os.path.isfile(path):
                continue
            st = os.stat(path)
            if name in used_names:
                used.append((name, st.st_size))
            elif now - st.st_mtime < UPLOAD_GRACE_SECONDS:
                recent.append((name, st.st_size))
            else:
                unused.append((name, st.st_size))
    present = {n for n, _ in used} | {n for n, _ in unused} | {n for n, _ in recent}
    return {
        'used': used, 'unused': unused, 'recent': recent,
        'missing': sorted(used_names - present),   # referenced by the database but not on disk
        'used_bytes': sum(s for _, s in used),
        'unused_bytes': sum(s for _, s in unused),
        'referenced': len(used_names),
    }


def delete_unused_uploads(force=False, dry_run=False):
    """Returns (files_removed, bytes_freed, refusal_reason_or_None)."""
    info = scan_uploads()
    # If the database mentions no uploads at all but files exist, we are almost certainly looking
    # at the wrong/empty database. Deleting "everything unused" then would destroy real content.
    if info['referenced'] == 0 and (info['unused'] or info['recent']) and not force:
        return 0, 0, 'The database references no uploads at all; refusing to delete (wrong database?).'
    removed = freed = 0
    for name, size in info['unused']:
        if not dry_run:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, name))
            except OSError as e:
                app.logger.warning(f"delete_unused_uploads: {name}: {e}")
                continue
        removed += 1
        freed += size
    return removed, freed, None


def run_maintenance(clean_uploads=False, dry_run=False):
    out = {'events_purged': 0 if dry_run else purge_past_events()}
    if clean_uploads:
        removed, freed, why = delete_unused_uploads(dry_run=dry_run)
        out.update(uploads_removed=removed, bytes_freed=freed, refused=why)
    return out


def human_size(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


app.jinja_env.filters['human_size'] = human_size

__all__ = [n for n in dir() if not n.startswith('__')]

if __name__ == '__main__':
    import argparse
    from schema import init_db
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--clean-uploads', action='store_true', help='delete uploads no database row refers to')
    ap.add_argument('--dry-run', action='store_true', help='report only, change nothing')
    args = ap.parse_args()
    init_db()
    print(json.dumps(run_maintenance(args.clean_uploads, args.dry_run), indent=2))
