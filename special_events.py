"""Special Events / Life Events (wedding, funeral, baptism, dedication, counseling, membership): settings and helpers.

Kept free of routes so schema.py can import the defaults; the pages live in routes_special_events.py.
Everything a visitor sees, and where requests are emailed, is editable under Admin → Special Events.
"""
from core import *

SPECIAL_EVENT_SLOTS = 8            # the 6 life events on the Church Center form, plus 2 spare
DEFAULT_SPECIAL_EVENTS_EMAIL = 'phegel@oasisnj.net'
EMAIL_RE = re.compile(r'[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+')
PHONE_TYPES = ('Mobile', 'Home', 'Work')

# Same choices, in the same order, as the "Life Events" form on Church Center (forms/373934).
_DEFAULT_SERVICES = [
    ('Wedding', 'bi-gem', 'Planning your big day? Talk with us about your ceremony at Oasis.'),
    ('Funeral', 'bi-flower1', 'We are so sorry for your loss. Let us help you honor your loved one.'),
    ('Water Baptism', 'bi-droplet-fill', 'Ready to take your next step of faith? Find out about the next baptism.'),
    ('Baby Dedication', 'bi-balloon-heart', 'Dedicate your little one to the Lord with your church family.'),
    ('Pastoral Counseling', 'bi-chat-heart', 'Meet with a pastor for prayer, guidance and biblical counsel.'),
    ('Church Membership', 'bi-house-heart', 'Make Oasis your home. Learn how to become a member.'),
]


def se_key(slot, field):
    return f'special_events_{slot}_{field}'


def setting_defaults():
    """(key, value) pairs seeded with INSERT OR IGNORE, so admin edits are never overwritten."""
    pairs = [
        ('special_events_title', 'Special Events'),
        ('special_events_description', "Celebrate life's milestones and find support in its hardest moments."),
        ('special_events_intro', 'There are many different events in life. Whether it is the joy of marriage or the '
                                 'pain of death, we at Oasis want you to know that we are here for you.'),
        ('special_events_email', DEFAULT_SPECIAL_EVENTS_EMAIL),
        ('special_events_mode', 'form'),          # 'form' = Hub form + email, 'link' = send people to Church Center
        ('special_events_form_url', 'https://oasisnj.churchcenter.com/people/forms/373934'),
        ('special_events_info_url', 'https://www.oasisnj.net/specialevents'),
    ]
    for slot in range(1, SPECIAL_EVENT_SLOTS + 1):
        name, icon, body = _DEFAULT_SERVICES[slot - 1] if slot <= len(_DEFAULT_SERVICES) else ('', 'bi-calendar-heart', '')
        pairs += [(se_key(slot, 'enabled'), '1' if name else '0'), (se_key(slot, 'name'), name),
                  (se_key(slot, 'icon'), icon), (se_key(slot, 'description'), body)]
    return pairs


def parse_recipients(raw):
    """'a@x.org, b@y.org' -> ['a@x.org', 'b@y.org'] (anything that isn't an address is dropped)."""
    return [p for p in re.split(r'[\s,;]+', raw or '') if EMAIL_RE.fullmatch(p)]


def get_special_events(settings=None):
    settings = settings or all_settings()
    val = lambda key, default='': (settings.get(key) or '').strip() or default
    services = []
    for slot in range(1, SPECIAL_EVENT_SLOTS + 1):
        name = val(se_key(slot, 'name'))
        if val(se_key(slot, 'enabled'), '0') == '1' and name:
            services.append({'slot': slot, 'name': name, 'icon': val(se_key(slot, 'icon'), 'bi-calendar-heart'),
                             'description': val(se_key(slot, 'description'))})
    mode = val('special_events_mode', 'form')
    return {
        'title': val('special_events_title', 'Special Events'),
        'description': val('special_events_description'),
        'intro': val('special_events_intro'),
        'mode': mode if mode in ('form', 'link') else 'form',
        'form_url': val('special_events_form_url'),
        'info_url': val('special_events_info_url'),
        'recipients': parse_recipients(settings.get('special_events_email')) or [DEFAULT_SPECIAL_EVENTS_EMAIL],
        'services': services,
    }


__all__ = [n for n in dir() if not n.startswith('__')]
