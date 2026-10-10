"""Shared foundation: Flask app + config, database and schema, settings, upload helpers,
security hooks (IP bans, CSRF, rate limits) and admin auth decorators."""
from flask import Flask, render_template, redirect, url_for, request, session, flash, send_from_directory
from datetime import datetime, date, timedelta
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import html
import json
import os, random, re, secrets, sqlite3, time, uuid, zipfile, zlib
from collections import Counter
from PIL import Image, ExifTags
from pillow_heif import register_heif_opener
register_heif_opener()
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import bleach
from markupsafe import Markup
import pdfplumber
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
    static_folder=os.path.join(BASE_DIR,'static'),
    template_folder=os.path.join(BASE_DIR,'templates'))
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY environment variable must be set')

# Number of reverse proxies in front of gunicorn (Tailscale funnel / Caddy = 1).
# ProxyFix then reads the client address from the Nth entry from the RIGHT of
# X-Forwarded-For, i.e. the one our own proxy appended. Entries to its left are
# supplied by the visitor and can be anything, so they are never trusted.
# Set PROXY_HOPS=0 when gunicorn is exposed directly.
PROXY_HOPS = int(os.environ.get('PROXY_HOPS', '1'))
if PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_HOPS)

UPLOAD_FOLDER = os.path.join(BASE_DIR,'static','uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','webp','gif','heic','heif'}
DOCUMENT_EXTENSIONS = {'pdf', 'docx'}

app.config.update(
    MAX_CONTENT_LENGTH=64 * 1024 * 1024,  # biggest legit request is a few photos / a sermon PDF
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=True,
)

@app.after_request
def _cache_static_assets(resp):
    # Uploaded images/docs get a random filename and are never overwritten,
    # so browsers can cache them indefinitely instead of revalidating every load.
    if request.path.startswith('/static/uploads/'):
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif request.path.startswith('/static/') and request.path not in ('/static/sw.js', '/static/manifest.json'):
        resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
MAIL_FROM = os.environ.get('MAIL_FROM', 'Oasis Hub <media@oasisnj.net>')

# OCC_DB_PATH lets the test suite use a throwaway database.
DB = os.environ.get('OCC_DB_PATH') or os.path.join(BASE_DIR,'oasis.db')

def hash_password(password):
    return generate_password_hash(password)

def password_is_hashed(value):
    return isinstance(value, str) and value.startswith(('pbkdf2:', 'scrypt:'))

def verify_password(stored_password, provided_password):
    if not stored_password:
        return False
    if password_is_hashed(stored_password):
        return check_password_hash(stored_password, provided_password)
    return stored_password == provided_password

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def sync_behind_scenes_with_ministries(conn):
    ministries = conn.execute(
        "SELECT id, name, description, photo, sort_order FROM ministries ORDER BY id"
    ).fetchall()
    ministry_ids = []
    for ministry in ministries:
        ministry_ids.append(ministry['id'])
        conn.execute(
            "INSERT OR IGNORE INTO behind_scenes (id,name,description,photo,sort_order) VALUES (?,?,?,?,?)",
            (ministry['id'], ministry['name'], ministry['description'], ministry['photo'] or '', ministry['sort_order'])
        )
        # only sync name/description/sort_order — photo is managed independently in Oasis Crew admin
        conn.execute(
            "UPDATE behind_scenes SET name=?,description=?,sort_order=? WHERE id=?",
            (ministry['name'], ministry['description'], ministry['sort_order'], ministry['id'])
        )
    if ministry_ids:
        placeholders = ",".join("?" for _ in ministry_ids)
        conn.execute(f"DELETE FROM behind_scenes WHERE id NOT IN ({placeholders})", ministry_ids)
    else:
        conn.execute("DELETE FROM behind_scenes")

def get_setting(key, default=''):
    try:
        conn=get_db(); r=conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); conn.close()
        return r['value'] if r else default
    except: return default

