#!/usr/bin/env python3
"""OccHub backup / migration tool (Python 3 standard library only, plus rsync and gpg).

  push      Pi:      snapshot the database, encrypt it, and sync database + photos to the droplet
  status    Pi:      show local snapshots and what the droplet holds
  verify    droplet: prove the newest backup is intact and complete (--quick is safe for cron)
  restore   droplet: put the newest (or a named) backup into an OccHub install
  prune     droplet: keep only the newest N database snapshots

What goes where
  db/oasis-<UTC time>.db.gz.gpg   one consistent, compressed, encrypted database snapshot per run
  db/oasis-<UTC time>.json        manifest: sha256, sizes, row counts. Written LAST, so a manifest
                                  only exists once its snapshot has fully arrived.
  uploads/                        every photo and sermon document, mirrored incrementally. Upload
                                  names are random and never reused, so nothing is ever overwritten.

Why the database is snapshotted with SQLite's backup API instead of being copied: the live file
is in WAL mode and is being written to; a plain copy can be torn or miss committed data.

Configuration is read from KEY=VALUE lines in <app dir>/.backup.env (override with --env-file);
real environment variables win. See docs/BACKUP_AND_MIGRATION.md for every key.
"""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

APP_DIR_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_RE = re.compile(r'^(oasis-\d{8}-\d{6})\.db\.gz(\.gpg)?$')
TS_FMT = '%Y%m%d-%H%M%S'


class BackupError(Exception):
    pass


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ── configuration ───────────────────────────────────────────────────────────

def parse_env_file(path):
    values = {}
    if not path or not os.path.isfile(path):
        return values
    for raw in open(path, encoding='utf-8'):
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in '"\'':
            val = val[1:-1]
        values[key.strip()] = val
    return values


class Config:
    def __init__(self, args, environ=None):
        environ = os.environ if environ is None else environ
        self.app_dir = os.path.abspath(args.app_dir or environ.get('APP_DIR') or APP_DIR_DEFAULT)
        env_file = args.env_file or os.path.join(self.app_dir, '.backup.env')
        merged = parse_env_file(env_file)
        merged.update({k: v for k, v in environ.items() if k.startswith(('BACKUP_', 'OCC_', 'KEEP_', 'APP_'))})
        self.raw = merged
        get = merged.get
        self.db_path = get('OCC_DB_PATH') or os.path.join(self.app_dir, 'oasis.db')
        self.uploads = os.path.join(self.app_dir, 'static', 'uploads')
        self.backup_dir = get('BACKUP_DIR') or os.path.join(self.app_dir, 'backups')
        self.remote = get('BACKUP_REMOTE', '')
        self.ssh_key = get('BACKUP_SSH_KEY', '')
        self.ssh_port = get('BACKUP_SSH_PORT', '')
        self.passphrase_file = get('BACKUP_PASSPHRASE_FILE', '')
        self.allow_unencrypted = get('BACKUP_ALLOW_UNENCRYPTED', '') == '1'
        self.bwlimit = get('BACKUP_BWLIMIT_KBPS', '')
        self.keep_local = int(get('KEEP_LOCAL', '7') or 7)
        self.sync_delete = get('BACKUP_SYNC_DELETE', '') == '1'
        self.ping_url = get('BACKUP_PING_URL', '').rstrip('/')

    def check_passphrase(self):
        if not self.passphrase_file:
            return None
        st = os.stat(self.passphrase_file) if os.path.exists(self.passphrase_file) else None
        if st is None:
            raise BackupError(f'BACKUP_PASSPHRASE_FILE not found: {self.passphrase_file}')
        if st.st_mode & 0o077:
            raise BackupError(f'{self.passphrase_file} is readable by other users; run: chmod 600 {self.passphrase_file}')
        if st.st_size == 0:
            raise BackupError(f'{self.passphrase_file} is empty')
        return self.passphrase_file


# ── building blocks ─────────────────────────────────────────────────────────

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def open_readonly(path):
    return sqlite3.connect('file:' + urllib.parse.quote(path) + '?mode=ro', uri=True)


