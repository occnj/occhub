import csv
import io
import sqlite3
import time
import zipfile

import pytest

import export

ROOT = 'Oasis Hub export 2026-10-28'


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    up = tmp_path / 'uploads'
    up.mkdir()
    monkeypatch.setattr(export, 'UPLOAD_FOLDER', str(up))
    monkeypatch.setattr(export, 'EXPORT_DIR', str(tmp_path / 'exports'))
    monkeypatch.setattr(export, '_STATUS', str(tmp_path / 'exports' / 'status.json'))
    for name in ('pastor_ab12.jpg', 'well_cd34.jpg', 'well2_ef56.jpg', 'old_9999.jpg'):
        (up / name).write_bytes(b'\xff\xd8 picture ' + name.encode())
    db.execute("INSERT INTO leaders (name, role, bio, photo, sort_order) VALUES "
               "('Pastor Exporttest', 'Lead Pastor', 'Loves <b>Jesus</b> &amp; people', 'uploads/pastor_ab12.jpg', -1)")
    mid = db.execute("INSERT INTO missions (title, summary, cover_photo, sort_order) VALUES "
                     "('Clean Water Export', 'Wells in Kenya', 'uploads/well_cd34.jpg', -1)").lastrowid
    db.execute("INSERT INTO mission_images (mission_id, photo, caption) VALUES (?, 'uploads/well2_ef56.jpg', 'The new well')", (mid,))
    db.commit()
    yield up
    db.execute("DELETE FROM leaders WHERE name='Pastor Exporttest'")
    db.execute("DELETE FROM mission_images WHERE mission_id=?", (mid,))
    db.execute("DELETE FROM missions WHERE id=?", (mid,))
    db.commit()


def built(stamp='2026-10-28'):
    name = export.build_export(stamp)
    return zipfile.ZipFile(f'{export.EXPORT_DIR}/{name}')


def folder_of(zf, file_name):
    return next(n for n in zf.namelist() if n.endswith('/' + file_name)).rsplit('/', 1)[0]


def test_each_item_gets_a_folder_with_its_text_and_its_own_pictures(world):
    zf = built()
    leader = folder_of(zf, 'pastor_ab12.jpg')
    assert leader.startswith(f'{ROOT}/Leadership/') and leader.endswith('Pastor Exporttest')
    text = zf.read(f'{leader}/text.txt').decode()
    assert 'Lead Pastor' in text and 'Loves Jesus & people' in text            # rich text made readable
    assert zf.read(f'{leader}/pastor_ab12.jpg').startswith(b'\xff\xd8')


def test_gallery_photos_go_in_their_parents_folder(world):
    zf = built()
    mission = folder_of(zf, 'well_cd34.jpg')
    assert mission.startswith(f'{ROOT}/Mission/') and folder_of(zf, 'well2_ef56.jpg') == mission
    assert 'The new well' in zf.read(f'{mission}/text.txt').decode()


def test_unused_uploads_database_and_spreadsheets_are_included(world):
    zf = built()
    assert f'{ROOT}/Not used by anything/old_9999.jpg' in zf.namelist()
    users = zf.read(f'{ROOT}/Spreadsheets/Admin Users.csv').decode('utf-8-sig')
    header = next(csv.reader(io.StringIO(users)))
    assert 'username' in header and 'password' not in header                 # never the password hashes
    data = zf.read(f'{ROOT}/Database/oasis.db')
    path = f'{export.EXPORT_DIR}/check.db'
    open(path, 'wb').write(data)
    assert sqlite3.connect(path).execute("SELECT name FROM leaders WHERE name='Pastor Exporttest'").fetchone()


def test_start_here_shows_each_item_with_its_pictures(world):
    html = built().read(f'{ROOT}/START HERE.html').decode()
    pastor = html.index('Pastor Exporttest')
    assert html.index('Leadership/') < pastor < html.index('pastor_ab12.jpg', pastor)
    assert '<img src="Leadership/' in html and 'Not used by anything/old_9999.jpg' in html
    assert 'password' not in html.lower()


def test_admin_prepares_and_downloads_the_zip(admin, world):
    assert 'Prepare download' in admin.get('/admin/export').get_data(as_text=True)
    assert admin.post('/admin/export/build').status_code == 302
    for _ in range(100):
        if admin.get('/admin/export/status').get_json()['state'] != 'building':
            break
        time.sleep(0.1)
    page = admin.get('/admin/export').get_data(as_text=True)
    assert 'Ready' in page and 'Download oasis-hub-export-' in page
    r = admin.get('/admin/export/download')
    assert r.status_code == 200 and r.data[:2] == b'PK'
    assert admin.post('/admin/export/delete').status_code == 302
    assert admin.get('/admin/export/status').get_json()['state'] == 'none'


