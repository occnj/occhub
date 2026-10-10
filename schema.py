"""Database schema + migrations.

init_db() is safe to run on every start and from several gunicorn workers at once.
  * CREATE TABLE IF NOT EXISTS / add_column() bring an older database up to date.
  * One-time data fixes are gated on PRAGMA user_version (SCHEMA_VERSION below):
    bump it and add an `if schema_version < N:` block when a release needs one.
  * Sample content is seeded only on a brand-new database, so deleting it stays deleted.
"""
import fcntl
from core import *

SCHEMA_VERSION = 1

def add_column(c, table, col, defn):
    """ALTER TABLE ... ADD COLUMN only when the column is missing."""
    if col not in {row[1] for row in c.execute(f'PRAGMA table_info("{table}")')}:
        c.execute(f'ALTER TABLE "{table}" ADD COLUMN {col} {defn}')

def _init_db():
    conn = get_db(); c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'editor',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    existing = c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    fresh_install = existing == 0   # sample content is only ever seeded on a brand-new database
    schema_version = c.execute("PRAGMA user_version").fetchone()[0]
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
        ('get_involved_icon','bi-people-fill'),
        ('get_involved_layout','stack'),
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
    for slot in range(2, GET_INVOLVED_SLOTS + 1):
        for field, default in (('enabled', '0'), ('title', ''), ('body', ''), ('link', ''),
                               ('link_label', 'Sign Up'), ('icon', 'bi-people-fill')):
            c.execute(
                "INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)",
                (get_involved_key(slot, field), default)
            )

    c.execute('''CREATE TABLE IF NOT EXISTS leaders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        role TEXT NOT NULL, bio TEXT DEFAULT '', photo TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
    )''')
    if fresh_install:
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
    add_column(c, 'beyond_walls', 'youtube_video_id', "TEXT DEFAULT ''")
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
    if fresh_install:
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
    if fresh_install:
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
        add_column(c, 'hub_cards', col, defn)
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
    for card in (seeded_hub_cards if fresh_install else []):
        c.execute(
            "INSERT OR IGNORE INTO hub_cards (slug,title,subtitle,photo,icon,card_type,parent_id,target_url,media_url,modal_title,modal_body,modal_button_label,modal_button_url,modal_image,open_in_new_tab,sort_order,is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            card
        )
    # One-time clean-up of the built-in cards (these used to be re-applied on every boot,
    # which silently undid any edit an admin made to them).
    if schema_version < 1:
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
    add_column(c, 'ministries', 'photo', "TEXT DEFAULT ''")
    if fresh_install:
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
    add_column(c, 'serve_categories', 'photo', "TEXT DEFAULT ''")
    add_column(c, 'serve_categories', 'description', "TEXT DEFAULT ''")
    c.execute('''CREATE TABLE IF NOT EXISTS serve_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL,
        label TEXT NOT NULL, sort_order INTEGER DEFAULT 0,
        FOREIGN KEY(category_id) REFERENCES serve_categories(id) ON DELETE CASCADE
    )''')
    if fresh_install:
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
        add_column(c, 'events', col, defn)

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

    # Shared by every gunicorn worker and survives restarts, unlike an in-memory dict.
    c.execute('''CREATE TABLE IF NOT EXISTS rate_events (
        bucket TEXT NOT NULL, ip TEXT NOT NULL, ts REAL NOT NULL
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rate_events ON rate_events(bucket, ip, ts)')

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
        add_column(c, 'sermon_notes', col, "TEXT DEFAULT ''")

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

    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit(); conn.close()

def init_db():
    # Both gunicorn workers import the app together; serialise them so the schema
    # work (and the first-run admin password) happens exactly once.
    with open(DB + '.initlock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _init_db()


__all__ = ['init_db', 'add_column', 'SCHEMA_VERSION']
