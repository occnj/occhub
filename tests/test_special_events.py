import pytest

import core
import routes_special_events
import schema


@pytest.fixture
def se_emails(monkeypatch):
    sent = []
    monkeypatch.setattr(routes_special_events, 'send_email',
                        lambda subject, to, html, reply_to=None: sent.append(
                            dict(subject=subject, to=to, html=html, reply_to=reply_to)) or True)
    return sent


def _set(db, **values):
    for k, v in values.items():
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    db.commit()


@pytest.fixture(autouse=True)
def defaults(db):
    _set(db, special_events_mode='form', special_events_email='phegel@oasisnj.net', special_events_1_enabled='1',
         special_events_1_name='Wedding', special_events_2_name='Funeral')
    yield


GOOD = {'services': ['Wedding'], 'first_name': 'Ann', 'last_name': 'Lee', 'email': 'ann@example.com',
        'phone': '732 555 0100', 'phone_type': 'Mobile', 'message': 'We would love to marry at Oasis.'}
EVENTS = ('Wedding', 'Funeral', 'Water Baptism', 'Baby Dedication', 'Pastoral Counseling', 'Church Membership')


def test_hub_shows_the_special_events_card(client):
    html = client.get('/hub').get_data(as_text=True)
    assert 'Special Events' in html and '/special-events' in html


def test_page_offers_the_same_six_life_events_as_church_center(client):
    html = client.get('/special-events').get_data(as_text=True)
    positions = [html.index(f'value="{name}"') for name in EVENTS]
    assert positions == sorted(positions)                    # same order as the Church Center form
    assert 'we at Oasis want you to know that we are here for you' in html


def test_a_switched_off_service_is_hidden_and_cannot_be_requested(client, db, se_emails):
    _set(db, special_events_1_enabled='0')
    assert 'value="Wedding"' not in client.get('/special-events').get_data(as_text=True)
    r = client.post('/special-events', GOOD)
    assert r.status_code == 400 and not se_emails


def test_request_is_saved_and_emailed_to_phegel_by_default(client, db, se_emails):
    r = client.post('/special-events', GOOD)
    assert r.status_code == 200 and 'Thank You, Ann' in r.get_data(as_text=True)
    row = db.execute("SELECT * FROM special_event_requests ORDER BY id DESC LIMIT 1").fetchone()
    assert (row['services'], row['first_name'], row['last_name'], row['phone'], row['phone_type'], row['status']) == \
        ('Wedding', 'Ann', 'Lee', '732 555 0100', 'Mobile', 'new')
    assert se_emails[0]['to'] == ['phegel@oasisnj.net']
    assert se_emails[0]['reply_to'] == 'ann@example.com'
    assert 'Wedding' in se_emails[0]['subject'] and 'Ann Lee' in se_emails[0]['subject']


