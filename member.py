from flask import Flask, render_template, redirect, url_for, request, session, flash, send_from_directory
from datetime import datetime, date, timedelta
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
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

UPLOAD_FOLDER = os.path.join(BASE_DIR,'static','uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','webp','gif','heic','heif'}
DOCUMENT_EXTENSIONS = {'pdf', 'docx'}

app.config.update(
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

DB = os.path.join(BASE_DIR,'oasis.db')

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

def init_db():
    conn = get_db(); c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'editor',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    existing = c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if existing == 0:
        first_password = secrets.token_urlsafe(16)
        c.execute(
            "INSERT INTO admin_users (username,password,role) VALUES (?,?,?)",
            ('admin', hash_password(first_password), 'superadmin')
        )
        print(f"\n[FIRST RUN] Admin account created — username: admin  password: {first_password}\n", flush=True)

    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    for k,v in [
        ('site_name','Oasis Hub'),
        ('tagline','Know God · Find Hope · Make a Difference'),
        ('phone','7324990040'),
        ('theme_primary','#13677A'),
        ('theme_accent','#F2541B'),
        ('theme_background','#F6EFE4'),
        ('theme_card','#ffffff'),
        ('theme_tile_bg','#FFFFFB'),
        ('theme_text','#1d1d1f'),
        ('theme_subtext','#6e6e73'),
        ('ui_background_image',''),
        ('instagram_url','https://www.instagram.com/occnj/'),
        ('facebook_url','https://www.facebook.com/occnj/'),
        ('give_url','https://thekingdomledger.com/donate?code=2335'),
        ('serve_form_url','https://oasisnj.churchcenter.com/people/forms/339396'),
        ('sermon_url','#'),
        ('yousaidyes_url','https://drive.google.com/file/d/1nc29sDRO2Q5ijcWGRv-3HHpXt_I4bF7t/view?usp=sharing'),
        ('connect_email','media@oasisnj.net'),
        ('contact_email','Oasis@OasisNJ.net'),
        ('prayer_email','Oasis@OasisNJ.net'),
        ('logo_path',''),
        ('about_title','About Oasis'),
        ('about_page_description','Get to know the heart, story, and vision behind Oasis.'),
        ('about_hero',''),
        ('about_body','Oasis Christian Centre is a multicultural, non-denominational church in Rahway, NJ. We exist to help people Know God, Find Hope, and Make a Difference.'),
        ('beliefs_page_description','The heart, truth, and biblical foundation that shape who we are as a church.'),
        ('values_page_description','The culture, convictions, and everyday posture that shape how we live out our faith together.'),
        ('calendar_page_description','See what is coming up at Oasis and make room for the moments that matter.'),
        ('connect_page_description','Tell us a little about yourself so we can help you take your next step.'),
        ('contact_page_description','We would love to hear from you and help you get connected.'),
        ('leadership_page_description','Meet the pastors and leaders helping guide the vision of Oasis.'),
        ('ministries_page_description','Explore the ministries where people of every age can belong, grow, and serve.'),
        ('prayer_page_description','Share what is on your heart and let us stand with you in prayer.'),
        ('feedback_page_description','Loving the app? Spotted something off? Tell us — your feedback shapes what we build next.'),
        ('serve_page_description','Find your place, use your gifts, and make a difference with us.'),
        ('social_page_title','Follow Along'),
        ('social_page_description','Stay connected with Oasis through every platform and every message.'),
        ('social_youtube_url','https://www.youtube.com/channel/UCR4FqPSfjQAGy6jZB7OJ76w'),
        ('social_twitter_url',''),
        ('social_tiktok_url',''),
        ('sermon_channel_url',''),
        ('watch_page_description','Stay close to what God is saying at Oasis with the latest messages, moments, and live experiences all in one place.'),
        ('sermon_notes_page_description','Catch the latest sermon notes in a clean reading format built for your phone.'),
        ('crew_page_description','Meet the teams who make the experience happen long before and after the lights come on.'),
        ('mission_page_description','Stories from the field, moments that matter, and the lives being touched through every mission.'),
        ('beyond_walls_page_description','Stories, impact, and moments from outreach beyond our Sunday walls.'),
        ('hub_notice_enabled','0'),
        ('hub_notice_title',''),
        ('hub_notice_body',''),
        ('hub_notice_link',''),
        ('hub_notice_link_label','Learn More'),
        ('hub_notice_image',''),
        ('get_involved_enabled','0'),
        ('get_involved_title',''),
        ('get_involved_body',''),
        ('get_involved_link',''),
        ('get_involved_link_label','Sign Up'),
        ('get_involved_image',''),
        ('primary_cta_enabled','1'),
        ('primary_cta_label','Give'),
        ('primary_cta_icon','bi-heart-fill'),
        ('primary_cta_url','https://thekingdomledger.com/donate?code=2335'),
    ]:
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)",(k,v))
    for slot in range(1, 11):
        c.execute(
            "INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)",
            (f'sermon_video_{slot}_title', '')
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)",
            (f'sermon_video_{slot}_url', '')
        )

    c.execute('''CREATE TABLE IF NOT EXISTS leaders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        role TEXT NOT NULL, bio TEXT DEFAULT '', photo TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
    )''')
    c.execute("SELECT COUNT(*) FROM leaders")
    if c.fetchone()[0]==0:
        c.executemany("INSERT INTO leaders (name,role,bio,photo,sort_order) VALUES (?,?,?,?,?)",[
            ('John Smith','Senior Pastor','Pastor John has been serving at Oasis for over 15 years. He has a heart for the city and a passion for teaching the Word of God in a way that is practical for everyday life.','',1),
            ('Jane Doe','Worship Director','Jane leads our worship teams with a passion for creating an atmosphere where people can encounter God. She is also a songwriter and mentor to many young musicians.','',2),
        ])

    c.execute('''CREATE TABLE IF NOT EXISTS behind_scenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        photo TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS behind_scene_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scene_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        role TEXT DEFAULT '',
        bio TEXT DEFAULT '',
        photo TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(scene_id) REFERENCES behind_scenes(id) ON DELETE CASCADE
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS behind_scene_people (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        bio TEXT DEFAULT '',
        photo TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS behind_scene_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER NOT NULL,
        scene_id INTEGER NOT NULL,
        role TEXT DEFAULT '',
        slot_order INTEGER DEFAULT 0,
        UNIQUE(person_id, scene_id),
        FOREIGN KEY(person_id) REFERENCES behind_scene_people(id) ON DELETE CASCADE,
        FOREIGN KEY(scene_id) REFERENCES behind_scenes(id) ON DELETE CASCADE
    )''')
    legacy_member_count = c.execute("SELECT COUNT(*) FROM behind_scene_members").fetchone()[0]
    people_count = c.execute("SELECT COUNT(*) FROM behind_scene_people").fetchone()[0]
    assignment_count = c.execute("SELECT COUNT(*) FROM behind_scene_assignments").fetchone()[0]
    if legacy_member_count > 0 and people_count == 0 and assignment_count == 0:
        legacy_members = c.execute("SELECT * FROM behind_scene_members ORDER BY id").fetchall()
        for member in legacy_members:
            c.execute(
                "INSERT INTO behind_scene_people (name,bio,photo,sort_order) VALUES (?,?,?,?)",
                (member['name'], member['bio'], member['photo'], member['sort_order'])
            )
            person_id = c.lastrowid
            c.execute(
                "INSERT INTO behind_scene_assignments (person_id,scene_id,role,slot_order) VALUES (?,?,?,?)",
                (person_id, member['scene_id'], member['role'], 1)
            )

    c.execute('''CREATE TABLE IF NOT EXISTS missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        summary TEXT DEFAULT '',
        body TEXT DEFAULT '',
        cover_photo TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS mission_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id INTEGER NOT NULL,
        photo TEXT NOT NULL,
        caption TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS beyond_walls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        summary TEXT DEFAULT '',
        body TEXT DEFAULT '',
        cover_photo TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        youtube_video_id TEXT DEFAULT ''
    )''')
    try:
        c.execute("ALTER TABLE beyond_walls ADD COLUMN youtube_video_id TEXT DEFAULT ''")
    except Exception:
        pass
    c.execute('''CREATE TABLE IF NOT EXISTS beyond_wall_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        beyond_id INTEGER NOT NULL,
        photo TEXT NOT NULL,
        caption TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(beyond_id) REFERENCES beyond_walls(id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS beliefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        body TEXT NOT NULL, scripture TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS belief_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        belief_id INTEGER NOT NULL,
        photo TEXT NOT NULL,
        caption TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(belief_id) REFERENCES beliefs(id) ON DELETE CASCADE
    )''')
    c.execute("SELECT COUNT(*) FROM beliefs")
    if c.fetchone()[0]==0:
        c.executemany("INSERT INTO beliefs (title,body,scripture,sort_order) VALUES (?,?,?,?)",[
            ('What We Believe','We believe the Bible, composed of the sixty-six books of the Old and New Testaments, to be the inspired (God-breathed) Word of God (2 Timothy 3:16). It is the final authority for all matters of faith and practice.\n\nIn essential beliefs — we have unity. In nonessential beliefs — we have liberty. In all our beliefs — we exhibit charity.','Genesis 1:1 · Deuteronomy 6:4 · 1 Timothy 2:5 · Psalm 90:2',1),
            ('God','There is only one, true God, eternally existing in three distinct personalities: the Father, the Son, and the Holy Spirit. These three are one God, having the same nature, attributes, and perfections, and are therefore worthy of the same worship and obedience.','Genesis 1:1, 26-27 · Deuteronomy 6:4 · Matthew 28:19',2),
            ('The Holy Spirit','The Holy Spirit is coequal with the Father and the Son. He is Creator, and is present in the world to convict people of their sin. People are regenerated by the Holy Spirit, and He lives in believers to manifest the character of Christ.','Genesis 1:2 · John 14:16-17 · Acts 1:8 · Galatians 5:22-25',3),
            ('Jesus Christ','Jesus Christ is the Son of God, coequal with the Father. He became a man, born of the virgin Mary, and lived a sinless life. He offered Himself as the perfect sacrifice by dying on a cross. He rose bodily from the dead after three days.','John 1:1-5,14 · Colossians 1:15-20 · Philippians 2:5-11',4),
            ('People','People are created in the image of God, designed to reflect His character. Through willful transgression, people have fallen from their originally created state.','Genesis 1:26-27 · Psalm 8:3-6 · Romans 3:23',5),
            ('The Church',"The church is composed of all who have experienced the new birth into the family of God through faith in Christ. The local church is an indispensable part of God's plan — for worship, prayer, fellowship, teaching, ministry, and evangelism.",'Matthew 16:16-18 · Romans 12:5 · Ephesians 1:22-23',6),
            ('Salvation','Because people are unable to save themselves, salvation is altogether the work of God. It is the free gift of God received by faith — by believing and trusting in Jesus Christ who died as our substitute.','John 1:12 · Romans 6:23 · Ephesians 2:8-9 · Titus 3:5',7),
            ('Eternal Destiny','God created people to exist forever — either eternally separated from God by sin, or eternally with God through forgiveness and salvation. Heaven and Hell are real places of eternal existence.','John 3:16 · Matthew 25:31-46 · Revelation 20:11-15',8),
            ('Marriage','Oasis Christian Centre believes in the sanctity of marriage between one man and one woman according to Mark 10:6-9. Married people are expected to maintain their marriage vows to each other.','Mark 10:6-9',9),
        ])
    c.execute('''CREATE TABLE IF NOT EXISTS values_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        body TEXT NOT NULL, scripture TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS value_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value_id INTEGER NOT NULL,
        photo TEXT NOT NULL,
        caption TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(value_id) REFERENCES values_items(id) ON DELETE CASCADE
    )''')
    c.execute("SELECT COUNT(*) FROM values_items")
    if c.fetchone()[0]==0:
        c.executemany("INSERT INTO values_items (title,body,scripture,sort_order) VALUES (?,?,?,?)",[
            ('Presence Over Performance','We value real encounters with God over polished appearances. Everything we build should create room for people to meet Jesus, not just admire the moment.','Psalm 27:4 · John 4:23-24',1),
            ('People Matter Deeply','We lead with love, honor, and attention because every person has value. We want everyone who walks into Oasis to feel seen, welcomed, and cared for.','Mark 12:31 · Romans 12:10',2),
            ('Excellence With Humility','We give God our best while staying teachable and servant-hearted. Excellence is not ego; it is stewardship.','Colossians 3:23 · Philippians 2:3-4',3),
            ('Unity Builds Strength','We move farther together than we ever could alone. We protect healthy relationships and work as one team with one mission.','Psalm 133:1 · Ephesians 4:3',4),
            ('Growth Is Intentional','We believe discipleship, healing, and leadership development happen on purpose. We stay open to God changing us from the inside out.','Luke 2:52 · 2 Peter 3:18',5),
        ])
    c.execute('''CREATE TABLE IF NOT EXISTS hub_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT DEFAULT '',
        photo TEXT DEFAULT '',
        icon TEXT DEFAULT 'bi-grid-fill',
        card_type TEXT DEFAULT 'link',
        parent_id INTEGER,
        target_url TEXT DEFAULT '',
        media_url TEXT DEFAULT '',
        modal_title TEXT DEFAULT '',
        modal_body TEXT DEFAULT '',
        modal_button_label TEXT DEFAULT '',
        modal_button_url TEXT DEFAULT '',
        modal_image TEXT DEFAULT '',
        open_in_new_tab INTEGER DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY(parent_id) REFERENCES hub_cards(id) ON DELETE CASCADE
    )''')
    for col, defn in [
        ("card_type",            "TEXT DEFAULT 'link'"),
        ("parent_id",            "INTEGER"),
        ("media_url",            "TEXT DEFAULT ''"),
        ("modal_title",          "TEXT DEFAULT ''"),
        ("modal_body",           "TEXT DEFAULT ''"),
        ("modal_button_label",   "TEXT DEFAULT ''"),
        ("modal_button_url",     "TEXT DEFAULT ''"),
        ("modal_image",          "TEXT DEFAULT ''"),
        ("card_notice_enabled",  "INTEGER DEFAULT 0"),
        ("card_notice_block",    "INTEGER DEFAULT 0"),
        ("card_notice_title",    "TEXT DEFAULT ''"),
        ("card_notice_body",     "TEXT DEFAULT ''"),
        ("card_notice_link",     "TEXT DEFAULT ''"),
        ("card_notice_link_label","TEXT DEFAULT 'Learn More'"),
        ("card_notice_image",    "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute("ALTER TABLE hub_cards ADD COLUMN " + col + " " + defn)
        except Exception:
            pass
    seeded_hub_cards = [
        ('you-said-yes', 'You Said Yes', 'Download PDF', '', 'bi-cloud-arrow-down', 'link', None, 'https://drive.google.com/file/d/1nc29sDRO2Q5ijcWGRv-3HHpXt_I4bF7t/view?usp=sharing', '', '', '', '', '', '', 1, 1, 1),
        ('watch-latest', 'Watch the Latest', 'Latest messages', '', 'bi-youtube', 'link', None, '/watch-sermon', '', '', '', '', '', '', 0, 2, 1),
        ('mission', 'Mission', 'Stories from the field', '', 'bi-globe-americas', 'group', None, '/mission', '', '', '', '', '', '', 0, 3, 1),
        ('upcoming-events', 'Upcoming Events', "What's happening", '', 'bi-calendar3', 'link', None, '/calendar', '', '', '', '', '', '', 0, 4, 1),
        ('prayer-request', 'Prayer Request', "We're here for you", '', 'bi-hand-index-thumb', 'link', None, '/prayer', '', '', '', '', '', '', 0, 5, 1),
        ('help-desk', 'Help Desk', 'Get support', '', 'bi-headset', 'link', None, 'https://www.oasisnj.net/helpdesk', '', '', '', '', '', '', 1, 6, 1),
        ('beliefs-values', 'Our Beliefs & Values', 'What shapes us', '', 'bi-book', 'link', None, '/beliefs', '', '', '', '', '', '', 0, 7, 1),
        ('ministries', 'Ministries', 'Every age, every stage', '', 'bi-people-fill', 'link', None, '/ministries', '', '', '', '', '', '', 0, 8, 1),
        ('leadership', 'Leadership', 'Meet the team', '', 'bi-person-badge', 'link', None, '/leadership', '', '', '', '', '', '', 0, 9, 1),
        ('oasis-crew', 'Oasis Crew', 'Meet the teams', '', 'bi-people-fill', 'link', None, '/behind-the-scene', '', '', '', '', '', '', 0, 10, 1),
        ('beyond-the-walls', 'Beyond the Walls', 'Outreach & missions', '', 'bi-compass', 'link', None, '/beyond-the-walls', '', '', '', '', '', '', 0, 11, 1),
        ('app-feedback', 'Rate the App', 'Tell us what you think', '', 'bi-stars', 'link', None, '/feedback', '', '', '', '', '', '', 0, 12, 1),
    ]
    for card in seeded_hub_cards:
        c.execute(
            "INSERT OR IGNORE INTO hub_cards (slug,title,subtitle,photo,icon,card_type,parent_id,target_url,media_url,modal_title,modal_body,modal_button_label,modal_button_url,modal_image,open_in_new_tab,sort_order,is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            card
        )
    c.execute("UPDATE hub_cards SET card_type='link', target_url='/watch-sermon', media_url='' WHERE slug='watch-latest'")
    c.execute("UPDATE hub_cards SET card_type='link', target_url='/mission' WHERE slug='mission'")
    c.execute("UPDATE hub_cards SET card_type='link', target_url='/beliefs' WHERE slug='beliefs-values'")
    c.execute("UPDATE hub_cards SET card_type='link', target_url='/beyond-the-walls' WHERE slug='beyond-the-walls'")
    c.execute("UPDATE hub_cards SET card_type='link', target_url='/leadership', media_url='', modal_title='', modal_body='', modal_button_label='', modal_button_url='', modal_image='' WHERE slug='leadership'")

    c.execute('''CREATE TABLE IF NOT EXISTS ministries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        description TEXT DEFAULT '', url TEXT DEFAULT '',
        icon TEXT DEFAULT 'bi-people-fill', photo TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
    )''')
    try:
        c.execute("ALTER TABLE ministries ADD COLUMN photo TEXT DEFAULT ''")
    except Exception:
        pass
    c.execute("SELECT COUNT(*) FROM ministries")
    if c.fetchone()[0]==0:
        c.executemany("INSERT INTO ministries (name,description,url,icon,sort_order) VALUES (?,?,?,?,?)",[
            ('Nursery','Even as an infant, we believe your children can experience the love of God. Our nursery team takes time to sing songs and read stories about Jesus.','https://www.oasisnj.net/nursery','bi-heart-fill',1),
            ('Pre-K','Our safe and clean environment helps kids connect to God through activities and lessons designed to let them discover who Jesus is.','https://www.oasisnj.net/prek','bi-emoji-smile-fill',2),
            ('Elementary',"Our goal each week is to build trust with our kids and present Jesus as someone they can trust forever.",'https://www.oasisnj.net/elementary','bi-star-fill',3),
            ('Middle & High School',"Help students realize one thing never changes. GOD LOVES US!",'https://www.oasisnj.net/takeover','bi-lightning-fill',4),
            ('W.O.W Women','Experience the joy and laughter that comes with building new friendships. Join us as we learn how we as women can grow in our relationship with Christ.','https://www.oasisnj.net/wow','bi-flower1',5),
            ('F.M.O Men',"For Men Only — designed to strengthen men to be the mighty men of God that they are called to be.",'https://www.oasisnj.net/fmo','bi-shield-fill',6),
            ('Circles Small Groups','Build relationships while growing together spiritually.','https://www.oasisnj.net/circles','bi-people-fill',7),
            ('The Journey',"For everyone dealing with hurts, habits and hangups, willing to seek God's wisdom to overcome challenges.",'https://www.oasisnj.net/thejourney','bi-compass-fill',8),
            ('The Collective','Our young adults group for ages 18-30.','https://www.oasisnj.net/the-collective','bi-collection-fill',9),
        ])
    sync_behind_scenes_with_ministries(conn)

    c.execute('''CREATE TABLE IF NOT EXISTS serve_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        description TEXT DEFAULT '',
        icon TEXT DEFAULT 'bi-people-fill', color TEXT DEFAULT 'teal', photo TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
    )''')
    try:
        c.execute("ALTER TABLE serve_categories ADD COLUMN photo TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE serve_categories ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    c.execute('''CREATE TABLE IF NOT EXISTS serve_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL,
        label TEXT NOT NULL, sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(category_id) REFERENCES serve_categories(id) ON DELETE CASCADE
    )''')
    c.execute("SELECT COUNT(*) FROM serve_categories")
    if c.fetchone()[0]==0:
        for cid,name,icon,color in [(1,'Oasis Kids','bi-rocket-takeoff-fill','teal'),(2,'Media','bi-camera-reels-fill','orange'),(3,'Worship','bi-music-note-beamed','teal'),(4,'First Impressions','bi-emoji-smile-fill','orange')]:
            c.execute("INSERT INTO serve_categories (id,name,icon,color,sort_order) VALUES (?,?,?,?,?)",(cid,name,icon,color,cid))
        c.executemany("INSERT INTO serve_roles (category_id,label,sort_order) VALUES (?,?,?)",[(1,'Nursery (0–3)',1),(1,'Pre-K (3–5)',2),(1,'Kids Church (K–5)',3),(1,'Middle School (6–8)',4),(1,'High School (9–12)',5),(2,'Media Presentation',1),(2,'Broadcast',2),(2,'Sound / Audio',3),(2,'Social Media',4),(3,'Musicians',1),(3,'Vocals',2),(4,'Guest Experience',1),(4,'Usher',2),(4,'Parking',3)])

    c.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        event_date TEXT NOT NULL,
        event_time TEXT DEFAULT '',
        location TEXT DEFAULT '',
        signup_url TEXT DEFAULT '',
        auto_delete INTEGER DEFAULT 1,
        recurrence TEXT DEFAULT 'none',
        recurrence_detail TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0
    )''')
    # Add new columns to existing DB without dropping data
    for col, defn in [
        ("signup_url",        "TEXT DEFAULT ''"),
        ("auto_delete",       "INTEGER DEFAULT 1"),
        ("recurrence",        "TEXT DEFAULT 'none'"),
        ("recurrence_detail", "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute("ALTER TABLE events ADD COLUMN " + col + " " + defn)
        except Exception:
            pass  # column already exists

    c.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, submitted_at TEXT NOT NULL,
        full_name TEXT, email TEXT, phone TEXT, address TEXT,
        gender TEXT, age_group TEXT, marital TEXT, member_status TEXT, referral TEXT,
        know_god TEXT, find_hope TEXT, make_diff TEXT, serve_area TEXT, prayer TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS prayer_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, submitted_at TEXT NOT NULL,
        full_name TEXT, email TEXT, phone TEXT,
        request_type TEXT DEFAULT 'Personal', message TEXT, is_private INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS app_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, submitted_at TEXT NOT NULL,
        full_name TEXT, rating INTEGER DEFAULT 0, message TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS analytics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        page TEXT NOT NULL, ip TEXT, ua TEXT, sid TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_analytics_sid_page_ts ON analytics(sid, page, ts)')

    c.execute('''CREATE TABLE IF NOT EXISTS banned_ips (
        ip TEXT PRIMARY KEY, banned_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, reason TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sermon_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        note_date TEXT NOT NULL,
        summary TEXT DEFAULT '',
        body_html TEXT DEFAULT '',
        source_file TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        next_steps_file TEXT DEFAULT '',
        next_steps_title TEXT DEFAULT '',
        next_steps_note TEXT DEFAULT ''
    )''')
    # "Oasis Next Steps" — the take-home homework attached to a note. next_steps_title
    # and next_steps_note hold the section heading and instruction; the documents
    # themselves live in sermon_next_steps, one row per file.
    for col in ('next_steps_file', 'next_steps_title', 'next_steps_note'):
        try:
            c.execute("ALTER TABLE sermon_notes ADD COLUMN " + col + " TEXT DEFAULT ''")
        except Exception:
            pass

    c.execute('''CREATE TABLE IF NOT EXISTS sermon_next_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER NOT NULL,
        file TEXT NOT NULL,
        title TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(note_id) REFERENCES sermon_notes(id) ON DELETE CASCADE
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_next_steps_note ON sermon_next_steps(note_id, sort_order, id)')
    # One-time lift of the original single-file column into the new table. The column
    # is cleared as it is moved so the table stays the only source of truth — leaving
    # it populated would resurrect a deleted file on the next boot.
    # next_steps_title stays put — it was always the heading for the section, and
    # still is. The lifted row gets a blank title so the label falls back to the
    # filename the way any other upload does.
    for row in c.execute(
        "SELECT id, next_steps_file FROM sermon_notes WHERE COALESCE(next_steps_file,'') <> ''"
    ).fetchall():
        c.execute(
            "INSERT INTO sermon_next_steps (note_id, file, title, sort_order) VALUES (?,?,'',0)",
            (row[0], row[1])
        )
        c.execute("UPDATE sermon_notes SET next_steps_file='' WHERE id=?", (row[0],))

    conn.commit(); conn.close()

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

@app.context_processor
def inject_site_settings():
    return {'site_settings': all_settings()}

def extract_youtube_video_id(url):
    if not url:
        return ''
    parsed = urlparse(url)
    host = (parsed.netloc or '').lower()
    if 'youtu.be' in host:
        return parsed.path.strip('/').split('/')[0]
    if 'youtube.com' in host:
        if parsed.path == '/watch':
            return parse_qs(parsed.query).get('v', [''])[0]
        if parsed.path.startswith('/live/'):
            return parsed.path.split('/live/', 1)[1].split('/')[0]
        if parsed.path.startswith('/shorts/'):
            return parsed.path.split('/shorts/', 1)[1].split('/')[0]
        if parsed.path.startswith('/embed/'):
            return parsed.path.split('/embed/', 1)[1].split('/')[0]
    return ''

def extract_youtube_channel_id(channel_source):
    if not channel_source:
        return ''
    source = channel_source.strip()
    if not source:
        return ''
    if source.startswith('UC') and len(source) >= 24:
        return source
    if 'feeds/videos.xml' in source:
        parsed = urlparse(source)
        channel_id = parse_qs(parsed.query).get('channel_id', [''])[0]
        if channel_id:
            return channel_id
    if source.startswith('http'):
        parsed = urlparse(source)
        parts = [part for part in parsed.path.split('/') if part]
        if 'channel' in parts:
            idx = parts.index('channel')
            if idx + 1 < len(parts):
                return parts[idx + 1]
        try:
            req = Request(source, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=6) as response:
                html = response.read().decode('utf-8', errors='ignore')
            for pattern in [
                r'"channelId":"(UC[^"]+)"',
                r'itemprop="channelId"\s+content="(UC[^"]+)"',
                r'"externalId":"(UC[^"]+)"',
            ]:
                match = re.search(pattern, html)
                if match:
                    return match.group(1)
        except Exception as e:
            app.logger.warning(f"extract_youtube_channel_id() error: {e}")
    return ''

def build_youtube_live_url(channel_source):
    source = (channel_source or '').strip()
    if not source:
        return ''
    if source.startswith('UC') and len(source) >= 24:
        return f'https://www.youtube.com/channel/{source}/live'
    if source.startswith('http'):
        parsed = urlparse(source)
        base = f'{parsed.scheme or "https"}://{parsed.netloc}'
        path = parsed.path.rstrip('/')
        if not path:
            return ''
        if path.endswith('/live'):
            return source
        return f'{base}{path}/live'
    return ''

def fetch_youtube_live_video(channel_source):
    live_url = build_youtube_live_url(channel_source)
    if not live_url:
        return None
    try:
        req = Request(live_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=6) as response:
            final_url = response.geturl()
            html = response.read().decode('utf-8', errors='ignore')
        indicators = [
            '"isLive":true',
            '"isLiveNow":true',
            '"isLiveContent":true',
            '"badgeStyle":"BADGE_STYLE_TYPE_LIVE_NOW"',
        ]
        is_live = any(indicator in html for indicator in indicators)
        video_id = extract_youtube_video_id(final_url)
        if not video_id:
            for pattern in [
                r'"videoId":"([A-Za-z0-9_-]{11})"',
                r'"canonicalBaseUrl":"\/watch\?v=([A-Za-z0-9_-]{11})"',
                r'"og:url"\s+content="https:\/\/www.youtube.com\/watch\?v=([A-Za-z0-9_-]{11})"',
            ]:
                match = re.search(pattern, html)
                if match:
                    video_id = match.group(1)
                    break
        if not (is_live and video_id):
            return None
        title = ''
        for pattern in [
            r'<meta property="og:title" content="([^"]+)"',
            r'<title>([^<]+)</title>',
        ]:
            match = re.search(pattern, html)
            if match:
                title = match.group(1).replace(' - YouTube', '').strip()
                break
        url = f'https://www.youtube.com/watch?v={video_id}'
        return {
            'slot': 1,
            'title': title or 'Live Now',
            'url': url,
            'video_id': video_id,
            'thumbnail': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
            'published': '',
            'source': 'youtube_live',
            'is_live': True,
            'is_active': True,
        }
    except Exception as e:
        app.logger.warning(f"fetch_youtube_live_video() error: {e}")
        return None

def fetch_youtube_feed_videos(channel_source, limit=10):
    channel_id = extract_youtube_channel_id(channel_source)
    if not channel_id:
        return []
    feed_url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    try:
        req = Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=6) as response:
            feed_xml = response.read()
        root = ET.fromstring(feed_xml)
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015',
            'media': 'http://search.yahoo.com/mrss/',
        }
        videos = []
        for slot, entry in enumerate(root.findall('atom:entry', ns)[:limit], start=1):
            video_id = (entry.findtext('yt:videoId', default='', namespaces=ns) or '').strip()
            title = (entry.findtext('atom:title', default='', namespaces=ns) or '').strip()
            link = entry.find('atom:link', ns)
            url = (link.attrib.get('href') if link is not None else '').strip()
            published = (entry.findtext('atom:published', default='', namespaces=ns) or '').strip()
            if not url and video_id:
                url = f'https://www.youtube.com/watch?v={video_id}'
            videos.append({
                'slot': slot,
                'title': title or f'Sermon {slot}',
                'url': url,
                'video_id': video_id,
                'thumbnail': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg' if video_id else '',
                'published': published,
                'source': 'youtube',
                'is_live': False,
                'is_active': bool(url),
            })
        return videos
    except Exception as e:
        app.logger.warning(f"fetch_youtube_feed_videos() error: {e}")
        return []

def get_sermon_videos(settings=None):
    settings = settings or all_settings()
    channel_source = (
        (settings.get('sermon_channel_url') or '').strip()
        or (settings.get('social_youtube_url') or '').strip()
    )
    auto_videos = fetch_youtube_feed_videos(channel_source, limit=10)
    live_video = fetch_youtube_live_video(channel_source)
    if live_video:
        deduped = [video for video in auto_videos if video.get('video_id') != live_video.get('video_id')]
        auto_videos = [live_video] + deduped
        auto_videos = auto_videos[:10]
        for slot, video in enumerate(auto_videos, start=1):
            video['slot'] = slot
    if auto_videos:
        return auto_videos
    videos = []
    for slot in range(1, 11):
        title = (settings.get(f'sermon_video_{slot}_title') or '').strip()
        url = (settings.get(f'sermon_video_{slot}_url') or '').strip()
        video_id = extract_youtube_video_id(url)
        videos.append({
            'slot': slot,
            'title': title or f'Sermon {slot}',
            'url': url,
            'video_id': video_id,
            'thumbnail': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg' if video_id else '',
            'published': '',
            'source': 'manual',
            'is_live': False,
            'is_active': bool(url),
        })
    return videos

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

PDF_MAX_PAGES = 50

_DOCX_NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
_DOCX_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_BULLET_MARKER_RE = re.compile(r'^[\-\*•●▪‣⁃◦]\s+')
_NUM_MARKER_RE = re.compile(r'^(?:\d{1,3}|[ivxlcdmIVXLCDM]{1,7}|[a-zA-Z])[.)]\s+')
# "C. S. Lewis" / "A. W. Tozer" start exactly like an "A." list marker; never treat those
# as lists, or the initial gets eaten and the sentence turns into a bogus list item.
_INITIALS_RE = re.compile(r'^[A-Za-z]\.\s*[A-Za-z]\.')

def list_marker_kind(text):
    """('ul'|'ol', marker_char_len) when text opens with a list marker, else (None, 0)."""
    if not text or _INITIALS_RE.match(text):
        return None, 0
    match = _BULLET_MARKER_RE.match(text)
    if match:
        return 'ul', match.end()
    match = _NUM_MARKER_RE.match(text)
    if match:
        return 'ol', match.end()
    return None, 0

def sermon_title_from_filename(path):
    base = os.path.splitext(os.path.basename(path or ''))[0]
    base = re.sub(r'[_-]+', ' ', base).strip()
    return base.title() if base else 'Sermon Notes'

# ---------------------------------------------------------------- DOCX

def _docx_toggle_on(rPr, name):
    """True when a run toggle (w:b / w:i) is present and not explicitly switched off.
    Word and Google Docs both emit <w:b w:val="0"/> to *cancel* inherited bold."""
    if rPr is None:
        return False
    el = rPr.find('w:' + name, _DOCX_NS)
    if el is None:
        return False
    val = (el.get(_DOCX_W + 'val') or '').strip().lower()
    return val not in ('0', 'false', 'off')

def _docx_numbering_kinds(docx_zip):
    """Map numId -> 'ul'/'ol' by reading numbering.xml's level-0 numFmt. Best-effort."""
    try:
        xml_data = docx_zip.read('word/numbering.xml')
    except KeyError:
        return {}
    try:
        root = ET.fromstring(xml_data)
        abstract_fmt = {}
        for abstract_num in root.findall('w:abstractNum', _DOCX_NS):
            abs_id = abstract_num.get(_DOCX_W + 'abstractNumId')
            for lvl in abstract_num.findall('w:lvl', _DOCX_NS):
                if lvl.get(_DOCX_W + 'ilvl') == '0':
                    num_fmt_el = lvl.find('w:numFmt', _DOCX_NS)
                    if num_fmt_el is not None:
                        abstract_fmt[abs_id] = num_fmt_el.get(_DOCX_W + 'val')
                    break
        num_kinds = {}
        for num in root.findall('w:num', _DOCX_NS):
            num_id = num.get(_DOCX_W + 'numId')
            abstract_id_el = num.find('w:abstractNumId', _DOCX_NS)
            if abstract_id_el is not None:
                fmt = abstract_fmt.get(abstract_id_el.get(_DOCX_W + 'val'), 'bullet')
                num_kinds[num_id] = 'ul' if fmt in ('bullet', 'none') else 'ol'
        return num_kinds
    except Exception:
        return {}

def _docx_para_runs(para):
    """[text, bold, italic] in document order. Walks the paragraph tree rather than only
    its direct w:r children, so text nested in w:hyperlink (scripture links), w:ins
    (tracked changes) or w:smartTag is not lost, and w:br is honoured wherever it sits.
    Deleted text (w:delText) is skipped because only w:t is read."""
    runs = []

    def walk(node, bold, italic):
        for child in node:
            tag = child.tag.split('}')[-1]
            if tag in ('pPr', 'rPr'):
                continue
            if tag == 'p':
                continue  # nested text-box paragraph; emitted as its own record
            if tag == 'r':
                rPr = child.find('w:rPr', _DOCX_NS)
                walk(child, _docx_toggle_on(rPr, 'b'), _docx_toggle_on(rPr, 'i'))
            elif tag == 't':
                if child.text:
                    runs.append([child.text, bold, italic])
            elif tag == 'br':
                runs.append(['\n', bold, italic])
            elif tag == 'tab':
                runs.append(['\t', bold, italic])
            else:
                walk(child, bold, italic)

    walk(para, False, False)

    merged = []
    for text, bold, italic in runs:
        if merged and merged[-1][1] == bold and merged[-1][2] == italic:
            merged[-1][0] += text
        else:
            merged.append([text, bold, italic])
    return merged

def docx_records(abs_path):
    """Parse a DOCX once into paragraph records, so the plain-text view (title/summary)
    and the HTML view (body) are always derived from the same list of paragraphs."""
    try:
        with zipfile.ZipFile(abs_path) as docx:
            xml_data = docx.read('word/document.xml')
            num_kinds = _docx_numbering_kinds(docx)
        root = ET.fromstring(xml_data)
        records = []
        for para in root.findall('.//w:p', _DOCX_NS):
            pPr = para.find('w:pPr', _DOCX_NS)
            style_val = ''
            list_kind = None
            if pPr is not None:
                pStyle = pPr.find('w:pStyle', _DOCX_NS)
                if pStyle is not None:
                    style_val = (pStyle.get(_DOCX_W + 'val') or '').lower()
                numPr = pPr.find('w:numPr', _DOCX_NS)
                if numPr is not None:
                    numId_el = numPr.find('w:numId', _DOCX_NS)
                    num_id = numId_el.get(_DOCX_W + 'val') if numId_el is not None else None
                    list_kind = num_kinds.get(num_id, 'ul')
            if not list_kind and style_val.startswith('listbullet'):
                list_kind = 'ul'
            elif not list_kind and style_val.startswith('listnumber'):
                list_kind = 'ol'

            runs = _docx_para_runs(para)
            if not runs:
                continue

            if not list_kind:
                first_text = runs[0][0]
                stripped = first_text.lstrip('\n\t ')
                lead_len = len(first_text) - len(stripped)
                kind, marker_len = list_marker_kind(stripped)
                if kind:
                    list_kind = kind
                    runs[0][0] = first_text[:lead_len] + stripped[marker_len:]

            text = re.sub(r'[\n\t]+', ' ', ''.join(r[0] for r in runs)).strip()
            if not text:
                continue
            records.append({
                'runs': runs,
                'text': text,
                'list_kind': list_kind,
                'heading': style_val.startswith('heading') or style_val in ('title', 'subtitle'),
            })
        return records
    except Exception as e:
        app.logger.warning(f"docx_records() error: {e}")
        return []

def extract_docx_paragraphs(abs_path):
    return [rec['text'] for rec in docx_records(abs_path)]

def _docx_runs_to_html(runs, suppress_bold=False):
    parts = []
    for raw_text, is_bold, is_italic in runs:
        if not raw_text:
            continue
        escaped = html.escape(raw_text).replace('\n', '<br>').replace('\t', '&emsp;')
        if suppress_bold:
            is_bold = False
        if is_bold and is_italic:
            escaped = f'<strong><em>{escaped}</em></strong>'
        elif is_bold:
            escaped = f'<strong>{escaped}</strong>'
        elif is_italic:
            escaped = f'<em>{escaped}</em>'
        parts.append(escaped)
    return ''.join(parts)

def docx_records_to_html(records):
    blocks = []
    list_kind = None
    list_items = []

    def flush_list():
        nonlocal list_kind, list_items
        if list_items:
            items_html = ''.join(f'<li>{item}</li>' for item in list_items)
            blocks.append(f'<{list_kind}>{items_html}</{list_kind}>')
        list_kind = None
        list_items = []

    for rec in records:
        para_html = _docx_runs_to_html(rec['runs'], suppress_bold=rec['heading']).strip()
        if not para_html:
            continue
        if rec['list_kind']:
            if list_kind and list_kind != rec['list_kind']:
                flush_list()
            list_kind = rec['list_kind']
            list_items.append(para_html)
            continue
        flush_list()
        tag = 'h2' if rec['heading'] else 'p'
        blocks.append(f'<{tag}>{para_html}</{tag}>')

    flush_list()
    return '\n'.join(blocks)

def extract_docx_html(abs_path, skip_first=False):
    """Rich HTML from a DOCX: bold, italic, headings, manual line breaks, and lists."""
    records = docx_records(abs_path)
    return docx_records_to_html(records[1:] if skip_first else records)

# ---------------------------------------------------------------- PDF

def _pdf_cluster_words_into_lines(words, tol=2.0):
    lines = []
    current = []
    current_top = None
    for w in sorted(words, key=lambda w: (round(w['top'], 1), w['x0'])):
        top = w['top']
        if current_top is None or abs(top - current_top) <= tol:
            current.append(w)
            if current_top is None:
                current_top = top
        else:
            lines.append(current)
            current = [w]
            current_top = top
    if current:
        lines.append(current)
    return lines

def _pdf_line_size(words):
    sizes = Counter(round(w.get('size') or 0) for w in words)
    return sizes.most_common(1)[0][0] if sizes else 0

def _pdf_runs_html(words, suppress_bold=False):
    if not words:
        return ''
    runs = []
    current_words = []
    current_style = None
    for w in words:
        fname = (w.get('fontname') or '').lower()
        style = (('bold' in fname) and not suppress_bold, 'italic' in fname or 'oblique' in fname)
        if current_style is None or style == current_style:
            current_words.append(w['text'])
            current_style = style
        else:
            runs.append((current_style, current_words))
            current_words = [w['text']]
            current_style = style
    if current_words:
        runs.append((current_style, current_words))
    parts = []
    for (is_bold, is_italic), run_words in runs:
        text = html.escape(' '.join(run_words))
        if is_bold and is_italic:
            text = f'<strong><em>{text}</em></strong>'
        elif is_bold:
            text = f'<strong>{text}</strong>'
        elif is_italic:
            text = f'<em>{text}</em>'
        parts.append(text)
    return ' '.join(parts)

def pdf_read_lines(abs_path, max_pages=PDF_MAX_PAGES):
    """Positioned text lines from a PDF. Single source of truth for title and body."""
    lines = []
    try:
        with pdfplumber.open(abs_path) as pdf:
            pages = pdf.pages
            if len(pages) > max_pages:
                app.logger.warning(
                    f"pdf_read_lines: {abs_path} has {len(pages)} pages, parsing first {max_pages}")
                pages = pages[:max_pages]
            for page_index, page in enumerate(pages):
                words = page.extract_words(extra_attrs=['fontname', 'size'], use_text_flow=False)
                if not words:
                    continue
                for i, line_words in enumerate(_pdf_cluster_words_into_lines(words)):
                    lines.append({
                        'words': line_words,
                        'top': line_words[0]['top'],
                        'x0': min(w['x0'] for w in line_words),
                        'x1': max(w['x1'] for w in line_words),
                        'page_width': float(page.width or 0),
                        'text': ' '.join(w['text'] for w in line_words),
                        'new_page': i == 0 and page_index > 0,
                    })
    except Exception as e:
        app.logger.warning(f"pdf_read_lines() error: {e}")
        return []
    return lines

def _pdf_layout_metrics(lines):
    """Body font size, modal line leading, and the left/right edges of the text block."""
    size_counter = Counter()
    for line in lines:
        for w in line['words']:
            size_counter[round(w.get('size') or 0)] += 1
    body_size = size_counter.most_common(1)[0][0] if size_counter else 12
    body_lines = [l for l in lines if _pdf_line_size(l['words']) == body_size]

    gap_counter = Counter()
    prev = None
    for line in lines:
        if (prev is not None and not line['new_page']
                and _pdf_line_size(prev['words']) == body_size
                and _pdf_line_size(line['words']) == body_size):
            gap = round(line['top'] - prev['top'])
            if gap > 0:
                gap_counter[gap] += 1
        prev = line
    leading = gap_counter.most_common(1)[0][0] if gap_counter else max(body_size * 1.6, 1)

    left_edge = min((l['x0'] for l in body_lines), default=0)
    # Assume symmetric margins to locate the true right margin. Using the widest observed
    # line instead would be circular: in notes where no line reaches the margin (an outline
    # of short points) the longest point *defines* the edge, so every line looks "full" and
    # the whole document collapses into one paragraph.
    page_width = max((l.get('page_width') or 0) for l in lines) if lines else 0
    observed_right = max((l['x1'] for l in body_lines), default=0)
    right_edge = max(observed_right, page_width - left_edge) if page_width else observed_right
    return body_size, leading, left_edge, right_edge

def pdf_lines_to_html(lines):
    """Rich HTML from positioned PDF lines: bold runs, size-based headings, lists, paragraphs."""
    if not lines:
        return ''
    body_size, leading, left_edge, right_edge = _pdf_layout_metrics(lines)
    # A wrapped line runs out to the right margin; the last line of a paragraph stops short.
    # Spacing alone cannot separate one-line paragraphs, which all share the same gap.
    # Ragged-right wrapping leaves up to a long word unused, so allow ~15% of the measure:
    # across sample notes, wrapped lines fell short by <=53pt and real paragraph ends by
    # >=151pt, so the threshold sits in open space between the two populations.
    measure = max(right_edge - left_edge, 1)
    fill_tol = max(measure * 0.15, body_size * 2.5)

    blocks = []
    list_kind = None
    list_items = []
    para_words = []
    prev_line = None

    def flush_list():
        nonlocal list_kind, list_items
        if list_items:
            items_html = ''.join(f'<li>{item}</li>' for item in list_items)
            blocks.append(f'<{list_kind}>{items_html}</{list_kind}>')
        list_kind = None
        list_items = []

    def flush_para():
        nonlocal para_words
        if para_words:
            blocks.append(f'<p>{_pdf_runs_html(para_words)}</p>')
        para_words = []

    for line in lines:
        words = line['words']
        size = _pdf_line_size(words)

        if size >= body_size * 1.15 and len(line['text']) <= 90:
            flush_list()
            flush_para()
            tag = 'h2' if size >= body_size * 1.4 else 'h3'
            blocks.append(f'<{tag}>{_pdf_runs_html(words, suppress_bold=True)}</{tag}>')
            prev_line = line
            continue

        kind, marker_len = list_marker_kind(line['text'])
        if kind:
            flush_para()
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            item_words = words[1:] if len(words[0]['text']) < marker_len else words
            list_items.append(_pdf_runs_html(item_words))
            prev_line = line
            continue

        flush_list()
        continues_para = (
            bool(para_words) and prev_line is not None and not line['new_page']
            and (line['top'] - prev_line['top']) <= leading * 1.35
            and prev_line['x1'] >= right_edge - fill_tol
            and line['x0'] <= left_edge + body_size
        )
        if not continues_para:
            flush_para()
        para_words.extend(words)
        prev_line = line

    flush_list()
    flush_para()
    return '\n'.join(blocks)

def extract_pdf_html(abs_path, skip_first=False):
    lines = pdf_read_lines(abs_path)
    return pdf_lines_to_html(lines[1:] if skip_first else lines)

RICH_TEXT_TAGS = ['p', 'br', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'ul', 'ol', 'li', 'blockquote']
_RICH_TEXT_TAG_RE = re.compile(r'<(' + '|'.join(RICH_TEXT_TAGS) + r')[ >/]', re.I)

def sanitize_rich_html(raw):
    return bleach.clean(raw or '', tags=RICH_TEXT_TAGS, attributes={}, strip=True)

@app.template_filter('richtext')
def richtext_filter(raw):
    """Render admin-entered story/notes text as HTML: sanitizes it if it already
    contains our rich-text tags (from the admin formatting toolbar), otherwise
    treats it as legacy plain text and auto-wraps blank-line-separated paragraphs."""
    raw = raw or ''
    if not _RICH_TEXT_TAG_RE.search(raw):
        escaped = html.escape(raw)
        paragraphs = [p.strip() for p in escaped.split('\n\n') if p.strip()] or ([escaped.strip()] if escaped.strip() else [])
        return Markup(''.join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs))
    return Markup(sanitize_rich_html(raw))

def build_sermon_note_payload(path_rel, ext, fallback_title='', summary=''):
    abs_path = os.path.join(BASE_DIR, 'static', path_rel)
    title = fallback_title.strip() if fallback_title else ''
    # Both branches parse the document once and derive the title, the summary and the
    # body from that same parse, so the title can never be taken from one extractor
    # while the body comes from another.
    if ext == 'docx':
        units = docx_records(abs_path)
        plain_lines = [rec['text'] for rec in units]
        to_html = docx_records_to_html
    else:
        units = pdf_read_lines(abs_path)
        plain_lines = [line['text'] for line in units]
        to_html = pdf_lines_to_html

    if not title and plain_lines:
        title = plain_lines[0][:120].strip()
        units = units[1:]
        plain_lines = plain_lines[1:]
    if not title:
        title = sermon_title_from_filename(path_rel)
    if not summary:
        source_line = next((l for l in plain_lines if len(l.split()) > 6), '')
        summary = source_line[:180].strip() if source_line else 'Sermon notes for this message.'
    body_html = to_html(units)
    if not body_html and plain_lines:
        body_html = '\n'.join(f'<p>{html.escape(l)}</p>' for l in plain_lines)
    return {
        'title': title,
        'summary': summary,
        'body_html': body_html or '<p>Sermon notes were uploaded, but the document did not contain readable text.</p>',
        'source_file': path_rel,
    }

def latest_sermon_note(conn):
    return conn.execute(
        "SELECT * FROM sermon_notes ORDER BY note_date DESC, id DESC LIMIT 1"
    ).fetchone()

NEXT_STEPS_LABEL = 'Oasis Next Steps'

def row_value(row, key, default=''):
    """Read a column off a sqlite3.Row without blowing up on rows that predate
    a migration (sqlite3.Row raises IndexError for unknown keys)."""
    if row is None:
        return default
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value

MAX_NEXT_STEPS_FILES = 10
_UPLOAD_STEM_RE = re.compile(r'_[0-9a-f]{8}$')

def next_steps_label(note):
    """Heading for the whole take-home section on a note."""
    return (row_value(note, 'next_steps_title') or '').strip() or NEXT_STEPS_LABEL

def note_next_steps(conn, nid):
    return conn.execute(
        "SELECT * FROM sermon_next_steps WHERE note_id=? ORDER BY sort_order, id", (nid,)
    ).fetchall()

def next_steps_counts(conn):
    """note_id -> number of attached documents, for the archive and admin lists."""
    return {
        r['note_id']: r['n'] for r in conn.execute(
            "SELECT note_id, COUNT(*) AS n FROM sermon_next_steps GROUP BY note_id"
        ).fetchall()
    }

def next_steps_item_label(item):
    """Name shown in the download list. Falls back to the filename with the uuid
    that save_document_file() appends stripped back off, so a cleared name box
    reads "Family Devotional" rather than "Family Devotional 75E0Dc29"."""
    explicit = (row_value(item, 'title') or '').strip()
    if explicit:
        return explicit
    base = os.path.splitext(os.path.basename(row_value(item, 'file')))[0]
    return sermon_title_from_filename(_UPLOAD_STEM_RE.sub('', base))

def next_steps_download_name(note, item):
    """Filename the phone saves it under — uploads keep a uuid stem on disk, which
    is useless to someone hunting for the file later in their Files app."""
    ext = os.path.splitext(row_value(item, 'file'))[1].lower() or '.pdf'
    stem = re.sub(r'[^A-Za-z0-9]+', '-', f"{next_steps_item_label(item)} {row_value(note, 'note_date')}").strip('-')
    return (stem or 'oasis-next-steps') + ext

def next_steps_file_meta(item):
    """'PDF · 240 KB' for the download list, or just the type if the file is gone."""
    stored = row_value(item, 'file')
    kind = (os.path.splitext(stored)[1].lstrip('.') or 'file').upper()
    try:
        size = os.path.getsize(os.path.join(UPLOAD_FOLDER, os.path.basename(stored)))
    except OSError:
        return kind
    if size >= 1024 * 1024:
        return f"{kind} · {size / (1024 * 1024):.1f} MB"
    return f"{kind} · {max(1, round(size / 1024))} KB"

app.jinja_env.globals['max_next_steps'] = MAX_NEXT_STEPS_FILES
app.jinja_env.filters['next_steps_label'] = next_steps_label
app.jinja_env.filters['next_steps_item_label'] = next_steps_item_label
app.jinja_env.filters['next_steps_file_meta'] = next_steps_file_meta

def client_ip():
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.headers.get('X-Real-IP') or request.remote_addr or '0.0.0.0'

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

def get_get_involved(settings=None):
    settings = settings or all_settings()
    return {
        'enabled': (settings.get('get_involved_enabled') or '0') == '1',
        'title': (settings.get('get_involved_title') or '').strip(),
        'body': (settings.get('get_involved_body') or '').strip(),
        'link': (settings.get('get_involved_link') or '').strip(),
        'link_label': (settings.get('get_involved_link_label') or 'Sign Up').strip() or 'Sign Up',
        'image': (settings.get('get_involved_image') or '').strip(),
    }

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

# ── Login rate limiting ───────────────────────────────────────────────────────

_login_attempts: dict = {}
_LOGIN_MAX = 5
_LOGIN_WINDOW = 900  # 15 minutes

def _check_rate_limit(ip):
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    if len(attempts) >= _LOGIN_MAX:
        wait = int(_LOGIN_WINDOW - (now - attempts[0]))
        return False, wait
    return True, 0

def _record_failed_login(ip):
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    attempts.append(now)
    _login_attempts[ip] = attempts

def _clear_login_attempts(ip):
    _login_attempts.pop(ip, None)

# ── Auth decorators ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
        return f(*a,**kw)
    return dec

def superadmin_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
        if session.get('admin_role')!='superadmin':
            flash('Superadmin only.','info'); return redirect(url_for('admin_dashboard'))
        return f(*a,**kw)
    return dec

# ── Public routes ─────────────────────────────────────────────────────────────

@app.route('/manifest.json')
def pwa_manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def pwa_service_worker():
    resp = app.send_static_file('sw.js')
    # Always revalidate so a new SW version deploys without waiting out the cache
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

@app.route('/')
def splash(): track('splash'); return render_template('splash.html')

@app.route('/gate')
def gate():
    track('gate')
    thank_you = None
    if request.args.get('connect') == '1':
        raw_name = (request.args.get('name') or '').strip()
        first_name = raw_name.split()[0] if raw_name else ''
        thank_you = f"Thank you{', ' + first_name if first_name else ''}. We will reach out soon."
    return render_template('gate.html', settings=all_settings(), thank_you=thank_you)

@app.route('/hub')
def hub():
    track('hub')
    hour=datetime.now().hour
    greeting="Good Morning" if hour<12 else "Good Afternoon" if hour<17 else "Good Evening"
    settings = all_settings()
    conn = get_db()
    latest_note = latest_sermon_note(conn)
    hub_cards = get_hub_cards(conn)
    conn.close()
    return render_template(
        'hub.html',
        greeting=greeting,
        date=datetime.now().strftime("%b %d, %Y").upper(),
        settings=settings,
        hub_notice=get_hub_notice(settings),
        get_involved=get_get_involved(settings),
        latest_note=latest_note,
        hub_cards=hub_cards,
    )

@app.route('/watch-sermon')
def watch_sermon():
    track('watch_sermon')
    settings = all_settings()
    return render_template('watch_sermon.html', settings=settings, videos=get_sermon_videos(settings))

@app.route('/sermon-notes')
def sermon_notes():
    track('sermon_notes')
    conn = get_db()
    notes = conn.execute("SELECT * FROM sermon_notes ORDER BY note_date DESC, id DESC").fetchall()
    latest = notes[0] if notes else None
    ns_counts = next_steps_counts(conn)
    conn.close()
    return render_template('sermon_notes.html', notes=notes, latest=latest,
                           ns_counts=ns_counts, settings=all_settings())

@app.route('/sermon-notes/<int:nid>')
def sermon_note_detail(nid):
    track('sermon_note_detail')
    conn = get_db()
    note = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    latest = latest_sermon_note(conn)
    ns_items = note_next_steps(conn, nid) if note else []
    conn.close()
    if not note:
        return redirect(url_for('sermon_notes'))
    return render_template('sermon_note_detail.html', note=note, latest=latest,
                           ns_items=ns_items, settings=all_settings())

@app.route('/sermon-notes/<int:nid>/next-steps/<int:fid>')
def sermon_next_steps(nid, fid):
    """Hand one Oasis Next Steps take-home document to the visitor's phone.
    ?view=1 opens it inline instead, which is the friendlier path on iOS where a
    forced download drops the file into Files rather than showing it."""
    conn = get_db()
    note = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    item = conn.execute(
        "SELECT * FROM sermon_next_steps WHERE id=? AND note_id=?", (fid, nid)
    ).fetchone()
    conn.close()
    if not note or not item:
        return redirect(url_for('sermon_note_detail', nid=nid))
    fname = os.path.basename(row_value(item, 'file'))
    if not fname or not os.path.isfile(os.path.join(UPLOAD_FOLDER, fname)):
        app.logger.warning(f"sermon_next_steps: missing file for note {nid} item {fid}")
        return redirect(url_for('sermon_note_detail', nid=nid))
    inline = request.args.get('view') == '1'
    track('sermon_next_steps_view' if inline else 'sermon_next_steps_download')
    return send_from_directory(
        UPLOAD_FOLDER, fname,
        as_attachment=not inline,
        download_name=next_steps_download_name(note, item),
    )

@app.route('/sermon-notes/<int:nid>/next-steps')
def sermon_next_steps_first(nid):
    """Kept so links handed out before multi-file support still resolve."""
    conn = get_db()
    items = note_next_steps(conn, nid)
    conn.close()
    if not items:
        return redirect(url_for('sermon_note_detail', nid=nid))
    return redirect(url_for('sermon_next_steps', nid=nid, fid=items[0]['id'],
                            **({'view': '1'} if request.args.get('view') == '1' else {})))

@app.route('/beliefs')
def beliefs():
    track('beliefs')
    return render_template('beliefs_landing.html', settings=all_settings())

@app.route('/beliefs/statement')
def beliefs_statement():
    track('beliefs_statement')
    conn=get_db()
    items=conn.execute("SELECT * FROM beliefs ORDER BY sort_order").fetchall()
    images=conn.execute("SELECT * FROM belief_images ORDER BY sort_order, id").fetchall()
    conn.close()
    return render_template(
        'beliefs.html',
        beliefs=items,
        belief_images=gallery_images_by_parent(images, 'belief_id'),
        settings=all_settings()
    )

@app.route('/beliefs/values')
def beliefs_values():
    track('beliefs_values')
    conn=get_db()
    items=conn.execute("SELECT * FROM values_items ORDER BY sort_order").fetchall()
    images=conn.execute("SELECT * FROM value_images ORDER BY sort_order, id").fetchall()
    conn.close()
    return render_template(
        'values.html',
        values=items,
        value_images=gallery_images_by_parent(images, 'value_id'),
        settings=all_settings()
    )

@app.route('/ministries')
def ministries():
    # Only ministries with a description are public; serving teams synced from
    # Oasis Crew have empty descriptions and stay off this page.
    track('ministries'); conn=get_db(); items=conn.execute("SELECT * FROM ministries WHERE TRIM(COALESCE(description,''))!='' ORDER BY sort_order").fetchall(); conn.close()
    return render_template('ministries.html',ministries=items,settings=all_settings())

@app.route('/leadership')
def leadership():
    track('leadership'); conn=get_db(); leaders=conn.execute("SELECT * FROM leaders ORDER BY sort_order").fetchall(); conn.close()
    return render_template('leadership.html',leaders=leaders,settings=all_settings())

@app.route('/behind-the-scene')
def behind_scene():
    track('behind_the_scene')
    settings = all_settings()
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scenes = list(conn.execute("SELECT * FROM behind_scenes ORDER BY sort_order, name").fetchall())
    random.shuffle(scenes)
    counts = get_people_count_by_scene(conn)
    conn.close()
    return render_template('behind_scene.html', scenes=scenes, member_counts=counts, settings=settings)

@app.route('/behind-the-scene/<int:sid>')
def behind_scene_detail(sid):
    track('behind_the_scene_detail')
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene = conn.execute("SELECT * FROM behind_scenes WHERE id=?", (sid,)).fetchone()
    if not scene:
        conn.close()
        return redirect(url_for('behind_scene'))
    members = get_people_for_scene(conn, sid)
    conn.close()
    return render_template('behind_scene_detail.html', scene=scene, members=members)

@app.route('/mission')
def missions():
    track('missions')
    conn = get_db()
    cards = get_mission_cards(conn)
    conn.close()
    return render_template('missions.html', missions=cards, settings=all_settings())

@app.route('/mission/<int:mid>')
def mission_detail(mid):
    track('mission_detail')
    conn = get_db()
    mission = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    if not mission:
        conn.close()
        return redirect(url_for('missions'))
    images = conn.execute(
        "SELECT * FROM mission_images WHERE mission_id=? ORDER BY sort_order, id",
        (mid,)
    ).fetchall()
    conn.close()
    return render_template('mission_detail.html', mission=mission, images=images, settings=all_settings())

@app.route('/beyond-the-walls')
def beyond_the_walls():
    track('beyond_the_walls')
    conn = get_db()
    cards = get_beyond_wall_cards(conn)
    conn.close()
    return render_template('beyond_walls.html', items=cards, settings=all_settings())

@app.route('/beyond-the-walls/<int:bid>')
def beyond_the_walls_detail(bid):
    track('beyond_the_walls_detail')
    conn = get_db()
    item = conn.execute("SELECT * FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    if not item:
        conn.close()
        return redirect(url_for('beyond_the_walls'))
    images = conn.execute(
        "SELECT * FROM beyond_wall_images WHERE beyond_id=? ORDER BY sort_order, id",
        (bid,)
    ).fetchall()
    conn.close()
    return render_template('beyond_walls_detail.html', item=item, images=images, settings=all_settings())

@app.route('/hub-cards/<int:cid>')
def hub_card_group(cid):
    track('hub_card_group')
    conn = get_db()
    parent = conn.execute("SELECT * FROM hub_cards WHERE id=? AND is_active=1", (cid,)).fetchone()
    if not parent:
        conn.close()
        return redirect(url_for('hub'))
    if parent['slug'] == 'mission':
        conn.close()
        return redirect(url_for('missions'))
    if parent['slug'] == 'beliefs-values':
        conn.close()
        return redirect(url_for('beliefs'))
    if parent['slug'] == 'beyond-the-walls':
        conn.close()
        return redirect(url_for('beyond_the_walls'))
    children = get_hub_cards(conn, parent_id=cid)
    conn.close()
    return render_template('hub_card_group.html', parent=parent, cards=children, settings=all_settings())

@app.route('/serve')
def serve():
    track('serve'); conn=get_db()
    cats=conn.execute("SELECT * FROM serve_categories ORDER BY sort_order").fetchall()
    roles=conn.execute("SELECT * FROM serve_roles ORDER BY sort_order").fetchall(); conn.close()
    grouped=[{'cat':c,'roles':[r for r in roles if r['category_id']==c['id']]} for c in cats]
    return render_template('serve.html',grouped=grouped,settings=all_settings())

def expand_recurring(events, today_str, months_ahead=6):
    """Return a merged list of one-time + expanded recurring occurrences."""
    from datetime import timedelta
    import calendar as cal_mod
    today  = date.fromisoformat(today_str)
    cutoff = date(today.year + (today.month + months_ahead - 1) // 12,
                  (today.month + months_ahead - 1) % 12 + 1, 1)

    result = []
    for e in events:
        rec = (e['recurrence'] or 'none').strip()
        if rec == 'none':
            result.append(dict(e))
            continue
        # Build synthetic occurrences from today up to cutoff
        detail = (e['recurrence_detail'] or '').strip()  # e.g. "1,3-Thursday" or "last-Saturday"
        cur = today.replace(day=1)
        while cur <= cutoff:
            year, month = cur.year, cur.month
            last_day = cal_mod.monthrange(year, month)[1]
            days_in_month = [date(year, month, d) for d in range(1, last_day+1)]
            occurrences = []
            try:
                # detail format: "1st,3rd-Thursday" or "last-Saturday"
                dash_idx = detail.rfind('-')
                weekday_name = detail[dash_idx+1:].strip().capitalize()
                pos_str      = detail[:dash_idx].strip() if dash_idx > 0 else ''
                weekdays = {'Monday':0,'Tuesday':1,'Wednesday':2,'Thursday':3,
                            'Friday':4,'Saturday':5,'Sunday':6}
                wd = weekdays.get(weekday_name, 0)
                matching = [d for d in days_in_month if d.weekday() == wd]
                pos_map = {'1st':0,'2nd':1,'3rd':2,'4th':3,'last':-1}
                pos_list = [p.strip() for p in pos_str.split(',') if p.strip()] if pos_str else list(pos_map.keys())
                for pos in pos_list:
                    if pos == 'last' and matching:
                        occurrences.append(matching[-1])
                    elif pos in pos_map and pos != 'last':
                        idx = pos_map[pos]
                        if idx < len(matching):
                            occurrences.append(matching[idx])
            except Exception:
                pass
            for occ_date in occurrences:
                if occ_date >= today:
                    row = dict(e)
                    row['event_date'] = occ_date.isoformat()
                    row['id'] = f"rec_{e['id']}_{occ_date.isoformat()}"
                    result.append(row)
            # next month
            if month == 12: cur = date(year+1, 1, 1)
            else:           cur = date(year, month+1, 1)
    result.sort(key=lambda x: (str(x['event_date']), str(x.get('event_time',''))))
    return result

@app.route('/calendar')
def calendar():
    track('calendar')
    conn = get_db()
    today_str = date.today().isoformat()
    # Auto-delete past one-time events marked for auto-delete
    conn.execute(
        "DELETE FROM events WHERE auto_delete=1 AND recurrence='none' AND event_date < ?",
        (today_str,)
    )
    conn.commit()
    events = conn.execute("SELECT * FROM events ORDER BY event_date ASC, event_time ASC").fetchall()
    conn.close()
    all_events = expand_recurring(events, today_str)
    upcoming   = [e for e in all_events if str(e['event_date']) >= today_str]
    conn2 = get_db()
    past_raw = conn2.execute(
        "SELECT * FROM events WHERE recurrence='none' AND event_date < ? ORDER BY event_date DESC LIMIT 20",
        (today_str,)
    ).fetchall()
    conn2.close()
    past_rows = [dict(r) for r in past_raw]
    return render_template('calendar.html', upcoming=upcoming, past=past_rows, today=today_str, settings=all_settings())

@app.route('/prayer',methods=['GET','POST'])
def prayer():
    track('prayer'); sent=False
    if request.method=='POST':
        fn=request.form.get('full_name','').strip(); em=request.form.get('email','').strip()
        ph=request.form.get('phone','').strip(); rt=request.form.get('request_type','Personal')
        msg=request.form.get('message','').strip(); priv=1 if request.form.get('is_private') else 0
        conn=get_db()
        conn.execute("INSERT INTO prayer_requests (submitted_at,full_name,email,phone,request_type,message,is_private) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M'),fn,em,ph,rt,msg,priv))
        conn.commit(); conn.close()
        priv_note="<p style='color:#c0392b;'><b>⚠ PRIVATE — Pastoral team only.</b></p>" if priv else ""
        html=f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
        <div style="background:#13677A;padding:20px;text-align:center"><h1 style="color:white;margin:0">Prayer Request</h1></div>
        <div style="padding:24px;border:1px solid #ddd;border-top:none">{priv_note}
        <p><b>Name:</b> {fn or '—'}<br><b>Email:</b> {em or '—'}<br><b>Phone:</b> {ph or '—'}<br><b>Type:</b> {rt}</p>
        <hr><h3 style="color:#13677A">Request</h3>
        <p style="background:#f9f9f9;padding:14px;border-left:4px solid #13677A">{msg}</p></div></body></html>"""
        send_email(f"Prayer Request: {fn}",get_setting('prayer_email','Oasis@OasisNJ.net'),html)
        sent=True
    return render_template('prayer.html',sent=sent,settings=all_settings())

@app.route('/feedback',methods=['GET','POST'])
def feedback():
    track('feedback'); sent=False
    if request.method=='POST':
        fn=request.form.get('full_name','').strip()
        try: rating=max(0,min(5,int(request.form.get('rating','0') or 0)))
        except ValueError: rating=0
        msg=request.form.get('message','').strip()
        if msg or rating:
            conn=get_db()
            conn.execute("INSERT INTO app_feedback (submitted_at,full_name,rating,message) VALUES (?,?,?,?)",
                (datetime.now().strftime('%Y-%m-%d %H:%M'),fn,rating,msg))
            conn.commit(); conn.close()
            # No email notification — app feedback/ratings stay in the admin panel only.
        sent=True
    return render_template('feedback.html',sent=sent,settings=all_settings())

@app.route('/connect',methods=['GET','POST'])
def connect():
    track('connect')
    if request.method=='POST':
        fn=request.form.get('full_name','').strip(); em=request.form.get('email','').strip()
        ph=request.form.get('phone','').strip(); addr=request.form.get('address','').strip()
        mar=request.form.get('marital',''); gen=request.form.get('gender','')
        age=request.form.get('age_group',''); sts=request.form.get('member_status','')
        ref=request.form.get('referral','').strip()
        kg=', '.join(request.form.getlist('kg')); fh=', '.join(request.form.getlist('fh')); md=', '.join(request.form.getlist('md'))
        sa=request.form.get('serve_area',''); pr=request.form.get('message','').strip()
        conn=get_db()
        conn.execute("INSERT INTO submissions (submitted_at,full_name,email,phone,address,gender,age_group,marital,member_status,referral,know_god,find_hope,make_diff,serve_area,prayer) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M'),fn,em,ph,addr,gen,age,mar,sts,ref,kg,fh,md,sa,pr))
        conn.commit(); conn.close()
        html=f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
        <div style="background:#F2541B;padding:20px;text-align:center"><h1 style="color:white;margin:0">New Connect Card</h1></div>
        <div style="padding:24px;border:1px solid #ddd;border-top:none">
        <p><b>Name:</b> {fn}<br><b>Email:</b> {em}<br><b>Phone:</b> {ph}<br><b>Address:</b> {addr}<br>
        <b>Gender:</b> {gen} | <b>Age:</b> {age}<br><b>Marital:</b> {mar} | <b>Status:</b> {sts}<br><b>Referral:</b> {ref}</p>
        <hr><h3 style="color:#13677A">Know God</h3><p>{kg or 'None'}</p>
        <h3 style="color:#13677A">Find Hope</h3><p>{fh or 'None'}</p>
        <h3 style="color:#13677A">Make a Difference</h3><p>{md or 'None'}{f'<br><b>Serve Area:</b> {sa}' if sa else ''}</p>
        <hr><h3 style="color:#13677A">Prayer / Comments</h3>
        <p style="background:#f9f9f9;padding:14px;border-left:4px solid #F2541B">{pr or '—'}</p>
        </div></body></html>"""
        send_email(f"Connect Card: {fn}",get_setting('connect_email','media@oasisnj.net'),html)
        return redirect(url_for('gate', connect='1', name=fn))
    return render_template('connect.html',settings=all_settings())

@app.route('/contact',methods=['GET','POST'])
def contact():
    track('contact'); sent=False
    if request.method=='POST':
        fn=request.form.get('first_name','').strip(); ln=request.form.get('last_name','').strip()
        em=request.form.get('email','').strip(); ph=request.form.get('phone','').strip()
        msg=request.form.get('message','').strip()
        html=f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
        <div style="background:#13677A;padding:20px;text-align:center"><h1 style="color:white;margin:0">Message from Oasis Hub</h1></div>
        <div style="padding:24px;border:1px solid #ddd;border-top:none">
        <p><b>Name:</b> {fn} {ln}<br><b>Email:</b> {em or '—'}<br><b>Phone:</b> {ph or '—'}</p>
        <hr><h3 style="color:#13677A">Message</h3>
        <p style="background:#f9f9f9;padding:14px;border-left:4px solid #13677A">{msg}</p></div></body></html>"""
        send_email(f"Message from {fn} {ln}",get_setting('contact_email','Oasis@OasisNJ.net'),html,reply_to=em)
        sent=True
    return render_template('contact.html',sent=sent,error=None,settings=all_settings())

@app.route('/about')
def about(): track('about'); return render_template('about.html',settings=all_settings())

@app.route('/social')
def social(): track('social'); return render_template('social.html',settings=all_settings())

# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route('/admin/login',methods=['GET','POST'])
def admin_login():
    if session.get('admin_logged_in'): return redirect(url_for('admin_dashboard'))
    error=None
    if request.method=='POST':
        ip = client_ip()
        allowed, wait = _check_rate_limit(ip)
        if not allowed:
            error = f'Too many failed attempts. Try again in {wait // 60 + 1} minute(s).'
            return render_template('admin/login.html', error=error)
        conn=get_db()
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        u=conn.execute("SELECT * FROM admin_users WHERE username=?",(username,)).fetchone()
        if u and verify_password(u['password'], password):
            ensure_admin_password_hash(conn, u, password)
            conn.close()
            _clear_login_attempts(ip)
            session.permanent=True; session['admin_logged_in']=True
            session['admin_user']=u['username']; session['admin_role']=u['role']
            return redirect(url_for('admin_dashboard'))
        conn.close()
        _record_failed_login(ip)
        error='Invalid username or password.'
    return render_template('admin/login.html',error=error)

@app.route('/admin/logout')
def admin_logout(): session.clear(); return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    conn=get_db()
    analytics = get_analytics_snapshot(conn)
    st={
        'leaders':conn.execute("SELECT COUNT(*) FROM leaders").fetchone()[0],
        'beliefs':conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0],
        'ministries':conn.execute("SELECT COUNT(*) FROM ministries").fetchone()[0],
        'submissions':conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
        'prayers':conn.execute("SELECT COUNT(*) FROM prayer_requests").fetchone()[0],
        'events':conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        'views_today': analytics['views_today'],
        'views_total': analytics['total_views'],
        'sessions_today': analytics['sessions_today'],
    }
    recent=conn.execute("SELECT * FROM submissions ORDER BY id DESC LIMIT 5").fetchall()
    top_pages=conn.execute("SELECT page,COUNT(*) cnt FROM analytics GROUP BY page ORDER BY cnt DESC LIMIT 8").fetchall()
    conn.close()
    return render_template('admin/dashboard.html',st=st,recent=recent,top_pages=top_pages)

@app.route('/admin/settings',methods=['GET','POST'])
@login_required
def admin_settings():
    if request.method=='POST':
        conn=get_db()
        for k,v in request.form.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",(k,v.strip()))
        logo=save_upload('logo_file')
        if logo: conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('logo_path',?)",(logo,))
        hero=save_upload('about_hero_file')
        if hero: conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('about_hero',?)",(hero,))
        custom_bg=save_upload('custom_bg_file')
        if custom_bg: conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('ui_background_image',?)",(custom_bg,))
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('primary_cta_enabled',?)",
                     ('1' if request.form.get('primary_cta_enabled') else '0',))
        conn.commit(); conn.close(); flash('Settings saved!','success')
        return redirect(url_for('admin_settings'))
    return render_template('admin/settings.html',settings=all_settings())

@app.route('/admin/page-headers', methods=['GET', 'POST'])
@login_required
def admin_page_headers():
    if request.method == 'POST':
        conn = get_db()
        for key in [
            'about_page_description',
            'feedback_page_description',
            'beliefs_page_description',
            'values_page_description',
            'calendar_page_description',
            'connect_page_description',
            'contact_page_description',
            'leadership_page_description',
            'mission_page_description',
            'beyond_walls_page_description',
            'ministries_page_description',
            'prayer_page_description',
            'serve_page_description',
            'social_page_description',
            'watch_page_description',
            'sermon_notes_page_description',
            'crew_page_description',
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (key, request.form.get(key, '').strip())
            )
        conn.commit()
        conn.close()
        flash('Page header descriptions saved!', 'success')
        return redirect(url_for('admin_page_headers'))
    return render_template('admin/page_headers.html', settings=all_settings())

@app.route('/admin/watch-sermons', methods=['GET', 'POST'])
@login_required
def admin_watch_sermons():
    if request.method == 'POST':
        conn = get_db()
        channel_url = request.form.get('sermon_channel_url', '').strip()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
            ('sermon_channel_url', channel_url)
        )
        for slot in range(1, 11):
            title = request.form.get(f'sermon_video_{slot}_title', '').strip()
            url = request.form.get(f'sermon_video_{slot}_url', '').strip()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (f'sermon_video_{slot}_title', title)
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (f'sermon_video_{slot}_url', url)
            )
        conn.commit()
        conn.close()
        flash('Watch sermons updated!', 'success')
        return redirect(url_for('admin_watch_sermons'))
    return render_template('admin/watch_sermons.html', settings=all_settings(), videos=get_sermon_videos())

@app.route('/admin/hub-cards')
@login_required
def admin_hub_cards():
    conn = get_db()
    cards = conn.execute(
        "SELECT c.*, p.title AS parent_title FROM hub_cards c LEFT JOIN hub_cards p ON p.id = c.parent_id ORDER BY COALESCE(c.parent_id, c.id), c.sort_order, c.id"
    ).fetchall()
    conn.close()
    return render_template('admin/hub_cards.html', cards=cards)

@app.route('/admin/hub-cards/new', methods=['GET', 'POST'])
@login_required
def admin_hub_card_new():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or f'card-{uuid.uuid4().hex[:6]}'
        photo = save_upload('photo') or ''
        modal_image = save_upload('modal_image') or ''
        raw_parent = (request.form.get('parent_id') or '').strip()
        parent_id = int(raw_parent) if raw_parent else None
        conn = get_db()
        card_notice_image = save_upload('card_notice_image') or ''
        conn.execute(
            "INSERT INTO hub_cards (slug,title,subtitle,photo,icon,card_type,parent_id,target_url,media_url,modal_title,modal_body,modal_button_label,modal_button_url,modal_image,open_in_new_tab,sort_order,is_active,card_notice_enabled,card_notice_block,card_notice_title,card_notice_body,card_notice_link,card_notice_link_label,card_notice_image) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                slug,
                title,
                request.form.get('subtitle', '').strip(),
                photo,
                request.form.get('icon', 'bi-grid-fill').strip(),
                request.form.get('card_type', 'link').strip(),
                parent_id,
                request.form.get('target_url', '').strip(),
                request.form.get('media_url', '').strip(),
                request.form.get('modal_title', '').strip(),
                request.form.get('modal_body', '').strip(),
                request.form.get('modal_button_label', '').strip(),
                request.form.get('modal_button_url', '').strip(),
                modal_image,
                1 if request.form.get('open_in_new_tab') else 0,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'hub_cards'),
                1 if request.form.get('is_active') else 0,
                1 if request.form.get('card_notice_enabled') else 0,
                1 if request.form.get('card_notice_block') else 0,
                request.form.get('card_notice_title', '').strip(),
                request.form.get('card_notice_body', '').strip(),
                request.form.get('card_notice_link', '').strip(),
                request.form.get('card_notice_link_label', '').strip() or 'Learn More',
                card_notice_image,
            )
        )
        conn.commit()
        conn.close()
        flash('Hub card added!', 'success')
        return redirect(url_for('admin_hub_cards'))
    conn = get_db()
    parent_choices = conn.execute("SELECT id,title FROM hub_cards WHERE parent_id IS NULL ORDER BY sort_order, id").fetchall()
    conn.close()
    return render_template('admin/hub_card_form.html', card=None, parent_choices=parent_choices)

@app.route('/admin/hub-cards/<int:cid>/edit', methods=['GET', 'POST'])
@login_required
def admin_hub_card_edit(cid):
    conn = get_db()
    card = conn.execute("SELECT * FROM hub_cards WHERE id=?", (cid,)).fetchone()
    if not card:
        conn.close()
        return redirect(url_for('admin_hub_cards'))
    parent_choices = conn.execute("SELECT id,title FROM hub_cards WHERE parent_id IS NULL AND id != ? ORDER BY sort_order, id", (cid,)).fetchall()
    if request.method == 'POST':
        photo = save_upload('photo')
        if photo is None:
            photo = card['photo']
        modal_image = save_upload('modal_image')
        if modal_image is None:
            modal_image = card['modal_image']
        raw_parent = (request.form.get('parent_id') or '').strip()
        parent_id = int(raw_parent) if raw_parent else None
        card_notice_image = save_upload('card_notice_image')
        if card_notice_image is None:
            card_notice_image = card['card_notice_image'] if card['card_notice_image'] else ''
        conn.execute(
            "UPDATE hub_cards SET title=?,subtitle=?,photo=?,icon=?,card_type=?,parent_id=?,target_url=?,media_url=?,modal_title=?,modal_body=?,modal_button_label=?,modal_button_url=?,modal_image=?,open_in_new_tab=?,sort_order=?,is_active=?,card_notice_enabled=?,card_notice_block=?,card_notice_title=?,card_notice_body=?,card_notice_link=?,card_notice_link_label=?,card_notice_image=? WHERE id=?",
            (
                request.form.get('title', '').strip(),
                request.form.get('subtitle', '').strip(),
                photo,
                request.form.get('icon', 'bi-grid-fill').strip(),
                request.form.get('card_type', 'link').strip(),
                parent_id,
                request.form.get('target_url', '').strip(),
                request.form.get('media_url', '').strip(),
                request.form.get('modal_title', '').strip(),
                request.form.get('modal_body', '').strip(),
                request.form.get('modal_button_label', '').strip(),
                request.form.get('modal_button_url', '').strip(),
                modal_image,
                1 if request.form.get('open_in_new_tab') else 0,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'hub_cards'),
                1 if request.form.get('is_active') else 0,
                1 if request.form.get('card_notice_enabled') else 0,
                1 if request.form.get('card_notice_block') else 0,
                request.form.get('card_notice_title', '').strip(),
                request.form.get('card_notice_body', '').strip(),
                request.form.get('card_notice_link', '').strip(),
                request.form.get('card_notice_link_label', '').strip() or 'Learn More',
                card_notice_image,
                cid,
            )
        )
        conn.commit()
        flash('Hub card updated!', 'success')
        card = conn.execute("SELECT * FROM hub_cards WHERE id=?", (cid,)).fetchone()
    conn.close()
    return render_template('admin/hub_card_form.html', card=card, parent_choices=parent_choices)

@app.route('/admin/hub-cards/<int:cid>/delete', methods=['POST'])
@login_required
def admin_hub_card_delete(cid):
    conn = get_db()
    conn.execute("DELETE FROM hub_cards WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    flash('Hub card removed.', 'info')
    return redirect(url_for('admin_hub_cards'))

@app.route('/admin/sermon-notes')
@login_required
def admin_sermon_notes():
    conn = get_db()
    notes = conn.execute("SELECT * FROM sermon_notes ORDER BY note_date DESC, id DESC").fetchall()
    ns_counts = next_steps_counts(conn)
    conn.close()
    return render_template('admin/sermon_notes.html', notes=notes, ns_counts=ns_counts)

def next_steps_heading_fields(note=None):
    """The note-level heading and instruction for the Oasis Next Steps section.
    A submit that never rendered the homework panel must not silently wipe them,
    so an absent field keeps whatever is already stored."""
    def field(name):
        if name not in request.form:
            return row_value(note, name)
        return request.form.get(name, '').strip()

    return field('next_steps_title'), field('next_steps_note')

def sync_next_steps(conn, nid):
    """Apply the Add Homework panel to one note: rename or drop the documents
    already attached, then append whatever was uploaded this time."""
    kept = 0
    for item in note_next_steps(conn, nid):
        if request.form.get(f"ns_remove_{item['id']}") == '1':
            conn.execute("DELETE FROM sermon_next_steps WHERE id=? AND note_id=?", (item['id'], nid))
            continue
        title_key = f"ns_title_{item['id']}"
        if title_key in request.form:
            conn.execute(
                "UPDATE sermon_next_steps SET title=?, sort_order=? WHERE id=? AND note_id=?",
                (request.form.get(title_key, '').strip(), kept, item['id'], nid)
            )
        else:
            conn.execute("UPDATE sermon_next_steps SET sort_order=? WHERE id=? AND note_id=?",
                         (kept, item['id'], nid))
        kept += 1

    for f in request.files.getlist('next_steps_file'):
        if not f or not f.filename:
            continue
        if kept >= MAX_NEXT_STEPS_FILES:
            flash(f'A sermon note can hold {MAX_NEXT_STEPS_FILES} Next Steps files — '
                  f'"{f.filename}" and anything after it were skipped.', 'info')
            break
        stored, _ext = save_document_file(f)
        if not stored:
            flash(f'"{f.filename}" was skipped — only PDF and DOCX files can be attached.', 'info')
            continue
        conn.execute(
            "INSERT INTO sermon_next_steps (note_id, file, title, sort_order) VALUES (?,?,?,?)",
            (nid, stored, sermon_title_from_filename(f.filename), kept)
        )
        kept += 1
    return kept

@app.route('/admin/sermon-notes/new', methods=['GET', 'POST'])
@login_required
def admin_sermon_note_new():
    if request.method == 'POST':
        source_file, ext = save_document_upload('source_file')
        if not source_file:
            flash('Please upload a PDF or DOCX file.', 'info')
            return render_template('admin/sermon_note_form.html', note=None)
        note_date = request.form.get('note_date', '').strip() or date.today().isoformat()
        payload = build_sermon_note_payload(
            source_file,
            ext,
            fallback_title=request.form.get('title', '').strip(),
            summary=request.form.get('summary', '').strip(),
        )
        ns_title, ns_note = next_steps_heading_fields()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO sermon_notes (title,note_date,summary,body_html,source_file,"
            "next_steps_title,next_steps_note) VALUES (?,?,?,?,?,?,?)",
            (payload['title'], note_date, payload['summary'], payload['body_html'], payload['source_file'],
             ns_title, ns_note)
        )
        sync_next_steps(conn, cur.lastrowid)
        conn.commit()
        conn.close()
        flash('Sermon notes imported!', 'success')
        return redirect(url_for('admin_sermon_notes'))
    return render_template('admin/sermon_note_form.html', note=None, ns_items=[])

@app.route('/admin/sermon-notes/<int:nid>/edit', methods=['GET', 'POST'])
@login_required
def admin_sermon_note_edit(nid):
    conn = get_db()
    note = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    if not note:
        conn.close()
        return redirect(url_for('admin_sermon_notes'))
    if request.method == 'POST':
        source_file, ext = save_document_upload('source_file')
        if source_file:
            payload = build_sermon_note_payload(
                source_file,
                ext,
                fallback_title=request.form.get('title', '').strip(),
                summary=request.form.get('summary', '').strip(),
            )
            title = payload['title']
            summary = payload['summary']
            body_html = payload['body_html']
            stored_file = payload['source_file']
        else:
            title = request.form.get('title', '').strip() or note['title']
            summary = request.form.get('summary', '').strip()
            body_html = request.form.get('body_html', '').strip()
            stored_file = note['source_file']
        ns_title, ns_note = next_steps_heading_fields(note)
        conn.execute(
            "UPDATE sermon_notes SET title=?, note_date=?, summary=?, body_html=?, source_file=?,"
            " next_steps_title=?, next_steps_note=? WHERE id=?",
            (
                title,
                request.form.get('note_date', '').strip() or note['note_date'],
                summary,
                body_html,
                stored_file,
                ns_title,
                ns_note,
                nid,
            )
        )
        sync_next_steps(conn, nid)
        conn.commit()
        updated = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
        ns_items = note_next_steps(conn, nid)
        conn.close()
        flash('Sermon notes updated!', 'success')
        return render_template('admin/sermon_note_form.html', note=updated, ns_items=ns_items)
    ns_items = note_next_steps(conn, nid)
    conn.close()
    return render_template('admin/sermon_note_form.html', note=note, ns_items=ns_items)

@app.route('/admin/sermon-notes/<int:nid>/delete', methods=['POST'])
@login_required
def admin_sermon_note_delete(nid):
    conn = get_db()
    conn.execute("DELETE FROM sermon_notes WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    flash('Sermon note removed.', 'info')
    return redirect(url_for('admin_sermon_notes'))

@app.route('/admin/hub-notice', methods=['GET', 'POST'])
@login_required
def admin_hub_notice():
    if request.method == 'POST':
        conn = get_db()
        values = {
            'hub_notice_enabled': '1' if request.form.get('hub_notice_enabled') else '0',
            'hub_notice_title': request.form.get('hub_notice_title', '').strip(),
            'hub_notice_body': request.form.get('hub_notice_body', '').strip(),
            'hub_notice_link': request.form.get('hub_notice_link', '').strip(),
            'hub_notice_link_label': request.form.get('hub_notice_link_label', '').strip() or 'Learn More',
        }
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        image = save_upload('hub_notice_image_file')
        if image:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('hub_notice_image',?)", (image,))
        conn.commit()
        conn.close()
        flash('Hub notice updated!', 'success')
        return redirect(url_for('admin_hub_notice'))
    return render_template('admin/hub_notice.html', settings=all_settings())

@app.route('/admin/get-involved', methods=['GET', 'POST'])
@login_required
def admin_get_involved():
    if request.method == 'POST':
        conn = get_db()
        values = {
            'get_involved_enabled': '1' if request.form.get('get_involved_enabled') else '0',
            'get_involved_title': request.form.get('get_involved_title', '').strip(),
            'get_involved_body': request.form.get('get_involved_body', '').strip(),
            'get_involved_link': request.form.get('get_involved_link', '').strip(),
            'get_involved_link_label': request.form.get('get_involved_link_label', '').strip() or 'Sign Up',
        }
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        if request.form.get('remove_get_involved_image'):
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('get_involved_image','')")
        image = save_upload('get_involved_image_file')
        if image:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('get_involved_image',?)", (image,))
        conn.commit()
        conn.close()
        flash('Get Involved card updated!', 'success')
        return redirect(url_for('admin_get_involved'))
    return render_template('admin/get_involved.html', settings=all_settings())

@app.route('/admin/missions')
@login_required
def admin_missions():
    conn = get_db()
    missions = get_mission_cards(conn)
    conn.close()
    return render_template('admin/missions.html', missions=missions)

@app.route('/admin/missions/new', methods=['GET', 'POST'])
@login_required
def admin_mission_new():
    if request.method == 'POST':
        cover = save_upload('cover_photo') or ''
        conn = get_db()
        conn.execute(
            "INSERT INTO missions (title,summary,body,cover_photo,sort_order) VALUES (?,?,?,?,?)",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'missions'),
            )
        )
        conn.commit()
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        flash('Mission created! Add images below.', 'success')
        return redirect(url_for('admin_mission_edit', mid=mid))
    return render_template('admin/mission_form.html', mission=None, images=[])

@app.route('/admin/missions/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def admin_mission_edit(mid):
    conn = get_db()
    mission = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    if not mission:
        conn.close()
        return redirect(url_for('admin_missions'))
    if request.method == 'POST':
        cover = save_upload('cover_photo')
        if cover is None:
            cover = mission['cover_photo']
        conn.execute(
            "UPDATE missions SET title=?,summary=?,body=?,cover_photo=?,sort_order=? WHERE id=?",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else mission['sort_order'],
                mid,
            )
        )
        conn.commit()
        flash('Mission updated!', 'success')
        mission = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    images = conn.execute(
        "SELECT * FROM mission_images WHERE mission_id=? ORDER BY sort_order, id",
        (mid,)
    ).fetchall()
    conn.close()
    return render_template('admin/mission_form.html', mission=mission, images=images)

@app.route('/admin/missions/<int:mid>/delete', methods=['POST'])
@login_required
def admin_mission_delete(mid):
    conn = get_db()
    conn.execute("DELETE FROM missions WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    flash('Mission removed.', 'info')
    return redirect(url_for('admin_missions'))

@app.route('/admin/missions/<int:mid>/images/new', methods=['POST'])
@login_required
def admin_mission_image_new(mid):
    conn = get_db()
    mission = conn.execute("SELECT id FROM missions WHERE id=?", (mid,)).fetchone()
    if not mission:
        conn.close()
        return redirect(url_for('admin_missions'))
    image_count = conn.execute("SELECT COUNT(*) FROM mission_images WHERE mission_id=?", (mid,)).fetchone()[0]
    files = [f for f in request.files.getlist('photo') if f and f.filename]
    if not files:
        conn.close()
        flash('Please choose at least one image.', 'info')
        return redirect(url_for('admin_mission_edit', mid=mid))
    caption = request.form.get('caption', '').strip()
    next_sort = (conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM mission_images WHERE mission_id=?", (mid,)).fetchone()[0] or 0) + 1
    saved = 0
    for f in files:
        photo = _save_file_obj(f)
        if photo:
            conn.execute(
                "INSERT INTO mission_images (mission_id,photo,caption,sort_order) VALUES (?,?,?,?)",
                (mid, photo, caption, next_sort + saved)
            )
            saved += 1
    conn.commit()
    conn.close()
    flash(f'{saved} image{"s" if saved != 1 else ""} added!', 'success')
    return redirect(url_for('admin_mission_edit', mid=mid))

@app.route('/admin/missions/<int:mid>/images/<int:iid>/delete', methods=['POST'])
@login_required
def admin_mission_image_delete(mid, iid):
    conn = get_db()
    conn.execute("DELETE FROM mission_images WHERE id=? AND mission_id=?", (iid, mid))
    conn.commit()
    conn.close()
    flash('Mission image removed.', 'info')
    return redirect(url_for('admin_mission_edit', mid=mid))

# Beyond the Walls
@app.route('/admin/beyond-the-walls')
@login_required
def admin_beyond_walls():
    conn = get_db()
    items = get_beyond_wall_cards(conn)
    conn.close()
    return render_template('admin/beyond_walls.html', items=items)

@app.route('/admin/beyond-the-walls/new', methods=['GET', 'POST'])
@login_required
def admin_beyond_wall_new():
    if request.method == 'POST':
        cover = save_upload('cover_photo') or ''
        youtube_url = request.form.get('youtube_url', '').strip()
        youtube_video_id = extract_youtube_video_id(youtube_url)
        if youtube_url and not youtube_video_id:
            flash("Couldn't recognize that YouTube link — video not saved.", 'info')
        conn = get_db()
        conn.execute(
            "INSERT INTO beyond_walls (title,summary,body,cover_photo,sort_order,youtube_video_id) VALUES (?,?,?,?,?,?)",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'beyond_walls'),
                youtube_video_id,
            )
        )
        conn.commit()
        bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        flash('Saved! Add images below.', 'success')
        return redirect(url_for('admin_beyond_wall_edit', bid=bid))
    return render_template('admin/beyond_wall_form.html', item=None, images=[])

@app.route('/admin/beyond-the-walls/<int:bid>/edit', methods=['GET', 'POST'])
@login_required
def admin_beyond_wall_edit(bid):
    conn = get_db()
    item = conn.execute("SELECT * FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    if not item:
        conn.close()
        return redirect(url_for('admin_beyond_walls'))
    if request.method == 'POST':
        cover = save_upload('cover_photo')
        if cover is None:
            cover = item['cover_photo']
        youtube_url = request.form.get('youtube_url', '').strip()
        youtube_video_id = extract_youtube_video_id(youtube_url) if youtube_url else ''
        if youtube_url and not youtube_video_id:
            flash("Couldn't recognize that YouTube link — video not saved.", 'info')
            youtube_video_id = item['youtube_video_id']
        conn.execute(
            "UPDATE beyond_walls SET title=?,summary=?,body=?,cover_photo=?,sort_order=?,youtube_video_id=? WHERE id=?",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else item['sort_order'],
                youtube_video_id,
                bid,
            )
        )
        conn.commit()
        flash('Updated!', 'success')
        item = conn.execute("SELECT * FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    images = conn.execute(
        "SELECT * FROM beyond_wall_images WHERE beyond_id=? ORDER BY sort_order, id",
        (bid,)
    ).fetchall()
    conn.close()
    return render_template('admin/beyond_wall_form.html', item=item, images=images)

@app.route('/admin/beyond-the-walls/<int:bid>/delete', methods=['POST'])
@login_required
def admin_beyond_wall_delete(bid):
    conn = get_db()
    conn.execute("DELETE FROM beyond_walls WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    flash('Removed.', 'info')
    return redirect(url_for('admin_beyond_walls'))

@app.route('/admin/beyond-the-walls/<int:bid>/images/new', methods=['POST'])
@login_required
def admin_beyond_wall_image_new(bid):
    conn = get_db()
    exists = conn.execute("SELECT id FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    if not exists:
        conn.close()
        return redirect(url_for('admin_beyond_walls'))
    image_count = conn.execute("SELECT COUNT(*) FROM beyond_wall_images WHERE beyond_id=?", (bid,)).fetchone()[0]
    files = [f for f in request.files.getlist('photo') if f and f.filename]
    if not files:
        conn.close()
        flash('Choose at least one image.', 'info')
        return redirect(url_for('admin_beyond_wall_edit', bid=bid))
    remaining = MAX_BEYOND_WALL_IMAGES - image_count
    if remaining <= 0:
        conn.close()
        flash(f'This story already has the max of {MAX_BEYOND_WALL_IMAGES} images.', 'info')
        return redirect(url_for('admin_beyond_wall_edit', bid=bid))
    if len(files) > remaining:
        flash(f'Only {remaining} more image{"s" if remaining != 1 else ""} fit under the {MAX_BEYOND_WALL_IMAGES}-image cap — the rest were skipped.', 'info')
        files = files[:remaining]
    caption = request.form.get('caption', '').strip()
    next_sort = (conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM beyond_wall_images WHERE beyond_id=?", (bid,)).fetchone()[0] or 0) + 1
    saved = 0
    for f in files:
        photo = _save_file_obj(f)
        if photo:
            conn.execute(
                "INSERT INTO beyond_wall_images (beyond_id,photo,caption,sort_order) VALUES (?,?,?,?)",
                (bid, photo, caption, next_sort + saved)
            )
            saved += 1
            conn.commit()  # commit per image so a slow/interrupted batch keeps whatever finished
    conn.close()
    flash(f'{saved} image{"s" if saved != 1 else ""} added!', 'success')
    return redirect(url_for('admin_beyond_wall_edit', bid=bid))

@app.route('/admin/beyond-the-walls/<int:bid>/images/<int:iid>/delete', methods=['POST'])
@login_required
def admin_beyond_wall_image_delete(bid, iid):
    conn = get_db()
    conn.execute("DELETE FROM beyond_wall_images WHERE id=? AND beyond_id=?", (iid, bid))
    conn.commit()
    conn.close()
    flash('Image removed.', 'info')
    return redirect(url_for('admin_beyond_wall_edit', bid=bid))

# Leaders
@app.route('/admin/leaders')
@login_required
def admin_leaders():
    conn=get_db(); l=conn.execute("SELECT * FROM leaders ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/leaders.html',leaders=l)

@app.route('/admin/leaders/new',methods=['GET','POST'])
@login_required
def admin_leader_new():
    if request.method=='POST':
        photo=save_upload('photo') or ''
        conn=get_db(); conn.execute("INSERT INTO leaders (name,role,bio,photo,sort_order) VALUES (?,?,?,?,?)",
            (request.form['name'].strip(),request.form['role'].strip(),request.form.get('bio','').strip(),photo,int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'leaders')))
        conn.commit(); conn.close(); flash('Leader added!','success'); return redirect(url_for('admin_leaders'))
    return render_template('admin/leader_form.html',leader=None)

@app.route('/admin/leaders/<int:lid>/edit',methods=['GET','POST'])
@login_required
def admin_leader_edit(lid):
    conn = get_db()
    l = conn.execute("SELECT * FROM leaders WHERE id=?",(lid,)).fetchone()
    if not l:
        conn.close()
        return redirect(url_for('admin_leaders'))
    if request.method == 'POST':
        try:
            photo = save_upload('photo')   # None if no new file
            if photo is None:
                photo = l['photo']         # keep existing photo
            conn.execute(
                "UPDATE leaders SET name=?,role=?,bio=?,photo=?,sort_order=? WHERE id=?",
                (request.form['name'].strip(), request.form['role'].strip(),
                 request.form.get('bio','').strip(), photo,
                 int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else l['sort_order'], lid)
            )
            conn.commit()
            flash('Leader updated!', 'success')
        except Exception as e:
            app.logger.error(f"leader_edit error: {e}")
            flash(f'Error saving: {e}', 'info')
        finally:
            conn.close()
        return redirect(url_for('admin_leaders'))
    conn.close()
    return render_template('admin/leader_form.html', leader=l)

@app.route('/admin/leaders/<int:lid>/delete',methods=['POST'])
@login_required
def admin_leader_delete(lid):
    conn=get_db(); conn.execute("DELETE FROM leaders WHERE id=?",(lid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_leaders'))

# Behind the Scene
@app.route('/admin/behind-the-scene')
@login_required
def admin_behind_scenes():
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scenes = conn.execute("SELECT * FROM behind_scenes ORDER BY sort_order, name").fetchall()
    counts = get_people_count_by_scene(conn)
    people = get_crew_people(conn)
    conn.close()
    return render_template('admin/behind_scenes.html', scenes=scenes, member_counts=counts, people=people)

@app.route('/admin/behind-the-scene/new', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_new():
    return redirect(url_for('admin_behind_scene_person_new'))

@app.route('/admin/behind-the-scene/<int:sid>/edit', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_edit(sid):
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene = conn.execute("SELECT * FROM behind_scenes WHERE id=?", (sid,)).fetchone()
    if not scene:
        conn.close()
        return redirect(url_for('admin_behind_scenes'))
    if request.method == 'POST':
        photo = save_upload('photo')
        if photo is None:
            photo = scene['photo']
        conn.execute(
            "UPDATE behind_scenes SET photo=? WHERE id=?",
            (
                photo,
                sid,
            )
        )
        conn.commit()
        flash("Oasis Crew ministry updated!", 'success')
        scene = conn.execute("SELECT * FROM behind_scenes WHERE id=?", (sid,)).fetchone()
    members = get_people_for_scene(conn, sid)
    conn.close()
    return render_template('admin/behind_scene_form.html', scene=scene, members=members)

@app.route('/admin/behind-the-scene/<int:sid>/delete', methods=['POST'])
@login_required
def admin_behind_scene_delete(sid):
    flash("Delete ministries from the Ministries admin section. Oasis Crew mirrors that list automatically.", 'info')
    return redirect(url_for('admin_ministries'))

@app.route('/admin/behind-the-scene/members/new', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_person_new():
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene_choices = get_scene_choices(conn)
    default_scene_id = request.args.get('scene_id', '').strip()
    default_scene_id = int(default_scene_id) if default_scene_id.isdigit() else None
    next_sort_order = next_available_sort_order(conn, 'behind_scene_people')
    if request.method == 'POST':
        photo = save_upload('photo') or ''
        conn.execute(
            "INSERT INTO behind_scene_people (name,photo,sort_order) VALUES (?,?,?)",
            (
                request.form['name'].strip(),
                photo,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'behind_scene_people'),
            )
        )
        person_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        slots = assignment_slots_from_form(request.form)
        if not slots and default_scene_id:
            slots = [{'scene_id': default_scene_id, 'role': request.form.get('assignment_1_role', '').strip(), 'slot_order': 1}]
        replace_person_assignments(conn, person_id, slots)
        conn.commit()
        conn.close()
        flash('Member added!', 'success')
        return redirect(url_for('admin_behind_scenes'))
    conn.close()
    return render_template(
        'admin/behind_scene_person_form.html',
        scene=None,
        member=None,
        assignments=[],
        scene_choices=scene_choices,
        default_scene_id=default_scene_id,
        display_order=next_sort_order,
    )

@app.route('/admin/behind-the-scene/members/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_person_edit(mid):
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene_choices = get_scene_choices(conn)
    member, assignments = get_person_with_assignments(conn, mid)
    if not member:
        conn.close()
        return redirect(url_for('admin_behind_scenes'))
    if request.method == 'POST':
        photo = save_upload('photo')
        if photo is None:
            photo = member['photo']
        conn.execute(
            "UPDATE behind_scene_people SET name=?,photo=?,sort_order=? WHERE id=?",
            (
                request.form['name'].strip(),
                photo,
                int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else member['sort_order'],
                mid,
            )
        )
        slots = assignment_slots_from_form(request.form)
        replace_person_assignments(conn, mid, slots)
        conn.commit()
        conn.close()
        flash('Member updated!', 'success')
        return redirect(url_for('admin_behind_scenes'))
    conn.close()
    return render_template(
        'admin/behind_scene_person_form.html',
        scene=None,
        member=member,
        assignments=assignments,
        scene_choices=scene_choices,
        default_scene_id=None,
        display_order=member['sort_order'],
    )

@app.route('/admin/behind-the-scene/members/<int:mid>/delete', methods=['POST'])
@login_required
def admin_behind_scene_person_delete(mid):
    conn = get_db()
    conn.execute("DELETE FROM behind_scene_people WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    flash('Member removed.', 'info')
    return redirect(url_for('admin_behind_scenes'))

@app.route('/admin/behind-the-scene/<int:sid>/members/new', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_member_new(sid):
    return redirect(url_for('admin_behind_scene_person_new', scene_id=sid))

@app.route('/admin/behind-the-scene/<int:sid>/members/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_member_edit(sid, mid):
    return redirect(url_for('admin_behind_scene_person_edit', mid=mid))

@app.route('/admin/behind-the-scene/<int:sid>/members/<int:mid>/delete', methods=['POST'])
@login_required
def admin_behind_scene_member_delete(sid, mid):
    return redirect(url_for('admin_behind_scene_person_delete', mid=mid))

# Beliefs
@app.route('/admin/beliefs')
@login_required
def admin_beliefs():
    conn=get_db(); items=conn.execute("SELECT * FROM beliefs ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/beliefs.html',beliefs=items, values=[])

@app.route('/admin/beliefs/new',methods=['GET','POST'])
@login_required
def admin_belief_new():
    if request.method=='POST':
        conn=get_db(); conn.execute("INSERT INTO beliefs (title,body,scripture,sort_order) VALUES (?,?,?,?)",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'beliefs')))
        conn.commit(); conn.close(); flash('Added!','success'); return redirect(url_for('admin_beliefs'))
    return render_template('admin/belief_form.html',belief=None)

@app.route('/admin/beliefs/<int:bid>/edit',methods=['GET','POST'])
@login_required
def admin_belief_edit(bid):
    conn=get_db(); b=conn.execute("SELECT * FROM beliefs WHERE id=?",(bid,)).fetchone()
    if not b: conn.close(); return redirect(url_for('admin_beliefs'))
    if request.method=='POST':
        conn.execute("UPDATE beliefs SET title=?,body=?,scripture=?,sort_order=? WHERE id=?",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else b['sort_order'],bid))
        conn.commit(); conn.close(); flash('Updated!','success'); return redirect(url_for('admin_beliefs'))
    images = conn.execute("SELECT * FROM belief_images WHERE belief_id=? ORDER BY sort_order, id", (bid,)).fetchall()
    conn.close(); return render_template('admin/belief_form.html',belief=b,images=images)

@app.route('/admin/beliefs/<int:bid>/delete',methods=['POST'])
@login_required
def admin_belief_delete(bid):
    conn=get_db(); conn.execute("DELETE FROM beliefs WHERE id=?",(bid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_beliefs'))

@app.route('/admin/beliefs/<int:bid>/images/new',methods=['POST'])
@login_required
def admin_belief_image_new(bid):
    conn=get_db()
    image_count = conn.execute("SELECT COUNT(*) FROM belief_images WHERE belief_id=?", (bid,)).fetchone()[0]
    if image_count >= 10:
        conn.close(); flash('Each belief can have up to 10 images.', 'info'); return redirect(url_for('admin_belief_edit', bid=bid))
    photo = save_upload('photo')
    if not photo:
        conn.close(); flash('Choose an image first.', 'info'); return redirect(url_for('admin_belief_edit', bid=bid))
    conn.execute(
        "INSERT INTO belief_images (belief_id,photo,caption,sort_order) VALUES (?,?,?,?)",
        (bid, photo, request.form.get('caption','').strip(), int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order_for_parent(conn, 'belief_images', 'belief_id', bid))
    )
    conn.commit(); conn.close(); flash('Belief image added!', 'success'); return redirect(url_for('admin_belief_edit', bid=bid))

@app.route('/admin/beliefs/<int:bid>/images/<int:iid>/delete',methods=['POST'])
@login_required
def admin_belief_image_delete(bid, iid):
    conn=get_db(); conn.execute("DELETE FROM belief_images WHERE id=? AND belief_id=?", (iid, bid)); conn.commit(); conn.close()
    flash('Belief image removed.','info'); return redirect(url_for('admin_belief_edit', bid=bid))

@app.route('/admin/values')
@login_required
def admin_values():
    conn=get_db(); items=conn.execute("SELECT * FROM values_items ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/values.html',values=items)

@app.route('/admin/values/new',methods=['GET','POST'])
@login_required
def admin_value_new():
    if request.method=='POST':
        conn=get_db(); conn.execute("INSERT INTO values_items (title,body,scripture,sort_order) VALUES (?,?,?,?)",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'values_items')))
        conn.commit(); conn.close(); flash('Added!','success'); return redirect(url_for('admin_values'))
    return render_template('admin/value_form.html',value=None)

@app.route('/admin/values/<int:vid>/edit',methods=['GET','POST'])
@login_required
def admin_value_edit(vid):
    conn=get_db(); v=conn.execute("SELECT * FROM values_items WHERE id=?",(vid,)).fetchone()
    if not v: conn.close(); return redirect(url_for('admin_values'))
    if request.method=='POST':
        conn.execute("UPDATE values_items SET title=?,body=?,scripture=?,sort_order=? WHERE id=?",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else v['sort_order'],vid))
        conn.commit(); conn.close(); flash('Updated!','success'); return redirect(url_for('admin_values'))
    images = conn.execute("SELECT * FROM value_images WHERE value_id=? ORDER BY sort_order, id", (vid,)).fetchall()
    conn.close(); return render_template('admin/value_form.html',value=v,images=images)

@app.route('/admin/values/<int:vid>/delete',methods=['POST'])
@login_required
def admin_value_delete(vid):
    conn=get_db(); conn.execute("DELETE FROM values_items WHERE id=?",(vid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_values'))

@app.route('/admin/values/<int:vid>/images/new',methods=['POST'])
@login_required
def admin_value_image_new(vid):
    conn=get_db()
    image_count = conn.execute("SELECT COUNT(*) FROM value_images WHERE value_id=?", (vid,)).fetchone()[0]
    if image_count >= 10:
        conn.close(); flash('Each value can have up to 10 images.', 'info'); return redirect(url_for('admin_value_edit', vid=vid))
    photo = save_upload('photo')
    if not photo:
        conn.close(); flash('Choose an image first.', 'info'); return redirect(url_for('admin_value_edit', vid=vid))
    conn.execute(
        "INSERT INTO value_images (value_id,photo,caption,sort_order) VALUES (?,?,?,?)",
        (vid, photo, request.form.get('caption','').strip(), int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order_for_parent(conn, 'value_images', 'value_id', vid))
    )
    conn.commit(); conn.close(); flash('Value image added!', 'success'); return redirect(url_for('admin_value_edit', vid=vid))

@app.route('/admin/values/<int:vid>/images/<int:iid>/delete',methods=['POST'])
@login_required
def admin_value_image_delete(vid, iid):
    conn=get_db(); conn.execute("DELETE FROM value_images WHERE id=? AND value_id=?", (iid, vid)); conn.commit(); conn.close()
    flash('Value image removed.','info'); return redirect(url_for('admin_value_edit', vid=vid))

# Ministries
@app.route('/admin/ministries')
@login_required
def admin_ministries():
    conn=get_db(); items=conn.execute("SELECT * FROM ministries ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/ministries.html',ministries=items)

@app.route('/admin/ministries/new',methods=['GET','POST'])
@login_required
def admin_ministry_new():
    if request.method=='POST':
        conn=get_db(); photo = save_upload('photo')
        conn.execute("INSERT INTO ministries (name,description,url,icon,photo,sort_order) VALUES (?,?,?,?,?,?)",
            (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('url','').strip(),request.form.get('icon','bi-people-fill').strip(),photo or '',int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'ministries')))
        conn.commit(); conn.close(); flash('Added!','success'); return redirect(url_for('admin_ministries'))
    return render_template('admin/ministry_form.html',ministry=None)

@app.route('/admin/ministries/<int:mid>/edit',methods=['GET','POST'])
@login_required
def admin_ministry_edit(mid):
    conn=get_db(); m=conn.execute("SELECT * FROM ministries WHERE id=?",(mid,)).fetchone()
    if not m: conn.close(); return redirect(url_for('admin_ministries'))
    if request.method=='POST':
        photo = save_upload('photo')
        if photo is None:
            photo = m['photo'] if 'photo' in m.keys() else ''
        conn.execute("UPDATE ministries SET name=?,description=?,url=?,icon=?,photo=?,sort_order=? WHERE id=?",
            (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('url','').strip(),request.form.get('icon','bi-people-fill').strip(),photo or '',int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else m['sort_order'],mid))
        conn.commit(); conn.close(); flash('Updated!','success'); return redirect(url_for('admin_ministries'))
    conn.close(); return render_template('admin/ministry_form.html',ministry=m)

@app.route('/admin/ministries/<int:mid>/delete',methods=['POST'])
@login_required
def admin_ministry_delete(mid):
    conn=get_db(); conn.execute("DELETE FROM ministries WHERE id=?",(mid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_ministries'))

# Serve
@app.route('/admin/serve')
@login_required
def admin_serve():
    conn=get_db()
    cats=conn.execute("SELECT * FROM serve_categories ORDER BY sort_order").fetchall()
    roles=conn.execute("SELECT * FROM serve_roles ORDER BY sort_order").fetchall(); conn.close()
    grouped=[{'cat':c,'roles':[r for r in roles if r['category_id']==c['id']]} for c in cats]
    return render_template('admin/serve.html',grouped=grouped)

@app.route('/admin/serve/category/new',methods=['POST'])
@login_required
def admin_serve_cat_new():
    photo = save_upload('photo') or ''
    conn=get_db(); conn.execute("INSERT INTO serve_categories (name,description,icon,color,photo,sort_order) VALUES (?,?,?,?,?,?)",
        (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('icon','bi-people-fill').strip(),request.form.get('color','teal'),photo,int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'serve_categories')))
    conn.commit(); conn.close(); flash('Category added!','success'); return redirect(url_for('admin_serve'))

@app.route('/admin/serve/category/<int:cid>/edit',methods=['GET','POST'])
@login_required
def admin_serve_cat_edit(cid):
    conn=get_db(); cat=conn.execute("SELECT * FROM serve_categories WHERE id=?",(cid,)).fetchone()
    if not cat: conn.close(); return redirect(url_for('admin_serve'))
    if request.method=='POST':
        photo = save_upload('photo')
        if photo is None:
            photo = cat['photo']
        conn.execute("UPDATE serve_categories SET name=?,description=?,icon=?,color=?,photo=?,sort_order=? WHERE id=?",
            (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('icon','bi-people-fill').strip(),request.form.get('color','teal'),photo,int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else cat['sort_order'],cid))
        conn.commit(); conn.close(); flash('Category updated!','success'); return redirect(url_for('admin_serve'))
    conn.close(); return render_template('admin/serve_cat_form.html',cat=cat)

@app.route('/admin/serve/category/<int:cid>/delete',methods=['POST'])
@login_required
def admin_serve_cat_delete(cid):
    conn=get_db(); conn.execute("DELETE FROM serve_roles WHERE category_id=?",(cid,)); conn.execute("DELETE FROM serve_categories WHERE id=?",(cid,))
    conn.commit(); conn.close(); flash('Category deleted.','info'); return redirect(url_for('admin_serve'))

@app.route('/admin/serve/role/new',methods=['POST'])
@login_required
def admin_serve_role_new():
    conn=get_db(); conn.execute("INSERT INTO serve_roles (category_id,label,sort_order) VALUES (?,?,?)",
        (int(request.form['category_id']),request.form['label'].strip(),int(request.form.get('sort_order')) if (request.form.get('sort_order') or '').strip() else next_available_sort_order(conn, 'serve_roles')))
    conn.commit(); conn.close(); return redirect(url_for('admin_serve'))

@app.route('/admin/serve/role/<int:rid>/delete',methods=['POST'])
@login_required
def admin_serve_role_delete(rid):
    conn=get_db(); conn.execute("DELETE FROM serve_roles WHERE id=?",(rid,)); conn.commit(); conn.close()
    return redirect(url_for('admin_serve'))

# Events
@app.route('/admin/events')
@login_required
def admin_events():
    conn=get_db(); ev=conn.execute("SELECT * FROM events ORDER BY event_date ASC").fetchall(); conn.close()
    return render_template('admin/events.html',events=ev)

@app.route('/admin/events/new',methods=['GET','POST'])
@login_required
def admin_event_new():
    if request.method=='POST':
        rec = request.form.get('recurrence','none')
        rec_detail = ''
        if rec == 'weekly':
            positions = [p for p in ['1st','2nd','3rd','4th','last'] if request.form.get(f'pos_{p}')]
            weekday   = request.form.get('recurrence_weekday','Sunday')
            rec_detail = ','.join(positions) + '-' + weekday if positions else weekday
        conn=get_db(); conn.execute(
            "INSERT INTO events (title,description,event_date,event_time,location,signup_url,auto_delete,recurrence,recurrence_detail,sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request.form['title'].strip(), request.form.get('description','').strip(),
             request.form['event_date'], request.form.get('event_time','').strip(),
             request.form.get('location','').strip(), request.form.get('signup_url','').strip(),
             1 if request.form.get('auto_delete') else 0,
             rec, rec_detail,
             int(request.form.get('sort_order') or 0)))
        conn.commit(); conn.close(); flash('Event added!','success'); return redirect(url_for('admin_events'))
    return render_template('admin/event_form.html',event=None)

@app.route('/admin/events/<int:eid>/edit',methods=['GET','POST'])
@login_required
def admin_event_edit(eid):
    conn=get_db(); ev=conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone()
    if not ev: conn.close(); return redirect(url_for('admin_events'))
    if request.method=='POST':
        rec = request.form.get('recurrence','none')
        rec_detail = ''
        if rec == 'weekly':
            positions = [p for p in ['1st','2nd','3rd','4th','last'] if request.form.get(f'pos_{p}')]
            weekday   = request.form.get('recurrence_weekday','Sunday')
            rec_detail = ','.join(positions) + '-' + weekday if positions else weekday
        conn.execute(
            "UPDATE events SET title=?,description=?,event_date=?,event_time=?,location=?,signup_url=?,auto_delete=?,recurrence=?,recurrence_detail=?,sort_order=? WHERE id=?",
            (request.form['title'].strip(), request.form.get('description','').strip(),
             request.form['event_date'], request.form.get('event_time','').strip(),
             request.form.get('location','').strip(), request.form.get('signup_url','').strip(),
             1 if request.form.get('auto_delete') else 0,
             rec, rec_detail,
             int(request.form.get('sort_order') or 0), eid))
        conn.commit(); conn.close(); flash('Event updated!','success'); return redirect(url_for('admin_events'))
    conn.close(); return render_template('admin/event_form.html',event=ev)

@app.route('/admin/events/<int:eid>/delete',methods=['POST'])
@login_required
def admin_event_delete(eid):
    conn=get_db(); conn.execute("DELETE FROM events WHERE id=?",(eid,)); conn.commit(); conn.close()
    flash('Event removed.','info'); return redirect(url_for('admin_events'))

# Submissions + Prayers
@app.route('/admin/submissions')
@login_required
def admin_submissions():
    conn=get_db(); s=conn.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall(); conn.close()
    return render_template('admin/submissions.html',subs=s)

@app.route('/admin/submissions/<int:sid>')
@login_required
def admin_submission_detail(sid):
    conn=get_db(); s=conn.execute("SELECT * FROM submissions WHERE id=?",(sid,)).fetchone(); conn.close()
    if not s: return redirect(url_for('admin_submissions'))
    return render_template('admin/submission_detail.html',sub=s)

@app.route('/admin/prayers')
@login_required
def admin_prayers():
    conn=get_db(); p=conn.execute("SELECT * FROM prayer_requests ORDER BY id DESC").fetchall(); conn.close()
    return render_template('admin/prayers.html',prayers=p)

@app.route('/admin/feedback')
@login_required
def admin_feedback():
    conn=get_db()
    fb=conn.execute("SELECT * FROM app_feedback ORDER BY id DESC").fetchall()
    stats=conn.execute("SELECT ROUND(AVG(rating),1) avg_rating, COUNT(*) rated FROM app_feedback WHERE rating>0").fetchone()
    conn.close()
    return render_template('admin/feedback.html',feedback=fb,avg_rating=stats['avg_rating'] or 0,rated=stats['rated'],total=len(fb))

@app.route('/admin/feedback/<int:fid>/delete',methods=['POST'])
@login_required
def admin_feedback_delete(fid):
    conn=get_db(); conn.execute("DELETE FROM app_feedback WHERE id=?",(fid,)); conn.commit(); conn.close()
    flash('Feedback entry deleted.','success')
    return redirect(url_for('admin_feedback'))

# Analytics
@app.route('/admin/analytics/clear', methods=['POST'])
@login_required
def admin_analytics_clear():
    conn = get_db()
    conn.execute("DELETE FROM analytics")
    conn.commit()
    conn.close()
    flash('Analytics data cleared.', 'success')
    return redirect(url_for('admin_analytics'))

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    conn=get_db()
    snapshot = get_analytics_snapshot(conn)
    by_page=conn.execute("SELECT page,COUNT(*) cnt FROM analytics GROUP BY page ORDER BY cnt DESC").fetchall()
    by_day=conn.execute("SELECT substr(ts,1,10) day,COUNT(*) cnt FROM analytics GROUP BY day ORDER BY day DESC LIMIT 30").fetchall()
    by_device=conn.execute("""
        SELECT sid,
               COUNT(*) views,
               COUNT(DISTINCT page) pages,
               MIN(ts) first_seen,
               MAX(ts) last_seen,
               (SELECT ua FROM analytics a3 WHERE a3.sid=a1.sid ORDER BY a3.ts DESC LIMIT 1) ua
        FROM analytics a1
        GROUP BY sid
        ORDER BY last_seen DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return render_template(
        'admin/analytics.html',
        total=snapshot['total_views'],
        today=snapshot['views_today'],
        week=snapshot['views_week'],
        sessions=snapshot['sessions_total'],
        sessions_today=snapshot['sessions_today'],
        sessions_week=snapshot['sessions_week'],
        by_page=by_page,
        by_day=by_day,
        by_device=by_device,
    )

@app.route('/admin/analytics/device/<sid>')
@login_required
def admin_analytics_device(sid):
    conn=get_db()
    rows=conn.execute(
        "SELECT ts,page,ua FROM analytics WHERE sid=? ORDER BY ts DESC LIMIT 300",
        (sid,)
    ).fetchall()
    conn.close()
    if not rows:
        flash('No analytics data found for that device.', 'error')
        return redirect(url_for('admin_analytics'))
    return render_template('admin/analytics_device.html', sid=sid, rows=rows)

@app.route('/admin/security')
@superadmin_required
def admin_security():
    conn = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    banned = conn.execute(
        "SELECT ip,banned_at,expires_at,reason FROM banned_ips WHERE expires_at > ? ORDER BY banned_at DESC",
        (now,)
    ).fetchall()
    conn.close()
    return render_template('admin/security.html', banned=banned, ban_days=BAN_DAYS)

@app.route('/admin/security/unban', methods=['POST'])
@superadmin_required
def admin_security_unban():
    ip = request.form.get('ip', '').strip()
    if ip:
        conn = get_db()
        conn.execute("DELETE FROM banned_ips WHERE ip=?", (ip,))
        conn.commit()
        conn.close()
        flash(f'{ip} unbanned.', 'success')
    return redirect(url_for('admin_security'))

# Users
@app.route('/admin/users')
@superadmin_required
def admin_users():
    conn=get_db(); u=conn.execute("SELECT * FROM admin_users ORDER BY id").fetchall(); conn.close()
    return render_template('admin/users.html',users=u)

@app.route('/admin/users/new',methods=['GET','POST'])
@superadmin_required
def admin_user_new():
    if request.method=='POST':
        uname=request.form.get('username','').strip(); pw=request.form.get('password','').strip()
        role=request.form.get('role','editor')
        if not uname or not pw: flash('Username and password required.','info')
        else:
            conn=get_db()
            try:
                conn.execute("INSERT INTO admin_users (username,password,role) VALUES (?,?,?)",(uname,hash_password(pw),role))
                conn.commit(); flash('User created!','success')
            except sqlite3.IntegrityError: flash('Username already taken.','info')
            conn.close()
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html',user=None)

@app.route('/admin/users/<int:uid>/delete',methods=['POST'])
@superadmin_required
def admin_user_delete(uid):
    if uid==1: flash("Can't delete the primary admin.",'info')
    else:
        conn=get_db(); conn.execute("DELETE FROM admin_users WHERE id=?",(uid,)); conn.commit(); conn.close()
        flash('User removed.','info')
    return redirect(url_for('admin_users'))

@app.route('/admin/password',methods=['GET','POST'])
@login_required
def admin_password():
    if request.method=='POST':
        pw=request.form.get('new_password','').strip()
        if len(pw)<6: flash('Min 6 characters.','info')
        else:
            conn=get_db(); conn.execute("UPDATE admin_users SET password=? WHERE username=?",(hash_password(pw),session['admin_user'])); conn.commit(); conn.close()
            flash('Password updated!','success')
    return render_template('admin/password.html')

@app.errorhandler(404)
def not_found(e): return redirect(url_for('hub'))
@app.errorhandler(500)
def server_error(e):
    app.logger.error(f"500 error: {e}")
    if request.path.startswith('/admin'):
        return f"""<html><body style="font-family:sans-serif;padding:40px;color:#333;">
        <h2 style="color:#c0392b;">&#9888; Server Error</h2>
        <p>{e}</p>
        <p>Check <code>error.log</code> on the Pi for the full traceback.</p>
        <a href="/admin" style="color:#13677A;">&larr; Back to Admin</a>
        </body></html>""", 500
    return redirect(url_for('hub'))

init_db()

if __name__=='__main__':
    app.run(debug=False,host='0.0.0.0',port=5500)