def row_counts(path):
    conn = open_readonly(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def check_integrity(path):
    conn = open_readonly(path)
    try:
        result = [r[0] for r in conn.execute('PRAGMA integrity_check')]
    finally:
        conn.close()
    if result != ['ok']:
        raise BackupError(f'database integrity check failed: {result[:3]}')


def snapshot_database(src, dest):
    """Consistent copy of a live (WAL) SQLite database using the online backup API."""
    if not os.path.isfile(src):
        raise BackupError(f'database not found: {src}')
    source = sqlite3.connect(src, timeout=30)    # a normal connection: the backup API only reads from it
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')   # single self-contained file
        target.commit()
    finally:
        target.close()
        source.close()
    check_integrity(dest)


def gzip_file(src, dest):
    with open(src, 'rb') as fi, gzip.open(dest, 'wb', compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)


def gunzip_file(src, dest):
    with gzip.open(src, 'rb') as fi, open(dest, 'wb') as fo:
        shutil.copyfileobj(fi, fo)


def _gpg_base(passphrase_file):
    return ['gpg', '--batch', '--yes', '--quiet', '--pinentry-mode', 'loopback',
            '--passphrase-file', passphrase_file]


def gpg_encrypt(src, dest, passphrase_file):
    r = subprocess.run(_gpg_base(passphrase_file) + ['--symmetric', '--cipher-algo', 'AES256', '-o', dest, src],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise BackupError(f'gpg encrypt failed: {r.stderr.strip()[-300:]}')


def gpg_decrypt(src, dest, passphrase_file):
    r = subprocess.run(_gpg_base(passphrase_file) + ['-o', dest, '-d', src], capture_output=True, text=True)
    if r.returncode != 0:
        raise BackupError(f'gpg decrypt failed (wrong passphrase or damaged file): {r.stderr.strip()[-300:]}')


def referenced_uploads(conn):
    names = set()
    ref = re.compile(r'uploads/([A-Za-z0-9._\-]+)')
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if table in ('analytics', 'rate_events', 'banned_ips'):
            continue
        for col in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
            if (col[2] or '').upper() not in ('TEXT', ''):
                continue
            for (value,) in conn.execute(f'SELECT "{col[1]}" FROM "{table}" WHERE "{col[1]}" LIKE \'%uploads/%\''):
                if isinstance(value, str):
                    names.update(ref.findall(value))
    return names


def list_snapshots(db_dir):
    """[(stamp, snapshot_filename, manifest_filename_or_None)] oldest first."""
    if not os.path.isdir(db_dir):
        return []
    out = []
    for name in sorted(os.listdir(db_dir)):
        m = SNAP_RE.match(name)
        if m:
            manifest = m.group(1) + '.json'
            out.append((m.group(1), name, manifest if os.path.exists(os.path.join(db_dir, manifest)) else None))
    return out


class Lock:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, 'w')

    def __enter__(self):
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('another backup is already running')
        return self

    def __exit__(self, *a):
        self.f.close()


# ── rsync ───────────────────────────────────────────────────────────────────

def remote_path(base, sub):
    return base + sub if base.endswith(':') or base.endswith('/') else base + '/' + sub


def ssh_command(cfg):
    parts = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
             '-o', 'ServerAliveInterval=30', '-o', 'ConnectTimeout=20']
    if cfg.ssh_key:
        parts += ['-i', cfg.ssh_key, '-o', 'IdentitiesOnly=yes']
    if cfg.ssh_port:
        parts += ['-p', cfg.ssh_port]
    return ' '.join(shlex.quote(p) for p in parts)


def rsync(cfg, args, label):
    cmd = ['rsync', '-a', '--partial', '--timeout=300']
    if ':' in cfg.remote.split('/')[0]:        # host:path form -> go over ssh; a plain path is a local copy
        cmd += ['-e', ssh_command(cfg)]
    if cfg.bwlimit:
        cmd.append(f'--bwlimit={cfg.bwlimit}')
    r = subprocess.run(cmd + args, capture_output=True, text=True)
    if r.returncode == 24:
        log(f'{label}: some files vanished while copying (deleted during backup); continuing')
    elif r.returncode != 0:
        raise BackupError(f'{label} failed (rsync exit {r.returncode}): {(r.stderr or r.stdout).strip()[-400:]}')
    return r