def test_only_admins_can_download(client):
    assert client.get('/admin/export').status_code == 302
    assert client.get('/admin/export/download').status_code == 302


# ---- putting an export into another Hub -----------------------------------------------------

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import restore_to_hub  # noqa: E402


def unpacked(tmp_path):
    zf = built()
    zf.extractall(tmp_path / 'unzipped')
    return tmp_path / 'unzipped' / ROOT


def test_manifest_and_item_json_say_where_everything_goes(world, tmp_path):
    root = unpacked(tmp_path)
    manifest = json.loads((root / 'manifest.json').read_text())
    assert (root / manifest['restore_script']).exists() and (root / 'HOW TO IMPORT.md').exists()
    leaders = next(s for s in manifest['sections'] if s['table'] == 'leaders')
    item = next(i for i in leaders['items'] if i['title'] == 'Pastor Exporttest')
    assert item['records'][0]['photo'] == 'uploads/pastor_ab12.jpg' and item['admin_route'] == f"/admin/leaders/{item['id']}/edit"
    f = item['files'][0]
    assert (f['column'], f['hub_path'], f['db_value']) == ('photo', 'static/uploads/pastor_ab12.jpg', 'uploads/pastor_ab12.jpg')
    assert (root / f['zip_path']).read_bytes().startswith(b'\xff\xd8')
    assert json.loads((root / item['folder'] / 'item.json').read_text()) == item
    mission = next(i for s in manifest['sections'] for i in s['items'] if i['title'] == 'Clean Water Export')
    assert mission['public_route'] == f"/mission/{mission['id']}"
    assert mission['children'][0]['table'] == 'mission_images' and mission['children'][0]['parent_column'] == 'mission_id'
    users = next((s for s in manifest['sections'] if s['table'] == 'admin_users'), None)
    assert users is None and 'password' not in (root / 'manifest.json').read_text().lower()


def other_hub(tmp_path, with_db=True):
    hub = tmp_path / 'other_hub'
    (hub / 'static' / 'uploads').mkdir(parents=True)
    if with_db:
        src = sqlite3.connect(export.DB)
        dst = sqlite3.connect(hub / 'oasis.db')
        src.backup(dst)
        src.close()
        dst.execute("DELETE FROM leaders")
        dst.execute("DELETE FROM mission_images")
        dst.execute("DELETE FROM missions")
        dst.commit()
        dst.close()
    return hub


def test_merge_adds_items_with_new_ids_children_and_files(world, tmp_path):
    root, hub = unpacked(tmp_path), other_hub(tmp_path)
    dry = restore_to_hub.main([str(hub), '--export', str(root), '--merge', '--only', 'Mission', '--dry-run'])
    assert dry['added'] and not os.listdir(hub / 'static' / 'uploads')
    assert sqlite3.connect(hub / 'oasis.db').execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0

    restore_to_hub.main([str(hub), '--export', str(root), '--merge', '--only', 'Mission', '--only', 'Leadership'])
    db = sqlite3.connect(hub / 'oasis.db')
    mid, cover = db.execute("SELECT id, cover_photo FROM missions WHERE title='Clean Water Export'").fetchone()
    assert cover == 'uploads/well_cd34.jpg'
    assert db.execute("SELECT photo, caption FROM mission_images WHERE mission_id=?", (mid,)).fetchone() == \
        ('uploads/well2_ef56.jpg', 'The new well')
    assert db.execute("SELECT role FROM leaders WHERE name='Pastor Exporttest'").fetchone() == ('Lead Pastor',)
    assert sorted(os.listdir(hub / 'static' / 'uploads')) == ['pastor_ab12.jpg', 'well2_ef56.jpg', 'well_cd34.jpg']
    again = restore_to_hub.main([str(hub), '--export', str(root), '--files'])
    assert again['files_kept'] == 3 and again['files_copied'] == 1          # only the unused one was new


def test_replace_database_restores_the_whole_hub(world, tmp_path):
    root, hub = unpacked(tmp_path), other_hub(tmp_path)
    restore_to_hub.main([str(hub), '--export', str(root), '--replace-database'])
    db = sqlite3.connect(hub / 'oasis.db')
    assert db.execute("SELECT 1 FROM leaders WHERE name='Pastor Exporttest'").fetchone()
    assert any(n.startswith('oasis.db.before-restore-') for n in os.listdir(hub))
    assert len(os.listdir(hub / 'static' / 'uploads')) == 4
