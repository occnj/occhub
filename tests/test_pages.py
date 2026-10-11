"""Smoke test: every GET page (public and admin) renders without a server error."""
import re

import pytest

import core

SEED = [
    "INSERT INTO missions(title,summary,body,cover_photo,sort_order) VALUES('M1','sum','<p>body</p>','uploads/a.jpg',1)",
    "INSERT INTO mission_images(mission_id,photo,caption,sort_order) VALUES((SELECT MAX(id) FROM missions),'uploads/b.jpg','cap',1)",
    "INSERT INTO beyond_walls(title,summary,body,cover_photo,sort_order,youtube_video_id) VALUES('B1','s','b','',1,'abcdefghijk')",
    "INSERT INTO behind_scene_people(name,bio,photo,sort_order) VALUES('P1','bio','',1)",
    "INSERT OR IGNORE INTO behind_scene_assignments(person_id,scene_id,role,slot_order) VALUES((SELECT MAX(id) FROM behind_scene_people),(SELECT MIN(id) FROM behind_scenes),'Lead',1)",
    "INSERT INTO sermon_notes(title,note_date,summary,body_html,source_file) VALUES('S1','2026-01-04','sum','<p>hi</p>','uploads/n.pdf')",
    "INSERT INTO sermon_next_steps(note_id,file,title,sort_order) VALUES((SELECT MAX(id) FROM sermon_notes),'uploads/ns_12345678.pdf','NS',0)",
    "INSERT INTO events(title,description,event_date,event_time,location,signup_url,auto_delete,recurrence,recurrence_detail) VALUES('E1','d','2099-01-01','10:00','here','',0,'none','')",
    "INSERT INTO events(title,description,event_date,event_time,location,signup_url,auto_delete,recurrence,recurrence_detail) VALUES('Rec','d','2020-01-01','10:00','here','',0,'monthly','1st-Sunday')",
    "INSERT INTO submissions(submitted_at,full_name,email) VALUES('2026-01-01 10:00','Sub','s@x.com')",
    "INSERT INTO prayer_requests(submitted_at,full_name,message) VALUES('2026-01-01 10:00','Pr','pray')",
    "INSERT INTO app_feedback(submitted_at,full_name,rating,message) VALUES('2026-01-01 10:00','Fb',5,'nice')",
]


def _routes(app):
    out = set()
    for rule in app.url_map.iter_rules():
        if ('GET' not in rule.methods or rule.endpoint == 'static' or rule.endpoint.startswith('honeypot')
                or rule.rule == '/admin/logout'):
            continue
        out.add(re.sub(r'<\w+>', 'abc', re.sub(r'<int:\w+>', '1', rule.rule)))
    return sorted(out)


@pytest.fixture(scope='module', autouse=True)
def seeded(app):
    conn = core.get_db()
    try:
        for q in SEED:
            conn.execute(q)
        conn.commit()
    finally:
        conn.close()


def test_every_public_page_renders(app, client):
    bad = {p: client.get(p).status_code for p in _routes(app)
           if not p.startswith('/admin') and client.get(p).status_code >= 500}
    assert not bad, bad


def test_every_admin_page_renders(app, admin):
    paths = [p for p in _routes(app) if p.startswith('/admin') and p != '/admin/login']
    assert len(paths) > 30
    bad = {p: admin.get(p).status_code for p in paths if admin.get(p).status_code >= 500}
    assert not bad, bad


def test_admin_pages_require_login(client):
    for p in ('/admin', '/admin/settings', '/admin/storage', '/admin/users'):
        r = client.get(p)
        assert r.status_code == 302 and 'login' in r.headers['Location'], p
