import pytest

import hub_layout as hl


def _set(db, **values):
    for k, v in values.items():
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    db.commit()


@pytest.fixture(autouse=True)
def new_layout(db):
    _set(db, hub_layout='new', service_day='0', service_times='08:30, 10:00, 11:30', service_starting_minutes='2')
    db.execute("UPDATE hub_cards SET is_active=1")
    db.commit()
    yield
    _set(db, hub_layout='new')
    db.execute("UPDATE hub_cards SET is_active=1")
    db.commit()


@pytest.mark.parametrize('text, expected', [
    ('8:30', '08:30'), ('08:30', '08:30'), ('8:30 AM', '08:30'), ('11:30am', '11:30'), ('10am', '10:00'),
    ('12:15 pm', '12:15'), ('12:00 AM', '00:00'), ('6:00 p.m.', '18:00'), ('25:00', None), ('13:00 pm', None), ('soon', None),
])
def test_parse_service_time(text, expected):
    assert hl.parse_service_time(text) == expected


def test_note_preview_prefers_list_items_and_trims():
    note = {'body_html': '<h2>Intro</h2><p>Opening words here.</p><ol><li>Hope is an <b>anchor</b>.<br>Really.</li>'
                         '<li>' + 'word ' * 60 + '</li><li>Third</li></ol>'}
    points = hl.note_preview(note)
    assert points[0] == 'Hope is an anchor. Really.' and len(points) == 2 and points[1].endswith('…') and len(points[1]) <= 111


def test_note_preview_falls_back_to_paragraphs():
    assert hl.note_preview({'body_html': '<p>First &amp; best.</p><p>Second.</p><p>Third.</p>'}) == ['First & best.', 'Second.']


def test_new_hub_shows_countdown_notes_and_sections(client, db):
    db.execute("INSERT INTO sermon_notes(title,note_date,summary,body_html) VALUES('Rooted in Hope','2099-01-01','s',"
               "'<ol><li>Hope is an anchor.</li><li>Stay rooted.</li></ol>')")
    nid = db.execute("SELECT MAX(id) FROM sermon_notes").fetchone()[0]
    db.execute("INSERT INTO sermon_next_steps(note_id,file,title,sort_order) VALUES(?,?,?,0)", (nid, 'uploads/x_12345678.pdf', 'Sheet'))
    db.execute("INSERT INTO events(title,event_date,event_time,auto_delete,recurrence) VALUES('Fall Outreach Day','2099-10-18','9:00 AM',0,'none')")
    db.commit()
    html = client.get('/hub').get_data(as_text=True)
    assert 'data-times="08:30,10:00,11:30"' in html and 'data-lead="2"' in html
    assert '8:30 AM' in html and '11:30 AM' in html
    assert 'Rooted in Hope' in html and 'Hope is an anchor.' in html
    assert f'/sermon-notes/{nid}#next-steps' in html and '1 Next Step<' in html
    assert 'Fall Outreach Day' in html and 'COMING UP' in html
    assert 'bi-person-raised-hand' in html and 'More' in html


def test_no_notification_bell_or_watch_button(client):
    html = client.get('/hub').get_data(as_text=True)
    assert 'bi-bell' not in html and 'Watch online' not in html and 'Directions' not in html


def test_quick_buttons_follow_hub_cards_and_are_not_repeated(client, db):
    html = client.get('/hub').get_data(as_text=True)
    grid = html.split('class="grid"')[1].split('</div>')[0]
    quick = html.split('class="quick"')[1].split('</div>')[0]
    for href in ('/prayer', '/connect', '/special-events', 'helpdesk'):
        assert href in quick
    for slug_target in ('href="/prayer"', 'href="/special-events"', 'href="/calendar"'):
        assert slug_target not in grid                  # already a quick button / Coming Up / Events tab
    assert 'Leadership' in grid and 'Ministries' in grid
    db.execute("UPDATE hub_cards SET is_active=0 WHERE slug='prayer-request'")
    db.commit()
    quick = client.get('/hub').get_data(as_text=True).split('class="quick"')[1].split('</div>')[0]
    assert '/prayer' not in quick                       # hiding the card in Hub Cards hides the button


def test_today_label_and_empty_notes(client, db):
    import datetime
    today = datetime.date.today().isoformat()
    db.execute("DELETE FROM sermon_notes WHERE note_date > ?", (today,))   # a future-dated note would be newest
    db.execute("INSERT INTO sermon_notes(title,note_date,summary,body_html) VALUES('Today Talk',?, 's','<p>x y z</p>')", (today,))
    db.commit()
    assert "TODAY&#39;S NOTES" in client.get('/hub').get_data(as_text=True)


def test_classic_layout_switch(client, db):
    _set(db, hub_layout='classic')
    html = client.get('/hub').get_data(as_text=True)
    assert 'Quick Access' in html and 'data-times=' not in html


def test_admin_saves_layout_and_times(admin, db):
    r = admin.post('/admin/hub-layout', {'hub_layout': 'classic', 'service_day': '0',
                                         'service_times': '11:30 AM, 8:30am, 10', 'service_starting_minutes': '3',
                                         'service_title': 'Join us'})
    assert r.status_code == 302
    got = dict(db.execute("SELECT key,value FROM settings WHERE key IN ('hub_layout','service_times',"
                          "'service_starting_minutes','service_title')").fetchall())
    assert got == {'hub_layout': 'classic', 'service_times': '08:30, 10:00, 11:30',
                   'service_starting_minutes': '3', 'service_title': 'Join us'}


def test_admin_rejects_bad_times(admin, db):
    admin.post('/admin/hub-layout', {'hub_layout': 'new', 'service_times': '8:30, lunchtime'})
    assert db.execute("SELECT value FROM settings WHERE key='service_times'").fetchone()[0] == '08:30, 10:00, 11:30'


def test_admin_page_renders(admin):
    html = admin.get('/admin/hub-layout').get_data(as_text=True)
    assert '8:30 AM, 10:00 AM, 11:30 AM' in html and 'Starting now' in html