def test_several_life_events_in_one_request(client, db, se_emails):
    client.post('/special-events', dict(GOOD, services=['Church Membership', 'Water Baptism', 'Made Up Event']))
    row = db.execute("SELECT services FROM special_event_requests ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == 'Water Baptism, Church Membership'      # page order kept, unknown values dropped
    assert 'Water Baptism, Church Membership' in se_emails[0]['html']


def test_email_content_is_escaped(client, se_emails):
    client.post('/special-events', dict(GOOD, first_name='<b>Ann</b>', message='<script>x</script>\nline 2'))
    html = se_emails[0]['html']
    assert '<script>' not in html and '&lt;script&gt;' in html and '<br>line 2' in html and '&lt;b&gt;Ann' in html


@pytest.mark.parametrize('change, why', [
    ({'services': []}, 'at least one life event'),
    ({'services': ['Bar Mitzvah']}, 'at least one life event'),
    ({'first_name': ''}, 'first and last name'),
    ({'last_name': ''}, 'first and last name'),
    ({'email': ''}, 'valid email'),
    ({'email': 'not-an-email'}, 'valid email'),
    ({'phone': ''}, 'phone number'),
    ({'phone': 'call me'}, 'phone number'),
])
def test_bad_requests_are_refused_and_keep_what_was_typed(client, se_emails, change, why):
    r = client.post('/special-events', dict(GOOD, **change))
    body = r.get_data(as_text=True)
    assert r.status_code == 400 and why in body and not se_emails
    assert 'We would love to marry at Oasis.' in body                      # their text is not lost


def test_unknown_phone_type_falls_back_to_mobile(client, db, se_emails):
    client.post('/special-events', dict(GOOD, phone_type='Pager'))
    assert db.execute("SELECT phone_type FROM special_event_requests ORDER BY id DESC LIMIT 1").fetchone()[0] == 'Mobile'


def test_admin_sets_where_requests_go(admin, client, db, se_emails):
    form = {'special_events_email': 'phegel@oasisnj.net, office@oasisnj.net', 'special_events_mode': 'form',
            'special_events_title': 'Special Events', 'special_events_1_enabled': '1', 'special_events_1_name': 'Wedding',
            'special_events_2_enabled': '1', 'special_events_2_name': 'Funeral'}
    assert admin.post('/admin/special-events', form).status_code == 302
    client.post('/special-events', GOOD)
    assert se_emails[-1]['to'] == ['phegel@oasisnj.net', 'office@oasisnj.net']


def test_admin_cannot_save_an_invalid_address(admin, db):
    admin.post('/admin/special-events', {'special_events_email': 'nobody'})
    assert db.execute("SELECT value FROM settings WHERE key='special_events_email'").fetchone()[0] == 'phegel@oasisnj.net'


def test_church_center_mode_links_out_and_ignores_posts(client, db, se_emails):
    _set(db, special_events_mode='link', special_events_form_url='https://oasisnj.churchcenter.com/people/forms/373934')
    html = client.get('/special-events').get_data(as_text=True)
    assert 'https://oasisnj.churchcenter.com/people/forms/373934' in html and 'name="first_name"' not in html
    before = db.execute("SELECT COUNT(*) FROM special_event_requests").fetchone()[0]
    assert client.post('/special-events', GOOD).status_code == 302
    assert db.execute("SELECT COUNT(*) FROM special_event_requests").fetchone()[0] == before and not se_emails


def test_admin_sees_handles_and_deletes_requests(admin, client, db, se_emails):
    client.post('/special-events', dict(GOOD, first_name='Zed', last_name='Requester'))
    rid = db.execute("SELECT id FROM special_event_requests WHERE last_name='Requester'").fetchone()[0]
    assert 'Zed Requester' in admin.get('/admin/special-events').get_data(as_text=True)
    admin.post(f'/admin/special-events/{rid}/status', {'status': 'handled'})
    assert db.execute("SELECT status FROM special_event_requests WHERE id=?", (rid,)).fetchone()[0] == 'handled'
    admin.post(f'/admin/special-events/{rid}/delete')
    assert db.execute("SELECT 1 FROM special_event_requests WHERE id=?", (rid,)).fetchone() is None


def test_admin_pages_need_login(client, db, se_emails):
    r = client.get('/admin/special-events')
    assert r.status_code == 302 and 'login' in r.headers['Location']
    client.post('/special-events', GOOD)
    rid = db.execute("SELECT MAX(id) FROM special_event_requests").fetchone()[0]
    r = client.post(f'/admin/special-events/{rid}/delete')
    assert r.status_code in (302, 403)                      # refused (login redirect or CSRF), never performed
    assert db.execute("SELECT 1 FROM special_event_requests WHERE id=?", (rid,)).fetchone() is not None


def test_requests_are_throttled(client, se_emails):
    codes = [client.post('/special-events', GOOD).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and 429 in codes[5:]


def test_existing_install_gets_the_card_once_and_admin_deletion_sticks(db):
    db.execute("DELETE FROM hub_cards WHERE slug='special-events'")
    db.execute("UPDATE settings SET value='custom@oasisnj.net' WHERE key='special_events_email'")
    db.execute("PRAGMA user_version = 1")                   # an install from before this feature
    db.commit()
    schema.init_db()
    assert db.execute("SELECT target_url FROM hub_cards WHERE slug='special-events'").fetchone()[0] == '/special-events'
    assert db.execute("SELECT value FROM settings WHERE key='special_events_email'").fetchone()[0] == 'custom@oasisnj.net'
    db.execute("DELETE FROM hub_cards WHERE slug='special-events'")
    db.commit()
    schema.init_db()                                        # later restarts don't bring it back
    assert db.execute("SELECT 1 FROM hub_cards WHERE slug='special-events'").fetchone() is None
    _restore_card(db)


def _restore_card(db):
    db.execute("PRAGMA user_version = 1")
    db.commit()
    schema.init_db()