# ── commands ────────────────────────────────────────────────────────────────

def cmd_push(cfg, args):
    if not cfg.remote:
        raise BackupError('BACKUP_REMOTE is not set (example: occbackup@droplet.example.com:)')
    passfile = cfg.check_passphrase()
    if not passfile and not cfg.allow_unencrypted:
        raise BackupError('refusing to send an unencrypted database (it holds prayer requests and contact details). '
                          'Set BACKUP_PASSPHRASE_FILE, or BACKUP_ALLOW_UNENCRYPTED=1 to override.')
    if not shutil.which('rsync'):
        raise BackupError('rsync is not installed (sudo apt install rsync)')
    if passfile and not shutil.which('gpg'):
        raise BackupError('gpg is not installed (sudo apt install gnupg)')

    local_db = os.path.join(cfg.backup_dir, 'db')
    os.makedirs(local_db, exist_ok=True)
    with Lock(os.path.join(cfg.backup_dir, '.lock')):
        started = time.time()
        stamp = 'oasis-' + datetime.datetime.now(datetime.timezone.utc).strftime(TS_FMT)
        with tempfile.TemporaryDirectory(dir=cfg.backup_dir) as tmp:
            raw, gz = os.path.join(tmp, stamp + '.db'), os.path.join(tmp, stamp + '.db.gz')
            log('snapshotting database (consistent online copy)')
            snapshot_database(cfg.db_path, raw)
            rows = row_counts(raw)
            gzip_file(raw, gz)
            final_name = stamp + '.db.gz' + ('.gpg' if passfile else '')
            final = os.path.join(tmp, final_name)
            if passfile:
                gpg_encrypt(gz, final, passfile)
            else:
                shutil.move(gz, final)
            conn = sqlite3.connect(raw)
            try:
                referenced = len(referenced_uploads(conn))
            finally:
                conn.close()
            up_count = up_bytes = 0
            if os.path.isdir(cfg.uploads):
                for n in os.listdir(cfg.uploads):
                    p = os.path.join(cfg.uploads, n)
                    if os.path.isfile(p):
                        up_count += 1
                        up_bytes += os.path.getsize(p)
            manifest = {
                'format': 1, 'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                'host': socket.gethostname(), 'snapshot': final_name, 'encrypted': bool(passfile),
                'sha256': sha256_file(final), 'bytes': os.path.getsize(final), 'rows': rows,
                'uploads_files': up_count, 'uploads_bytes': up_bytes, 'uploads_referenced': referenced,
            }
            man_path = os.path.join(tmp, stamp + '.json')
            with open(man_path, 'w') as f:
                json.dump(manifest, f, indent=1, sort_keys=True)
            # keep a local copy first: if the droplet is unreachable tonight we still have tonight's snapshot
            shutil.copy2(final, os.path.join(local_db, final_name))
            shutil.copy2(man_path, os.path.join(local_db, stamp + '.json'))
        log(f'snapshot {final_name}: {manifest["bytes"]:,} bytes, {sum(rows.values()):,} rows in {len(rows)} tables')

        # 1) photos first (incremental; a re-run only sends what is new)
        if os.path.isdir(cfg.uploads):
            log(f'syncing {up_count} upload(s) ({up_bytes / 1048576:.1f} MB)')
            extra = ['--delete-after'] if cfg.sync_delete else []
            rsync(cfg, extra + [cfg.uploads.rstrip('/') + '/', remote_path(cfg.remote, 'uploads/')], 'uploads sync')
        else:
            log('no uploads folder yet; skipping photo sync')
        # 2) database snapshot, then 3) its manifest LAST: a manifest only exists once the snapshot is complete
        rsync(cfg, [os.path.join(local_db, final_name), remote_path(cfg.remote, 'db/')], 'database upload')
        rsync(cfg, [os.path.join(local_db, stamp + '.json'), remote_path(cfg.remote, 'db/')], 'manifest upload')
        log(f'done in {time.time() - started:.0f}s -> {cfg.remote}')

        snaps = list_snapshots(local_db)
        for old_stamp, snap, man in snaps[:-cfg.keep_local] if cfg.keep_local > 0 else []:
            for n in (snap, man):
                if n:
                    os.remove(os.path.join(local_db, n))
            log(f'removed old local snapshot {old_stamp}')