def all_settings():
    try:
        conn=get_db(); rows=conn.execute("SELECT key,value FROM settings").fetchall(); conn.close()
        return {r['key']:r['value'] for r in rows}
    except: return {}

# Hub card colors: (setting key, label, fallback theme key, fallback color).
# An unset color follows the site theme, so existing installs look the same.
HUB_COLOR_GROUPS = [
    ("Today's Message", [
        ('hub_msg_bg', 'Card', 'theme_primary', '#13677A'),
        ('hub_msg_title', 'Title', None, '#ffffff'),
        ('hub_msg_text', 'Description', None, '#ffffff'),
    ]),
    ('Get Involved', [
        ('hub_gi_bg', 'Card', 'theme_tile_bg', '#FFFFFB'),
        ('hub_gi_title', 'Title', 'theme_text', '#1d1d1f'),
        ('hub_gi_text', 'Description', 'theme_subtext', '#6e6e73'),
        ('hub_gi_accent', 'Icon & Button', 'theme_accent', '#F2541B'),
    ]),
    ('Quick Access', [
        ('hub_qa_bg', 'Card', 'theme_tile_bg', '#FFFFFB'),
        ('hub_qa_title', 'Title', 'theme_text', '#1d1d1f'),
        ('hub_qa_text', 'Description', 'theme_subtext', '#6e6e73'),
        ('hub_qa_icon', 'Icon', 'theme_primary', '#13677A'),
    ]),
]

def hub_colors(settings):
    colors = {}
    for _, fields in HUB_COLOR_GROUPS:
        for key, _, theme_key, default in fields:
            colors[key] = (settings.get(key) or (settings.get(theme_key) if theme_key else '') or default).strip()
    return colors

@app.context_processor
def inject_site_settings():
    settings = all_settings()
    return {'site_settings': settings, 'hub_colors': hub_colors(settings),
            'hub_color_groups': HUB_COLOR_GROUPS}


def allowed_file(f): return '.'in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_document(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in DOCUMENT_EXTENSIONS

_EXIF_ORIENTATION = next(
    (k for k, v in ExifTags.TAGS.items() if v == 'Orientation'), None
)
_EXIF_TRANSPOSE = {
    2: Image.FLIP_LEFT_RIGHT,
    3: Image.ROTATE_180,
    4: Image.FLIP_TOP_BOTTOM,
    5: Image.TRANSPOSE,
    6: Image.ROTATE_270,
    7: Image.TRANSVERSE,
    8: Image.ROTATE_90,
}
MAX_IMAGE_PX = 1400
MAX_BEYOND_WALL_IMAGES = 100

def _save_file_obj(f):
    try:
        if not f or not f.filename:
            return None
        if not allowed_file(f.filename):
            app.logger.warning(f"_save_file_obj: rejected '{f.filename}'")
            return None
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        stem = uuid.uuid4().hex[:8]
        fname = f"{stem}.jpg"
        dest = os.path.join(UPLOAD_FOLDER, fname)

        img = Image.open(f.stream)
        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)

        # fix EXIF orientation before anything else
        try:
            exif = img._getexif()
            if exif and _EXIF_ORIENTATION:
                op = _EXIF_TRANSPOSE.get(exif.get(_EXIF_ORIENTATION))
                if op:
                    img = img.transpose(op)
        except Exception:
            pass

        # resize so longest edge <= MAX_IMAGE_PX
        w, h = img.size
        if max(w, h) > MAX_IMAGE_PX:
            scale = MAX_IMAGE_PX / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        if has_alpha:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            fname = f"{stem}.png"
            dest = os.path.join(UPLOAD_FOLDER, fname)
            img.save(dest, 'PNG', optimize=True)
        else:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(dest, 'JPEG', quality=82, optimize=True)

        app.logger.info(f"_save_file_obj: saved {dest}")
        return 'uploads/' + fname
    except Exception as e:
        app.logger.error(f"_save_file_obj error: {e}")
        return None

