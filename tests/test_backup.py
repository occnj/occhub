"""End-to-end tests of tools/occhub_backup.py with real rsync and gpg (a local folder stands in
for the droplet; the ssh leg is only a transport and is covered by the deployment checklist)."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import occhub_backup as ob  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which('rsync') and shutil.which('gpg')), reason='needs rsync and gpg')

MARKER = 'PRIVATE-PRAYER-REQUEST-TEXT'


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A 'Pi' install, an empty 'droplet' backup folder, and a passphrase file."""
    gnupg = tempfile.mkdtemp(prefix='gh', dir='/tmp')               # short path: gpg-agent socket limit
    os.chmod(gnupg, 0o700)
    monkeypatch.setenv('GNUPGHOME', gnupg)
    for k in list(os.environ):
        if k.startswith(('BACKUP_', 'OCC_', 'KEEP_', 'APP_')):
            monkeypatch.delenv(k)
    pi = tmp_path / 'pi'
    (pi / 'static' / 'uploads').mkdir(parents=True)
    droplet = tmp_path / 'droplet'
    droplet.mkdir()
    for n, body in (('a1b2c3d4.jpg', b'photo-one'), ('notes_deadbeef.pdf', b'%PDF-notes')):
        (pi / 'static' / 'uploads' / n).write_bytes(body)

    db = pi / 'oasis.db'
    writer = sqlite3.connect(db)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')                    # keep recent commits ONLY in the -wal file
    writer.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE prayer_requests (id INTEGER PRIMARY KEY, message TEXT);
        CREATE TABLE leaders (id INTEGER PRIMARY KEY, name TEXT, photo TEXT);
    """)
    writer.execute("INSERT INTO settings VALUES ('logo_path','uploads/a1b2c3d4.jpg')")
    writer.execute("INSERT INTO leaders(name,photo) VALUES ('Pat','uploads/notes_deadbeef.pdf')")
    writer.execute("INSERT INTO prayer_requests(message) VALUES (?)", (MARKER,))
    writer.commit()
    writer.close()
    writer = sqlite3.connect(db)                                     # new connection: commit lands in the WAL only
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute("INSERT INTO prayer_requests(message) VALUES ('committed-but-only-in-the-wal')")
    writer.commit()                                                  # connection stays open on purpose
    assert os.path.getsize(str(db) + '-wal') > 0

    pw = tmp_path / 'pass'
    pw.write_text('correct horse battery staple\n')
    os.chmod(pw, 0o600)

    class World:
        pass
    w = World()
    w.pi, w.droplet, w.pass_file, w.db, w.writer, w.tmp = pi, droplet, pw, db, writer, tmp_path
    w.mp = monkeypatch
    yield w
    writer.close()
    shutil.rmtree(gnupg, ignore_errors=True)


def run(w, *argv, app_dir=None, env=None):
    for k, v in (env or {}).items():
        w.mp.setenv(k, v)
    return ob.main(['--app-dir', str(app_dir or w.pi), *argv])


def push(w, **env):
    base = {'BACKUP_REMOTE': str(w.droplet), 'BACKUP_PASSPHRASE_FILE': str(w.pass_file)}
    base.update(env)
    return run(w, 'push', env=base)


def newest(w, suffix):
    return sorted(p for p in (w.droplet / 'db').iterdir() if p.name.endswith(suffix))[-1]


def new_app(w, name='new'):
    d = w.tmp / name
    d.mkdir()
    return d




def test_round_trip(world):
    w = world
    assert push(w) == 0
    assert len(list((w.droplet / 'db').glob('*.json'))) == 1
    assert sorted(p.name for p in (w.droplet / 'uploads').iterdir()) == ['a1b2c3d4.jpg', 'notes_deadbeef.pdf']

    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file), app_dir=target) == 0
    c = sqlite3.connect(target / 'oasis.db')
    msgs = [r[0] for r in c.execute('SELECT message FROM prayer_requests ORDER BY id')]
    assert msgs == [MARKER, 'committed-but-only-in-the-wal']          # the WAL-only row survived
    assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert (target / 'static' / 'uploads' / 'a1b2c3d4.jpg').read_bytes() == b'photo-one'


def test_database_on_the_droplet_is_encrypted(world):
    w = world
    assert push(w) == 0
    blob = newest(w, '.gpg').read_bytes()
    assert MARKER.encode() not in blob and b'SQLite format' not in blob
    assert json.load(open(newest(w, '.json')))['encrypted'] is True


def test_refuses_to_send_unencrypted_by_default(world):
    w = world
    assert run(w, 'push', env={'BACKUP_REMOTE': str(w.droplet)}) == 1
    assert not (w.droplet / 'db').exists() or not list((w.droplet / 'db').iterdir())


def test_unencrypted_is_allowed_when_explicit(world):
    w = world
    assert run(w, 'push', env={'BACKUP_REMOTE': str(w.droplet), 'BACKUP_ALLOW_UNENCRYPTED': '1'}) == 0
    assert json.load(open(newest(w, '.json')))['encrypted'] is False
    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), app_dir=target) == 0


def test_passphrase_file_must_be_private(world):
    w = world
    os.chmod(w.pass_file, 0o644)
    assert push(w) == 1


def test_damaged_backup_is_detected(world):
    w = world
    push(w)
    snap = newest(w, '.gpg')
    data = bytearray(snap.read_bytes())
    data[len(data) // 2] ^= 0xFF
    snap.write_bytes(bytes(data))
    assert run(w, 'verify', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file)) == 1
    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file), app_dir=target) == 1
    assert not (target / 'oasis.db').exists()


def test_wrong_passphrase_changes_nothing(world):
    w = world
    push(w)
    bad = w.tmp / 'bad'
    bad.write_text('not the passphrase')
    os.chmod(bad, 0o600)
    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), '--passphrase-file', str(bad), app_dir=target) == 1
    assert not (target / 'oasis.db').exists()


def test_restore_will_not_overwrite_without_force_and_keeps_the_old_file(world):
    w = world
    push(w)
    target = new_app(w)
    (target / 'oasis.db').write_text('existing database')
    args = ('restore', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file))
    assert run(w, *args, app_dir=target) == 1
    assert (target / 'oasis.db').read_text() == 'existing database'
    assert run(w, *args, '--force', app_dir=target) == 0
    aside = [p for p in target.iterdir() if p.name.startswith('oasis.db.before-restore-')]
    assert len(aside) == 1 and aside[0].read_text() == 'existing database'
    assert sqlite3.connect(target / 'oasis.db').execute('SELECT COUNT(*) FROM leaders').fetchone()[0] == 1


def test_stale_wal_files_are_not_replayed_onto_a_restored_database(world):
    w = world
    push(w)
    target = new_app(w)
    (target / 'oasis.db').write_text('old')
    (target / 'oasis.db-wal').write_bytes(b'garbage' * 100)
    (target / 'oasis.db-shm').write_bytes(b'garbage' * 100)
    assert run(w, 'restore', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file), '--force',
               app_dir=target) == 0
    assert not (target / 'oasis.db-wal').exists()
    assert sqlite3.connect(target / 'oasis.db').execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_dry_run_changes_nothing(world):
    w = world
    push(w)
    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file), '--dry-run',
               app_dir=target) == 0
    assert list(target.iterdir()) == []


def test_second_push_is_incremental_and_never_deletes_by_default(world):
    w = world
    push(w)
    first_mtime = (w.droplet / 'uploads' / 'a1b2c3d4.jpg').stat().st_mtime_ns
    (w.pi / 'static' / 'uploads' / 'ffffffff.jpg').write_bytes(b'new-photo')
    os.remove(w.pi / 'static' / 'uploads' / 'notes_deadbeef.pdf')
    assert push(w) == 0
    up = sorted(p.name for p in (w.droplet / 'uploads').iterdir())
    assert up == ['a1b2c3d4.jpg', 'ffffffff.jpg', 'notes_deadbeef.pdf']   # new one added, deleted one kept
    assert (w.droplet / 'uploads' / 'a1b2c3d4.jpg').stat().st_mtime_ns == first_mtime   # unchanged file untouched


def test_sync_delete_mirrors_removals_when_enabled(world):
    w = world
    push(w)
    os.remove(w.pi / 'static' / 'uploads' / 'notes_deadbeef.pdf')
    assert push(w, BACKUP_SYNC_DELETE='1') == 0
    assert sorted(p.name for p in (w.droplet / 'uploads').iterdir()) == ['a1b2c3d4.jpg']


def test_verify_flags_uploads_missing_from_the_backup(world):
    w = world
    push(w)
    os.remove(w.droplet / 'uploads' / 'a1b2c3d4.jpg')
    assert run(w, 'verify', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file)) == 1


def test_verify_passes_on_a_good_backup_and_flags_a_stale_one(world):
    w = world
    push(w)
    args = ('verify', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file))
    assert run(w, *args) == 0
    man = newest(w, '.json')
    data = json.load(open(man))
    data['created_utc'] = '2020-01-01T00:00:00+00:00'
    json.dump(data, open(man, 'w'))
    assert run(w, *args, '--max-age-hours', '30') == 1


def test_a_snapshot_without_its_manifest_is_never_used(world):
    w = world
    push(w)
    good = newest(w, '.gpg').name
    stamp = good.split('.')[0]
    # simulate a transfer that died: a newer snapshot file arrived but its manifest did not
    (w.droplet / 'db' / 'oasis-29990101-000000.db.gz.gpg').write_bytes(b'half a file')
    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), '--passphrase-file', str(w.pass_file), app_dir=target) == 0
    assert sqlite3.connect(target / 'oasis.db').execute('SELECT COUNT(*) FROM prayer_requests').fetchone()[0] == 2
    assert stamp in ''.join(os.listdir(w.droplet / 'db'))


def test_restore_a_named_older_snapshot(world):
    w = world
    push(w)
    first = newest(w, '.json').stem
    w.writer.execute("INSERT INTO prayer_requests(message) VALUES ('later')")
    w.writer.commit()
    os.rename(w.droplet / 'db' / (first + '.json'), w.droplet / 'db' / 'oasis-20200101-000000.json')
    os.rename(w.droplet / 'db' / (first + '.db.gz.gpg'), w.droplet / 'db' / 'oasis-20200101-000000.db.gz.gpg')
    push(w)
    target = new_app(w)
    assert run(w, 'restore', '--source', str(w.droplet), '--snapshot', 'oasis-20200101-000000',
               '--passphrase-file', str(w.pass_file), app_dir=target) == 0
    assert sqlite3.connect(target / 'oasis.db').execute('SELECT COUNT(*) FROM prayer_requests').fetchone()[0] == 2


def test_prune_keeps_the_newest(world):
    w = world
    db = w.droplet / 'db'
    db.mkdir()
    for i in range(1, 6):
        (db / f'oasis-2026010{i}-000000.db.gz.gpg').write_bytes(b'x')
        (db / f'oasis-2026010{i}-000000.json').write_text('{}')
    assert run(w, 'prune', '--source', str(w.droplet), '--keep', '2', '--dry-run') == 0
    assert len(list(db.glob('*.json'))) == 5
    assert run(w, 'prune', '--source', str(w.droplet), '--keep', '2') == 0
    assert sorted(p.stem.split('.')[0] for p in db.glob('*.json')) == ['oasis-20260104-000000', 'oasis-20260105-000000']
    assert len(list(db.iterdir())) == 4


def test_local_snapshots_are_pruned_to_keep_local(world):
    w = world
    for _ in range(3):
        (w.pi / 'static' / 'uploads' / f'{_}.jpg').write_bytes(b'x')
        assert push(w, KEEP_LOCAL='2') == 0
        import time as _t; _t.sleep(1.1)                              # snapshot names are per-second
    assert len(list((w.pi / 'backups' / 'db').glob('*.json'))) == 2


def test_overlapping_runs_are_refused(world):
    w = world
    (w.pi / 'backups').mkdir()
    with ob.Lock(str(w.pi / 'backups' / '.lock')):
        assert push(w) == 1


def test_paths_with_spaces_and_quotes(world):
    w = world
    weird = w.tmp / "Church Pi's folder"
    shutil.copytree(w.pi, weird, symlinks=True)
    assert run(w, 'push', app_dir=weird, env={'BACKUP_REMOTE': str(w.droplet),
                                             'BACKUP_PASSPHRASE_FILE': str(w.pass_file),
                                             'OCC_DB_PATH': str(weird / 'oasis.db')}) == 0


def test_quick_verify_needs_no_passphrase(world):
    w = world
    push(w)
    assert run(w, 'verify', '--quick', '--source', str(w.droplet), '--max-age-hours', '30') == 0


def test_quick_verify_catches_tampering_missing_photos_and_staleness(world):
    w = world
    push(w)
    base = ('verify', '--quick', '--source', str(w.droplet))
    os.remove(w.droplet / 'uploads' / 'a1b2c3d4.jpg')
    assert run(w, *base) == 1                                          # photo count short of the manifest
    shutil.copy(w.pi / 'static' / 'uploads' / 'a1b2c3d4.jpg', w.droplet / 'uploads')
    assert run(w, *base) == 0
    man = newest(w, '.json')
    data = json.load(open(man))
    data['created_utc'] = '2020-01-01T00:00:00+00:00'
    json.dump(data, open(man, 'w'))
    assert run(w, *base, '--max-age-hours', '30') == 1                 # stale
    data['created_utc'] = ob.datetime.datetime.now(ob.datetime.timezone.utc).isoformat()
    json.dump(data, open(man, 'w'))
    snap = newest(w, '.gpg')
    blob = bytearray(snap.read_bytes())
    blob[10] ^= 0xFF
    snap.write_bytes(bytes(blob))
    assert run(w, *base) == 1                                          # checksum no longer matches


def _ping_server():
    import http.server
    import threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


def test_monitor_is_pinged_on_success_and_on_failure(world):
    w = world
    srv, hits = _ping_server()
    try:
        url = f'http://127.0.0.1:{srv.server_port}/check-uuid'
        assert push(w, BACKUP_PING_URL=url) == 0
        assert hits == ['/check-uuid']
        os.chmod(w.pass_file, 0o644)                                   # make the next run fail
        assert push(w, BACKUP_PING_URL=url) == 1
        assert hits == ['/check-uuid', '/check-uuid/fail']
    finally:
        srv.shutdown()


def test_a_dead_monitor_never_breaks_the_backup(world):
    w = world
    assert push(w, BACKUP_PING_URL='http://127.0.0.1:9/nothing-listens-here') == 0