def cmd_status(cfg, args):
    local_db = os.path.join(cfg.backup_dir, 'db')
    snaps = list_snapshots(local_db)
    print(f'Local snapshots in {local_db}: {len(snaps)}')
    for stamp, snap, man in snaps[-5:]:
        size = os.path.getsize(os.path.join(local_db, snap))
        print(f'  {snap}  {size / 1048576:.2f} MB  {"ok" if man else "NO MANIFEST"}')
    if cfg.remote and shutil.which('rsync'):
        r = rsync(cfg, ['--list-only', remote_path(cfg.remote, 'db/')], 'remote listing')
        names = [ln.split()[-1] for ln in r.stdout.splitlines() if ln.strip()]
        remote_snaps = [n for n in names if SNAP_RE.match(n)]
        print(f'Remote ({cfg.remote}): {len(remote_snaps)} snapshot(s); newest: {remote_snaps[-1] if remote_snaps else "none"}')
    elif not cfg.remote:
        print('BACKUP_REMOTE is not set.')


def _pick_snapshot(source, wanted):
    db_dir = os.path.join(source, 'db')
    snaps = [s for s in list_snapshots(db_dir) if s[2]]          # complete ones only
    if not snaps:
        raise BackupError(f'no complete backup found in {db_dir}')
    if wanted:
        match = [s for s in snaps if wanted in (s[0], s[1])]
        if not match:
            raise BackupError(f'snapshot {wanted} not found (have: {", ".join(s[0] for s in snaps[-5:])} ...)')
        return db_dir, match[0]
    return db_dir, snaps[-1]


def _open_snapshot(db_dir, snap, passphrase_file, workdir):
    """Verify checksum, decrypt, gunzip. Returns (path_to_plain_db, manifest)."""
    stamp, name, man_name = snap
    manifest = json.load(open(os.path.join(db_dir, man_name)))
    path = os.path.join(db_dir, name)
    if sha256_file(path) != manifest['sha256']:
        raise BackupError(f'{name}: checksum does not match its manifest (damaged or incomplete transfer)')
    gz = os.path.join(workdir, 'snapshot.db.gz')
    if name.endswith('.gpg'):
        if not passphrase_file:
            raise BackupError('this backup is encrypted: pass --passphrase-file (or set BACKUP_PASSPHRASE_FILE)')
        gpg_decrypt(path, gz, passphrase_file)
    else:
        shutil.copy2(path, gz)
    plain = os.path.join(workdir, 'snapshot.db')
    gunzip_file(gz, plain)
    os.remove(gz)
    check_integrity(plain)
    counts = row_counts(plain)
    if counts != manifest['rows']:
        diff = {t: (manifest['rows'].get(t), counts.get(t)) for t in set(counts) | set(manifest['rows'])
                if counts.get(t) != manifest['rows'].get(t)}
        raise BackupError(f'row counts differ from the manifest: {diff}')
    return plain, manifest


def _missing_uploads(plain_db, uploads_dir):
    conn = open_readonly(plain_db)
    try:
        wanted = referenced_uploads(conn)
    finally:
        conn.close()
    have = set(os.listdir(uploads_dir)) if os.path.isdir(uploads_dir) else set()
    return sorted(wanted - have), len(wanted)