def save_upload(field):
    return _save_file_obj(request.files.get(field))

def save_document_upload(field):
    return save_document_file(request.files.get(field))

def save_document_file(f):
    try:
        if not f or not f.filename:
            return None, None
        if not allowed_document(f.filename):
            app.logger.warning(f"save_document_file: rejected file type '{f.filename}'")
            return None, None
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        original_name = secure_filename(f.filename)
        stem, ext = os.path.splitext(original_name)
        fname = secure_filename(f"{stem}_{uuid.uuid4().hex[:8]}{ext.lower()}")
        dest = os.path.join(UPLOAD_FOLDER, fname)
        f.save(dest)
        app.logger.info(f"save_document_file: saved {dest}")
        return 'uploads/' + fname, ext.lower().lstrip('.')
    except Exception as e:
        app.logger.error(f"save_document_file error ({getattr(f, 'filename', '?')}): {e}")
        return None, None


def client_ip():
    # remote_addr is already the real client address: ProxyFix (see PROXY_HOPS)
    # replaced it with the entry our own proxy appended. Never read
    # X-Forwarded-For / X-Real-IP here, a visitor can set those to anything.
    return request.remote_addr or '0.0.0.0'

def track(page):
    try:
        # Never track admin sessions
        if session.get('admin_logged_in'):
            return
        # Skip service-worker precache fetches (not real visits)
        if request.headers.get('X-SW-Precache'):
            return
        sid = session.get('sid')
        if not sid:
            sid = str(uuid.uuid4())[:16]
            session['sid'] = sid
        session.permanent = True
        # Store timestamp as local date string for correct "today" queries
        now = datetime.now()
        ts = now.strftime('%Y-%m-%d %H:%M:%S')
        today = now.strftime('%Y-%m-%d')
        session['today'] = today  # persist so queries can use it
        conn = get_db()
        # Dedupe: same device reloading the same page within the last hour
        # doesn't count as a new view (avoids refresh-inflated counts).
        hour_ago = (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        dup = conn.execute(
            "SELECT 1 FROM analytics WHERE sid=? AND page=? AND ts>=? LIMIT 1",
            (sid, page, hour_ago)
        ).fetchone()
        if not dup:
            conn.execute("INSERT INTO analytics (ts,page,ip,ua,sid) VALUES (?,?,?,?,?)",
                (ts, page, client_ip(), request.headers.get('User-Agent','')[:200], sid))
            conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"track() error: {e}")

def send_email(subject, to, html, reply_to=None):
    try:
        if not RESEND_API_KEY:
            app.logger.warning("Email skipped: RESEND_API_KEY not set")
            return False
        payload = {
            'from': MAIL_FROM,
            'to': [to] if isinstance(to, str) else list(to),
            'subject': subject,
            'html': html,
        }
        if reply_to: payload['reply_to'] = reply_to
        req = Request('https://api.resend.com/emails',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Authorization': f'Bearer {RESEND_API_KEY}',
                     'Content-Type': 'application/json',
                     'User-Agent': 'occ-hub/1.0'},
            method='POST')
        with urlopen(req, timeout=15) as response:
            response.read()
        return True
    except Exception as e:
        app.logger.warning(f"Email failed: {e}"); return False

def assignment_slots_from_form(form):
    slots = []
    seen_scene_ids = set()
    for slot_order in range(1, 4):
        raw_scene_id = (form.get(f'assignment_{slot_order}_scene_id') or '').strip()
        if not raw_scene_id:
            continue
        try:
            scene_id = int(raw_scene_id)
        except ValueError:
            continue
        if scene_id in seen_scene_ids:
            continue
        seen_scene_ids.add(scene_id)
        slots.append({
            'scene_id': scene_id,
            'role': form.get(f'assignment_{slot_order}_role', '').strip(),
            'slot_order': slot_order,
        })
    return slots

