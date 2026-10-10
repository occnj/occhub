import core


def test_login_limit_ignores_spoofed_forwarded_for(client):
    # The proxy appends the real address (6.6.6.6); anything to its left is attacker-controlled.
    for i in range(6):
        r = client.post('/admin/login', {'username': 'admin', 'password': 'nope'}, xff=f'10.9.9.{i}, 6.6.6.6')
    assert 'Too many failed attempts' in r.get_data(as_text=True)


def test_login_limit_is_per_real_address(client):
    for i in range(6):
        client.post('/admin/login', {'username': 'admin', 'password': 'nope'}, xff='6.6.6.7')
    r = client.post('/admin/login', {'username': 'admin', 'password': 'nope'}, xff='7.7.7.8')
    assert 'Invalid username or password' in r.get_data(as_text=True)


def test_login_limit_lives_in_the_database(app, client):
    for _ in range(5):
        client.post('/admin/login', {'username': 'admin', 'password': 'nope'}, xff='6.6.6.8')
    assert len(core.rate_count('login', '6.6.6.8', 900)) == 5          # visible to any worker
    other_worker = app.test_client()
    with other_worker.session_transaction() as s:
        s['_csrf'] = 'tok'
    r = other_worker.post('/admin/login', data={'username': 'admin', 'password': 'nope', 'csrf_token': 'tok'},
                          headers={'X-Forwarded-For': '6.6.6.8'})
    assert 'Too many failed attempts' in r.get_data(as_text=True)


def test_honeypot_bans_the_real_address_not_a_spoofed_one(client, db):
    client.get('/.env', xff='8.8.8.8, 9.9.9.9')
    assert core.is_ip_banned('9.9.9.9')
    assert not core.is_ip_banned('8.8.8.8')
    db.execute("DELETE FROM banned_ips")
    db.commit()


def test_prayer_email_is_escaped(client, emails):
    client.post('/prayer', {'full_name': '<b>Bob</b>', 'email': 'a@b.com', 'request_type': 'Personal',
                            'message': '<img src=x onerror=alert(1)>\nsecond line'})
    html = emails[0]['html']
    assert '<img' not in html and '&lt;img' in html and '<br>second line' in html
    assert '\n' not in emails[0]['subject']


def test_connect_email_is_escaped(client, emails):
    r = client.post('/connect', {'full_name': '<script>x</script>', 'email': 'e', 'message': '<script>y</script>',
                                 'kg': ['<b>']})
    assert r.status_code == 302
    assert '<script>' not in emails[0]['html'] and '&lt;script&gt;' in emails[0]['html']


def test_contact_blocks_header_injection(client, emails):
    client.post('/contact', {'first_name': 'A\nBcc: x@y.z', 'last_name': '<i>', 'phone': '1',
                             'email': 'x@y.com\nBcc: evil@x.com', 'message': 'hi'})
    assert emails[0]['reply_to'] is None
    assert '\n' not in emails[0]['subject']
    assert '&lt;i&gt;' in emails[0]['html']


def test_contact_keeps_a_valid_reply_to(client, emails):
    client.post('/contact', {'first_name': 'A', 'last_name': 'B', 'email': 'ok@x.com', 'phone': '1', 'message': 'hi'})
    assert emails[0]['reply_to'] == 'ok@x.com'


def test_long_fields_are_clipped(client, emails, db):
    client.post('/prayer', {'full_name': 'x', 'message': 'a' * 20000})
    n = db.execute("SELECT length(message) FROM prayer_requests ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert n == 5000


def test_public_forms_are_throttled(client):
    codes = [client.post('/feedback', {'full_name': 'x', 'message': 'm', 'rating': '5'}).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and 429 in codes[5:]


def test_hub_notice_cannot_inject_script(client, db):
    for k, v in [('hub_notice_enabled', '1'), ('hub_notice_title', 'T'),
                 ('hub_notice_body', 'hello <script>alert(1)</script>\nline2')]:
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    db.commit()
    html = client.get('/hub').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;<br>line2' in html


def test_junk_sort_order_does_not_500(admin, db):
    assert admin.post('/admin/leaders/new', {'name': 'Junk', 'role': 'R', 'sort_order': 'abc'}).status_code == 302
    admin.post('/admin/leaders/new', {'name': 'Seven', 'role': 'R', 'sort_order': '7'})
    assert db.execute("SELECT sort_order FROM leaders WHERE name='Seven'").fetchone()[0] == 7


def test_csrf_token_is_not_saved_as_a_setting(admin, db):
    admin.post('/admin/settings', {'site_name': 'X'})
    assert db.execute("SELECT 1 FROM settings WHERE key='csrf_token'").fetchone() is None


def test_password_change_requires_current_password(app, db):
    from conftest import Client, ADMIN_PASSWORD
    db.execute("INSERT OR REPLACE INTO admin_users(username,password,role) VALUES('pwuser',?, 'editor')",
               (core.hash_password('first-password-1'),))
    db.commit()
    c = Client(app, '11.0.0.1')
    c.login('pwuser', 'first-password-1')

    def stored_ok(pw):
        row = db.execute("SELECT password FROM admin_users WHERE username='pwuser'").fetchone()
        return core.verify_password(row[0], pw)

    c.post('/admin/password', {'current_password': 'wrong', 'new_password': 'brand-new-password'})
    assert stored_ok('first-password-1')
    c.post('/admin/password', {'current_password': 'first-password-1', 'new_password': 'short'})
    assert stored_ok('first-password-1')
    c.post('/admin/password', {'current_password': 'first-password-1', 'new_password': 'brand-new-password'})
    assert stored_ok('brand-new-password')


def test_deleted_admin_loses_access_immediately(app, db):
    from conftest import Client
    db.execute("INSERT OR REPLACE INTO admin_users(username,password,role) VALUES('temp',?, 'editor')",
               (core.hash_password('temp-password-1'),))
    db.commit()
    c = Client(app, '11.0.0.2')
    c.login('temp', 'temp-password-1')
    assert c.get('/admin').status_code == 200
    db.execute("DELETE FROM admin_users WHERE username='temp'")
    db.commit()
    r = c.get('/admin')
    assert r.status_code == 302 and 'login' in r.headers['Location']


def test_admin_error_page_hides_the_exception(app, admin, monkeypatch):
    def boom():
        raise RuntimeError('SECRET_INTERNAL_DETAIL')
    monkeypatch.setattr(core, 'get_analytics_snapshot', lambda conn: boom())
    import routes_admin_core
    monkeypatch.setattr(routes_admin_core, 'get_analytics_snapshot', lambda conn: boom())
    app.config['PROPAGATE_EXCEPTIONS'] = False
    r = admin.get('/admin')
    assert r.status_code == 500 and 'SECRET_INTERNAL_DETAIL' not in r.get_data(as_text=True)


def test_upload_size_is_capped(app):
    assert app.config['MAX_CONTENT_LENGTH'] == 64 * 1024 * 1024
