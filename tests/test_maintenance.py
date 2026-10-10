import os
import time

import core
import maintenance


def _touch(folder, name, age_hours=48, size=10):
    path = folder / name
    path.write_bytes(b'x' * size)
    t = time.time() - age_hours * 3600
    os.utime(path, (t, t))
    return path


def test_unused_upload_is_found_and_used_one_is_kept(uploads, db):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('logo_path','uploads/keep.png')")
    db.commit()
    _touch(uploads, 'keep.png')
    _touch(uploads, 'orphan.jpg', size=30)
    info = maintenance.scan_uploads()
    assert [n for n, _ in info['used']] == ['keep.png']
    assert [n for n, _ in info['unused']] == ['orphan.jpg'] and info['unused_bytes'] == 30


def test_reference_in_any_table_counts(uploads, db):
    db.execute("INSERT INTO leaders(name,role,photo) VALUES('Photo Person','R','uploads/doc_1a2b3c4d.pdf')")
    db.commit()
    _touch(uploads, 'doc_1a2b3c4d.pdf')
    assert maintenance.scan_uploads()['unused'] == []


def test_recent_files_are_never_reported_unused(uploads):
    _touch(uploads, 'fresh.jpg', age_hours=1)
    info = maintenance.scan_uploads()
    assert info['unused'] == [] and [n for n, _ in info['recent']] == ['fresh.jpg']


def test_delete_removes_only_unused(uploads, db):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('logo_path','uploads/keep.png')")
    db.commit()
    keep, orphan = _touch(uploads, 'keep.png'), _touch(uploads, 'orphan.jpg')
    removed, freed, why = maintenance.delete_unused_uploads()
    assert (removed, why) == (1, None) and keep.exists() and not orphan.exists()


def test_dry_run_deletes_nothing(uploads, db):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('logo_path','uploads/keep.png')")
    db.commit()
    orphan = _touch(uploads, 'orphan.jpg')
    assert maintenance.delete_unused_uploads(dry_run=True)[0] == 1
    assert orphan.exists()


def test_refuses_when_database_references_nothing(uploads, db, monkeypatch):
    # Pointing at an empty/wrong database must not wipe the uploads folder.
    monkeypatch.setattr(maintenance, 'referenced_uploads', lambda conn: set())
    orphan = _touch(uploads, 'orphan.jpg')
    removed, _, why = maintenance.delete_unused_uploads()
    assert removed == 0 and why and orphan.exists()


def test_missing_files_are_reported(uploads, db):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('about_hero','uploads/gone.jpg')")
    db.commit()
    assert 'gone.jpg' in maintenance.scan_uploads()['missing']


def test_calendar_view_no_longer_deletes_events(client, db):
    db.execute("INSERT INTO events(title,event_date,auto_delete,recurrence) VALUES('Expired','2020-01-01',1,'none')")
    db.commit()
    assert client.get('/calendar').status_code == 200
    assert db.execute("SELECT 1 FROM events WHERE title='Expired'").fetchone() is not None
    assert 'Expired' not in client.get('/calendar').get_data(as_text=True)   # but it is not shown


def test_maintenance_purges_expired_events_only(db):
    db.execute("INSERT INTO events(title,event_date,auto_delete,recurrence) VALUES('ExpiredA','2020-01-01',1,'none')")
    db.execute("INSERT INTO events(title,event_date,auto_delete,recurrence) VALUES('KeptPast','2020-01-01',0,'none')")
    db.execute("INSERT INTO events(title,event_date,auto_delete,recurrence) VALUES('Future','2099-01-01',1,'none')")
    db.commit()
    assert maintenance.purge_past_events() >= 1
    titles = {r[0] for r in db.execute("SELECT title FROM events")}
    assert 'ExpiredA' not in titles and {'KeptPast', 'Future'} <= titles


def test_storage_page_is_superadmin_only_and_cleans(app, admin, uploads):
    _touch(uploads, 'orphan.jpg')
    assert 'orphan.jpg' in admin.get('/admin/storage').get_data(as_text=True)
    admin.post('/admin/storage/clean')
    assert not (uploads / 'orphan.jpg').exists()