def get_scene_choices(conn):
    sync_behind_scenes_with_ministries(conn)
    return conn.execute("SELECT id,name FROM ministries ORDER BY sort_order, name").fetchall()

def get_people_count_by_scene(conn):
    return {
        row['scene_id']: row['person_count']
        for row in conn.execute(
            "SELECT scene_id, COUNT(*) AS person_count FROM behind_scene_assignments GROUP BY scene_id"
        ).fetchall()
    }

def get_crew_people(conn):
    people = conn.execute("SELECT * FROM behind_scene_people ORDER BY LOWER(name), sort_order, id").fetchall()
    assignments = conn.execute(
        """
        SELECT a.*, s.name AS scene_name
        FROM behind_scene_assignments a
        JOIN behind_scenes s ON s.id = a.scene_id
        ORDER BY a.slot_order, a.id
        """
    ).fetchall()
    grouped = {}
    for row in assignments:
        grouped.setdefault(row['person_id'], []).append(row)
    items = []
    for person in people:
        slots = grouped.get(person['id'], [])
        items.append({
            'person': person,
            'assignments': slots,
            'assignment_labels': [f"{slot['scene_name']}" + (f" - {slot['role']}" if slot['role'] else "") for slot in slots],
        })
    return items

def next_available_sort_order(conn, table_name, column_name='sort_order'):
    rows = conn.execute(f"SELECT COALESCE({column_name}, 0) AS sort_order FROM {table_name} ORDER BY {column_name}, id").fetchall()
    used = {int(row['sort_order']) for row in rows if row['sort_order'] is not None and int(row['sort_order']) > 0}
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate

def next_available_sort_order_for_parent(conn, table_name, parent_col, parent_id, column_name='sort_order'):
    rows = conn.execute(
        f"SELECT COALESCE({column_name}, 0) AS sort_order FROM {table_name} WHERE {parent_col}=? ORDER BY {column_name}, id",
        (parent_id,)
    ).fetchall()
    used = {int(row['sort_order']) for row in rows if row['sort_order'] is not None and int(row['sort_order']) > 0}
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate

@app.context_processor
def inject_next_sort_order():
    def next_sort_order(table_name):
        conn = get_db()
        try:
            return next_available_sort_order(conn, table_name)
        finally:
            conn.close()
    return dict(next_sort_order=next_sort_order)

def get_people_for_scene(conn, scene_id):
    return conn.execute(
        """
        SELECT
            p.id,
            p.name,
            p.photo,
            p.sort_order,
            a.role,
            a.slot_order
        FROM behind_scene_assignments a
        JOIN behind_scene_people p ON p.id = a.person_id
        WHERE a.scene_id=?
        ORDER BY p.sort_order, p.name
        """,
        (scene_id,)
    ).fetchall()

def get_person_with_assignments(conn, person_id):
    person = conn.execute("SELECT * FROM behind_scene_people WHERE id=?", (person_id,)).fetchone()
    if not person:
        return None, []
    assignments = conn.execute(
        "SELECT * FROM behind_scene_assignments WHERE person_id=? ORDER BY slot_order, id",
        (person_id,)
    ).fetchall()
    return person, assignments

def replace_person_assignments(conn, person_id, slots):
    conn.execute("DELETE FROM behind_scene_assignments WHERE person_id=?", (person_id,))
    for slot in slots:
        conn.execute(
            "INSERT INTO behind_scene_assignments (person_id,scene_id,role,slot_order) VALUES (?,?,?,?)",
            (person_id, slot['scene_id'], slot['role'], slot['slot_order'])
        )

def get_mission_cover(conn, mission_id):
    cover = conn.execute("SELECT cover_photo FROM missions WHERE id=?", (mission_id,)).fetchone()
    if cover and cover['cover_photo']:
        return cover['cover_photo']
    first_image = conn.execute(
        "SELECT photo FROM mission_images WHERE mission_id=? ORDER BY sort_order, id LIMIT 1",
        (mission_id,)
    ).fetchone()
    return first_image['photo'] if first_image else ''

