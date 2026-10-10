import os
import sqlite3
import subprocess
import sys
import tempfile

import core
import schema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_init(db_path):
    code = "import schema; schema.init_db()"
    return subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(os.environ, OCC_DB_PATH=db_path, SECRET_KEY='x'))


def test_add_column_is_idempotent():
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE t (a TEXT)')
    schema.add_column(c, 't', 'b', "TEXT DEFAULT 'x'")
    schema.add_column(c, 't', 'b', "TEXT DEFAULT 'x'")
    assert [r[1] for r in c.execute('PRAGMA table_info(t)')] == ['a', 'b']


def test_schema_version_is_recorded(db):
    assert db.execute('PRAGMA user_version').fetchone()[0] == schema.SCHEMA_VERSION


def test_deleted_sample_content_stays_deleted(db):
    db.execute('DELETE FROM leaders')
    db.execute("DELETE FROM hub_cards WHERE slug='help-desk'")
    db.commit()
    schema.init_db()
    assert db.execute('SELECT COUNT(*) FROM leaders').fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM hub_cards WHERE slug='help-desk'").fetchone()[0] == 0


def test_admin_edits_to_builtin_hub_cards_survive_a_restart(db):
    db.execute("UPDATE hub_cards SET target_url='/custom', title='Custom' WHERE slug='leadership'")
    db.commit()
    schema.init_db()
    row = db.execute("SELECT target_url,title FROM hub_cards WHERE slug='leadership'").fetchone()
    assert tuple(row) == ('/custom', 'Custom')


def test_old_database_is_upgraded_in_place():
    path = os.path.join(tempfile.mkdtemp(), 'old.db')
    c = sqlite3.connect(path)
    # an old install: events/ministries tables from before several columns existed, one admin account
    c.executescript("""
        CREATE TABLE admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, role TEXT DEFAULT 'editor', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO admin_users(username,password,role) VALUES('admin','x','superadmin');
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '',
            event_date TEXT NOT NULL, event_time TEXT DEFAULT '', location TEXT DEFAULT '', sort_order INTEGER DEFAULT 0);
        INSERT INTO events(title,event_date) VALUES('Old event','2099-01-01');
    """)
    c.commit()
    c.close()
    p = _run_init(path)
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err.decode()
    c = sqlite3.connect(path)
    cols = {r[1] for r in c.execute('PRAGMA table_info(events)')}
    assert {'signup_url', 'auto_delete', 'recurrence', 'recurrence_detail'} <= cols
    assert c.execute("SELECT title, auto_delete FROM events").fetchone() == ('Old event', 1)
    assert c.execute('SELECT COUNT(*) FROM leaders').fetchone()[0] == 0       # existing install: no sample content
    assert c.execute('PRAGMA user_version').fetchone()[0] == schema.SCHEMA_VERSION


def test_two_workers_starting_together_create_one_admin():
    path = os.path.join(tempfile.mkdtemp(), 'fresh.db')
    procs = [_run_init(path) for _ in range(3)]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()
    c = sqlite3.connect(path)
    assert c.execute('SELECT COUNT(*) FROM admin_users').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM leaders').fetchone()[0] == 2        # seeded exactly once
