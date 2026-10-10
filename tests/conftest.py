"""Test setup: every test run gets a brand-new throwaway database (OCC_DB_PATH), so the real
oasis.db is never touched. Uploads that a test creates go to a temp dir via monkeypatching."""
import itertools
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix='occhub-test-')
os.environ.update(
    SECRET_KEY='test-secret',
    OCC_DB_PATH=os.path.join(_TMP, 'oasis.db'),
    PROXY_HOPS='1',
    RESEND_API_KEY='',
)

import member  # noqa: E402  (must come after the environment is set)
import core, routes_public, youtube, maintenance  # noqa: E402

ADMIN_PASSWORD = 'correct horse battery'
_ip_counter = itertools.count(1)


@pytest.fixture(scope='session')
def app():
    conn = core.get_db()
    conn.execute("UPDATE admin_users SET password=? WHERE username='admin'",
                 (core.hash_password(ADMIN_PASSWORD),))
    conn.commit()
    conn.close()
    return core.app


@pytest.fixture
def ip():
    """A fresh visitor address per test, so rate limits from one test never leak into another."""
    n = next(_ip_counter)
    return f'10.{n // 250}.{n % 250}.7'


class Client:
    """Test client that sends X-Forwarded-For like our proxy does, and handles the CSRF token."""
    def __init__(self, app, ip):
        self.c, self.ip = app.test_client(), ip
        with self.c.session_transaction() as s:
            s['_csrf'] = 'tok'

    def get(self, url, **kw):
        kw.setdefault('headers', {})['X-Forwarded-For'] = kw.pop('xff', self.ip)
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {}, csrf_token='tok')
        kw.setdefault('headers', {})['X-Forwarded-For'] = kw.pop('xff', self.ip)
        return self.c.post(url, data=data, **kw)

    def login(self, username='admin', password=ADMIN_PASSWORD):
        return self.post('/admin/login', {'username': username, 'password': password})


@pytest.fixture
def client(app, ip):
    return Client(app, ip)


@pytest.fixture
def admin(app, ip):
    c = Client(app, ip)
    assert c.login().status_code == 302
    return c


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(routes_public, 'send_email',
                        lambda subject, to, html, reply_to=None: sent.append(
                            dict(subject=subject, to=to, html=html, reply_to=reply_to)) or True)
    return sent


@pytest.fixture
def db():
    conn = core.get_db()
    yield conn
    conn.close()


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, 'UPLOAD_FOLDER', str(tmp_path))
    return tmp_path