def get_mission_cards(conn):
    missions = conn.execute("SELECT * FROM missions ORDER BY sort_order, id DESC").fetchall()
    cards = []
    for mission in missions:
        cards.append({
            'id': mission['id'],
            'title': mission['title'],
            'summary': mission['summary'],
            'cover_photo': get_mission_cover(conn, mission['id']),
            'image_count': conn.execute(
                "SELECT COUNT(*) FROM mission_images WHERE mission_id=?",
                (mission['id'],)
            ).fetchone()[0],
        })
    return cards

def get_beyond_wall_cover(conn, beyond_id):
    cover = conn.execute("SELECT cover_photo FROM beyond_walls WHERE id=?", (beyond_id,)).fetchone()
    if cover and cover['cover_photo']:
        return cover['cover_photo']
    first_image = conn.execute(
        "SELECT photo FROM beyond_wall_images WHERE beyond_id=? ORDER BY sort_order, id LIMIT 1",
        (beyond_id,)
    ).fetchone()
    return first_image['photo'] if first_image else ''

def get_beyond_wall_cards(conn):
    items = conn.execute("SELECT * FROM beyond_walls ORDER BY sort_order, id DESC").fetchall()
    cards = []
    for item in items:
        cards.append({
            'id': item['id'],
            'title': item['title'],
            'summary': item['summary'],
            'cover_photo': get_beyond_wall_cover(conn, item['id']),
            'image_count': conn.execute(
                "SELECT COUNT(*) FROM beyond_wall_images WHERE beyond_id=?",
                (item['id'],)
            ).fetchone()[0],
        })
    return cards

def get_gallery_images(conn, table_name, ref_col, ref_id):
    return conn.execute(
        f"SELECT * FROM {table_name} WHERE {ref_col}=? ORDER BY sort_order, id",
        (ref_id,)
    ).fetchall()

def gallery_images_by_parent(rows, parent_key):
    grouped = {}
    for row in rows:
        grouped.setdefault(row[parent_key], []).append({
            'id': row['id'],
            'photo': row['photo'],
            'caption': row['caption'] or '',
            'sort_order': row['sort_order'],
        })
    return grouped

def get_hub_notice(settings=None):
    settings = settings or all_settings()
    return {
        'enabled': (settings.get('hub_notice_enabled') or '0') == '1',
        'title': (settings.get('hub_notice_title') or '').strip(),
        'body': (settings.get('hub_notice_body') or '').strip(),
        'link': (settings.get('hub_notice_link') or '').strip(),
        'link_label': (settings.get('hub_notice_link_label') or 'Learn More').strip() or 'Learn More',
        'image': (settings.get('hub_notice_image') or '').strip(),
    }

GET_INVOLVED_SLOTS = 5

def get_involved_key(slot, field):
    # Slot 1 keeps the original single-card keys so existing content carries over.
    return f'get_involved_{field}' if slot == 1 else f'get_involved{slot}_{field}'

def get_get_involved(settings=None):
    settings = settings or all_settings()
    cards = []
    for slot in range(1, GET_INVOLVED_SLOTS + 1):
        val = lambda field, default='': (settings.get(get_involved_key(slot, field)) or default).strip() or default
        card = {
            'slot': slot,
            'enabled': val('enabled', '0') == '1',
            'title': val('title'),
            'body': val('body'),
            'link': val('link'),
            'link_label': val('link_label', 'Sign Up'),
            'icon': val('icon', 'bi-people-fill'),
        }
        if card['enabled'] and (card['title'] or card['body']):
            cards.append(card)
    layout = settings.get('get_involved_layout') or 'stack'
    return {'cards': cards, 'layout': layout if layout in ('row', 'stack') else 'stack'}