def cmd_verify_quick(cfg, args):
    """Checks that need no passphrase, so a cron job on the droplet never has to hold it:
    the newest snapshot is complete, matches its checksum, is recent, and the photos arrived."""
    source = args.source
    db_dir, snap = _pick_snapshot(source, args.snapshot)
    manifest = json.load(open(os.path.join(db_dir, snap[2])))
    if sha256_file(os.path.join(db_dir, snap[1])) != manifest['sha256']:
        raise BackupError(f'{snap[1]}: checksum does not match its manifest (damaged or incomplete transfer)')
    uploads = os.path.join(source, 'uploads')
    have = len([n for n in os.listdir(uploads) if os.path.isfile(os.path.join(uploads, n))]) if os.path.isdir(uploads) else 0
    age_h = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.datetime.fromisoformat(manifest['created_utc'])).total_seconds() / 3600
    log(f'{snap[1]}: checksum OK, {have}/{manifest["uploads_files"]} upload file(s) present, backup is {age_h:.1f}h old '
        f'(contents not decrypted; run a full verify with the passphrase now and then)')
    problems = []
    if have < manifest['uploads_files']:
        problems.append(f'only {have} of {manifest["uploads_files"]} upload files are on the droplet')
    if args.max_age_hours and age_h > args.max_age_hours:
        problems.append(f'newest backup is {age_h:.0f}h old (limit {args.max_age_hours}h): is the Pi still pushing?')
    if problems:
        raise BackupError('; '.join(problems))
    log('verify --quick: OK')


def cmd_verify(cfg, args):
    if args.quick:
        return cmd_verify_quick(cfg, args)
    source = args.source
    passfile = args.passphrase_file or cfg.check_passphrase()
    db_dir, snap = _pick_snapshot(source, args.snapshot)
    with tempfile.TemporaryDirectory() as tmp:
        plain, manifest = _open_snapshot(db_dir, snap, passfile, tmp)
        missing, total = _missing_uploads(plain, os.path.join(source, 'uploads'))
    age_h = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.datetime.fromisoformat(manifest['created_utc'])).total_seconds() / 3600
    log(f'{snap[1]}: database OK ({sum(manifest["rows"].values()):,} rows), {total} referenced upload(s), '
        f'{len(missing)} missing, backup is {age_h:.1f}h old')
    problems = []
    if missing:
        problems.append(f'{len(missing)} referenced upload(s) are not in the backup, e.g. {missing[:3]}')
    if args.max_age_hours and age_h > args.max_age_hours:
        problems.append(f'newest backup is {age_h:.0f}h old (limit {args.max_age_hours}h): is the Pi still pushing?')
    if problems:
        raise BackupError('; '.join(problems))
    log('verify: OK')


def cmd_restore(cfg, args):
    source, app_dir = args.source, cfg.app_dir
    passfile = args.passphrase_file or cfg.check_passphrase()
    db_dir, snap = _pick_snapshot(source, args.snapshot)
    target_db = cfg.db_path
    os.makedirs(os.path.dirname(target_db), exist_ok=True)
    if os.path.exists(target_db) and not args.force:
        raise BackupError(f'{target_db} already exists. Stop the app, then re-run with --force '
                          f'(the old file is kept as oasis.db.before-restore-<time>).')
    with tempfile.TemporaryDirectory(dir=os.path.dirname(target_db)) as tmp:
        plain, manifest = _open_snapshot(db_dir, snap, passfile, tmp)
        log(f'restoring {snap[1]} (created {manifest["created_utc"]} on {manifest["host"]})')
        if args.dry_run:
            log('dry run: backup verified, nothing changed')
            return
        if os.path.exists(target_db):
            aside = f'{target_db}.before-restore-{datetime.datetime.now().strftime(TS_FMT)}'
            os.replace(target_db, aside)
            log(f'previous database kept as {aside}')
        for ext in ('-wal', '-shm'):                 # stale journals would be replayed onto the new file
            if os.path.exists(target_db + ext):
                os.remove(target_db + ext)
        os.chmod(plain, 0o600)
        os.replace(plain, target_db)
    uploads_src = os.path.join(source, 'uploads')
    uploads_dst = cfg.uploads
    os.makedirs(uploads_dst, exist_ok=True)
    if os.path.isdir(uploads_src):
        if shutil.which('rsync'):
            subprocess.run(['rsync', '-a', uploads_src.rstrip('/') + '/', uploads_dst.rstrip('/') + '/'], check=True)
        else:
            shutil.copytree(uploads_src, uploads_dst, dirs_exist_ok=True)
    if os.geteuid() == 0:                            # restoring as root into a service user's folder
        st = os.stat(app_dir)
        for root, dirs, files in os.walk(uploads_dst):
            for n in dirs + files:
                os.chown(os.path.join(root, n), st.st_uid, st.st_gid)
        os.chown(uploads_dst, st.st_uid, st.st_gid)
        parent = os.path.dirname(uploads_dst)           # static/ - only if we had to create it
        if os.stat(parent).st_uid == 0:
            os.chown(parent, st.st_uid, st.st_gid)
        os.chown(target_db, st.st_uid, st.st_gid)
    missing, total = _missing_uploads(target_db, uploads_dst)
    log(f'database restored; {total - len(missing)}/{total} referenced uploads present')
    if missing:
        log(f'WARNING: {len(missing)} referenced upload(s) missing from the backup, e.g. {missing[:5]}')
    log('next: start the app (sudo systemctl start occhub) and check /hub and /admin')