def get_hub_cards(conn, parent_id=None):
    if parent_id is None:
        return conn.execute(
            "SELECT * FROM hub_cards WHERE is_active=1 AND parent_id IS NULL ORDER BY sort_order, id"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM hub_cards WHERE is_active=1 AND parent_id=? ORDER BY sort_order, id",
        (parent_id,)
    ).fetchall()

def analytics_window_start(days=0):
    base_day = datetime.now().date() - timedelta(days=days)
    return f"{base_day.isoformat()} 00:00:00"

def get_analytics_snapshot(conn):
    today_start = analytics_window_start(0)
    week_start = analytics_window_start(6)
    return {
        'total_views': conn.execute("SELECT COUNT(*) FROM analytics").fetchone()[0],
        'views_today': conn.execute("SELECT COUNT(*) FROM analytics WHERE ts >= ?", (today_start,)).fetchone()[0],
        'views_week': conn.execute("SELECT COUNT(*) FROM analytics WHERE ts >= ?", (week_start,)).fetchone()[0],
        'sessions_total': conn.execute("SELECT COUNT(DISTINCT sid) FROM analytics").fetchone()[0],
        'sessions_today': conn.execute("SELECT COUNT(DISTINCT sid) FROM analytics WHERE ts >= ?", (today_start,)).fetchone()[0],
        'sessions_week': conn.execute("SELECT COUNT(DISTINCT sid) FROM analytics WHERE ts >= ?", (week_start,)).fetchone()[0],
    }

def ensure_admin_password_hash(conn, user_row, raw_password):
    if password_is_hashed(user_row['password']):
        return
    conn.execute(
        "UPDATE admin_users SET password=? WHERE id=?",
        (hash_password(raw_password), user_row['id'])
    )
    conn.commit()

# ── IP bans & honeypot ────────────────────────────────────────────────────────

BAN_DAYS = 7
HONEYPOT_PATHS = [
    '/wp-admin', '/wp-login.php', '/wp-config.php', '/wp-content/uploads',
    '/xmlrpc.php', '/.env', '/.env.bak', '/.env.production',
    '/.git/config', '/.aws/credentials', '/.ssh/id_rsa',
    '/phpmyadmin', '/phpMyAdmin', '/pma', '/admin.php', '/administrator',
    '/administrator/index.php', '/config.php', '/config.json.bak',
    '/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php',
    '/.docker/config.json', '/server-status', '/actuator/env',
]

def is_ip_banned(ip):
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT 1 FROM banned_ips WHERE ip=? AND expires_at > ?",
            (ip, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception as e:
        app.logger.warning(f"is_ip_banned() error: {e}")
        return False

def ban_ip(ip, reason=''):
    try:
        now = datetime.now()
        expires = now + timedelta(days=BAN_DAYS)
        conn = get_db()
        conn.execute(
            "INSERT INTO banned_ips (ip,banned_at,expires_at,reason) VALUES (?,?,?,?) "
            "ON CONFLICT(ip) DO UPDATE SET banned_at=excluded.banned_at, "
            "expires_at=excluded.expires_at, reason=excluded.reason",
            (ip, now.strftime('%Y-%m-%d %H:%M:%S'), expires.strftime('%Y-%m-%d %H:%M:%S'), reason)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"ban_ip() error: {e}")

@app.before_request
def block_banned_ips():
    if is_ip_banned(client_ip()):
        from flask import abort
        abort(403)

def _honeypot_hit():
    ban_ip(client_ip(), reason=f'honeypot:{request.path}')
    app.logger.warning(f"Honeypot tripped by {client_ip()}: {request.path}")
    return ('Not Found', 404)

for _i, _path in enumerate(HONEYPOT_PATHS):
    app.add_url_rule(_path, endpoint=f'honeypot_{_i}', view_func=_honeypot_hit)

# ── CSRF ─────────────────────────────────────────────────────────────────────

def get_csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(32)
    return session['_csrf']

app.jinja_env.globals['csrf_token'] = get_csrf_token

@app.before_request
def csrf_protect():
    if request.method == 'POST' and request.path.startswith('/admin'):
        token = request.form.get('csrf_token', '')
        if not token or token != session.get('_csrf'):
            from flask import abort
            abort(403)

# ── Rate limiting (stored in SQLite so all workers share it) ─────────────────

_LOGIN_MAX = 5
_LOGIN_WINDOW = 900  # 15 minutes

def rate_count(bucket, ip, window):
    now = time.time()
    conn = get_db()
    try:
        conn.execute("DELETE FROM rate_events WHERE ts < ?", (now - 86400,))
        rows = conn.execute(
            "SELECT ts FROM rate_events WHERE bucket=? AND ip=? AND ts>=? ORDER BY ts",
            (bucket, ip, now - window)
        ).fetchall()
        conn.commit()
        return [r['ts'] for r in rows]
    finally:
        conn.close()

def rate_record(bucket, ip):
    conn = get_db()
    try:
        conn.execute("INSERT INTO rate_events (bucket,ip,ts) VALUES (?,?,?)", (bucket, ip, time.time()))
        conn.commit()
    finally:
        conn.close()

def rate_clear(bucket, ip):
    conn = get_db()
    try:
        conn.execute("DELETE FROM rate_events WHERE bucket=? AND ip=?", (bucket, ip))
        conn.commit()
    finally:
        conn.close()

def _check_rate_limit(ip):
    attempts = rate_count('login', ip, _LOGIN_WINDOW)
    if len(attempts) >= _LOGIN_MAX:
        return False, int(_LOGIN_WINDOW - (time.time() - attempts[0]))
    return True, 0

def _record_failed_login(ip):
    rate_record('login', ip)

def _clear_login_attempts(ip):
    rate_clear('login', ip)

def form_rate_limited(bucket, max_events=5, window=600):
    """Throttle a public form per visitor. Counts every POST, returns True when over."""
    ip = client_ip()
    try:
        if len(rate_count(bucket, ip, window)) >= max_events:
            return True
        rate_record(bucket, ip)
    except Exception as e:
        app.logger.warning(f"form_rate_limited() error: {e}")  # never block a visitor on a limiter fault
    return False

def clip(value, limit=2000):
    """Trim visitor-supplied text so a single field can't be megabytes long."""
    return (value or '')[:limit]

def one_line(value, limit=200):
    """For anything that ends up in an email subject: no line breaks, bounded length."""
    return re.sub(r'[\r\n]+', ' ', value or '').strip()[:limit]

def esc(value):
    """Escape visitor-supplied text before it goes into an email's HTML body."""
    return html.escape(str(value or ''))

def form_sort_order():
    """The admin's sort_order box as an int, or None when it is blank or not a number."""
    return int_or(request.form.get('sort_order'), None)

def int_or(value, default=0):
    """int() for form fields: a blank or non-numeric value falls back instead of 500ing."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default

# ── Auth decorators ───────────────────────────────────────────────────────────

def current_admin_still_valid():
    """A deleted (or renamed) account must stop working at once, not when its cookie expires.
    Also picks up a role change."""
    conn = get_db()
    try:
        row = conn.execute("SELECT role FROM admin_users WHERE username=?", (session.get('admin_user'),)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    session['admin_role'] = row['role']
    return True

def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get('admin_logged_in') or not current_admin_still_valid():
            session.clear()
            return redirect(url_for('admin_login'))
        return f(*a,**kw)
    return dec

def superadmin_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get('admin_logged_in') or not current_admin_still_valid():
            session.clear()
            return redirect(url_for('admin_login'))
        if session.get('admin_role')!='superadmin':
            flash('Superadmin only.','info'); return redirect(url_for('admin_dashboard'))
        return f(*a,**kw)
    return dec



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]