def cmd_prune(cfg, args):
    db_dir = os.path.join(args.source, 'db')
    snaps = list_snapshots(db_dir)
    complete = [s for s in snaps if s[2]]
    stale = snaps[:-args.keep] if args.keep > 0 else []
    if len(complete) <= args.keep:
        log(f'prune: {len(complete)} complete snapshot(s), keeping {args.keep}; nothing to remove')
        return
    removed = 0
    for stamp, snap, man in stale:
        if args.dry_run:
            log(f'would remove {stamp}')
            continue
        for n in (man, snap):                        # manifest first: a lone snapshot is "incomplete", never trusted
            if n:
                os.remove(os.path.join(db_dir, n))
        removed += 1
    log(f'prune: removed {removed} old snapshot(s), kept the newest {args.keep}')


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--app-dir', help=f'OccHub install folder (default: {APP_DIR_DEFAULT})')
    p.add_argument('--env-file', help='settings file (default: <app dir>/.backup.env)')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('push', help='back up to the droplet').set_defaults(fn=cmd_push)
    sub.add_parser('status', help='list local and remote snapshots').set_defaults(fn=cmd_status)
    for name, fn, h in (('verify', cmd_verify, 'check the newest backup is intact'),
                        ('restore', cmd_restore, 'restore a backup into an OccHub install')):
        sp = sub.add_parser(name, help=h)
        sp.add_argument('--source', default='/srv/occhub-backup', help='backup folder holding db/ and uploads/')
        sp.add_argument('--snapshot', help='snapshot to use, e.g. oasis-20261010-023000 (default: newest)')
        sp.add_argument('--passphrase-file', help='file holding the backup passphrase')
        sp.set_defaults(fn=fn)
        if name == 'verify':
            sp.add_argument('--max-age-hours', type=float, default=0, help='fail if the newest backup is older')
            sp.add_argument('--quick', action='store_true',
                            help='checksum, freshness and photo count only; needs no passphrase (for cron)')
        else:
            sp.add_argument('--force', action='store_true', help='replace an existing database (kept aside)')
            sp.add_argument('--dry-run', action='store_true', help='verify the backup, change nothing')
    sp = sub.add_parser('prune', help='delete all but the newest N snapshots')
    sp.add_argument('--source', default='/srv/occhub-backup')
    sp.add_argument('--keep', type=int, default=30)
    sp.add_argument('--dry-run', action='store_true')
    sp.set_defaults(fn=cmd_prune)
    return p


def ping(cfg, suffix=''):
    """Optional dead-man's-switch (healthchecks.io style): GET <url> on success, <url>/fail on failure.
    The monitor alerts you by email when the success ping stops arriving, which also catches the
    case where the Pi is off and nothing runs at all. Never allowed to break the backup itself."""
    if not cfg.ping_url:
        return
    try:
        urllib.request.urlopen(cfg.ping_url + suffix, timeout=10).read()
    except Exception as e:
        log(f'(monitor ping failed: {e})')


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = Config(args)
    try:
        args.fn(cfg, args)
    except BackupError as e:
        log(f'ERROR: {e}')
        if args.cmd == 'push':
            ping(cfg, '/fail')
        return 1
    if args.cmd == 'push':
        ping(cfg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
