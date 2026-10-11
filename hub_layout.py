"""Data for the new Hub layout: service countdown settings, the notes preview, upcoming events,
quick buttons and the Explore grid. The template is templates/hub_v2.html; the classic layout
(templates/hub.html) stays available under Admin -> Hub Layout."""
from core import *
from documents import row_value

DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
_TIME_RE = re.compile(r'^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$', re.I)

# Shown as the row of quick buttons. Each one is tied to a Hub card by slug, so an admin who
# edits or hides that card in Hub Cards changes the button too; the card then isn't repeated
# in the Explore grid. (slug, short label, icon, fallback link)
QUICK_BUTTONS = [
    ('prayer-request', 'Pray', 'bi-hand-index-thumb', '/prayer'),
    (None, 'Connect', 'bi-person-plus-fill', '/connect'),
    ('special-events', 'Life Events', 'bi-calendar-heart', '/special-events'),
    ('help-desk', 'Help Desk', 'bi-headset', 'https://www.oasisnj.net/helpdesk'),
]
# Cards the new layout already covers elsewhere (quick buttons, Coming Up + the Events tab).
COVERED_SLUGS = {'prayer-request', 'special-events', 'help-desk', 'upcoming-events'}


def parse_service_time(text):
    """'8:30', '08:30', '8:30 AM', '11:30am', '10am' -> 'HH:MM' (24h), or None."""
    m = _TIME_RE.match(text or '')
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or '').lower().replace('.', '')
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == 'pm' else 0)
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def service_times(settings):
    times = [parse_service_time(t) for t in re.split(r'[,;\n]+', settings.get('service_times') or '')]
    return sorted({t for t in times if t})


def display_time(hhmm):
    h, m = map(int, hhmm.split(':'))
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def service_info(settings):
    try:
        day = int(settings.get('service_day') or 0) % 7
    except ValueError:
        day = 0
    try:
        lead = max(0, min(60, int(settings.get('service_starting_minutes') or 2)))
    except ValueError:
        lead = 2
    times = service_times(settings)
    return {'day': day, 'day_name': DAY_NAMES[day], 'times': times, 'labels': [display_time(t) for t in times],
            'lead': lead, 'title': (settings.get('service_title') or '').strip() or 'Worship with us'}


def _plain(fragment):
    # Block-level tags separate words; inline ones (<b>, <em>…) must not, or "an <b>anchor</b>."
    # would read "an anchor ." on the card.
    text = re.sub(r'<(?:br|/?p|/?li|/?h\d|/?div|/?ol|/?ul)\b[^>]*>', ' ', fragment or '', flags=re.I)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    return re.sub(r'\s+', ' ', text).strip()


def note_preview(note, limit=2, width=110):
    """The first points of a sermon note for the Hub card: list items if there are any,
    otherwise the first paragraphs. Plain text, trimmed."""
    if not note:
        return []
    body = row_value(note, 'body_html')
    parts = re.findall(r'<li[^>]*>(.*?)</li>', body, re.S | re.I) or re.findall(r'<p[^>]*>(.*?)</p>', body, re.S | re.I)
    points = []
    for part in parts:
        text = _plain(part)
        if len(text) < 4:
            continue
        points.append(text if len(text) <= width else text[:width].rsplit(' ', 1)[0] + '…')
        if len(points) == limit:
            break
    return points


def short_date(iso):
    """'2026-10-11' -> 'OCT 11' (left as-is if it isn't a date)."""
    try:
        d = date.fromisoformat(str(iso))
    except ValueError:
        return str(iso or '')
    return f"{d.strftime('%b').upper()} {d.day}"


def upcoming_events(events, limit=6):
    out = []
    for e in events[:limit]:
        try:
            d = date.fromisoformat(str(e['event_date']))
        except ValueError:
            continue
        out.append({'title': e['title'], 'month': d.strftime('%b').upper(), 'day': d.day,
                    'weekday': d.strftime('%a'), 'time': (e.get('event_time') or '').strip()})
    return out


def quick_buttons(cards):
    by_slug = {c['slug']: c for c in cards}
    buttons = []
    for slug, label, icon, fallback in QUICK_BUTTONS:
        if slug is None:
            buttons.append({'label': label, 'icon': icon, 'card': None, 'href': fallback, 'new_tab': False})
        elif slug in by_slug:                       # only while the card is active
            card = by_slug[slug]
            buttons.append({'label': label, 'icon': icon, 'card': card,
                            'href': card['target_url'] or fallback,
                            'new_tab': bool(card['open_in_new_tab'])})
    return buttons


def explore_cards(cards):
    return [c for c in cards if c['slug'] not in COVERED_SLUGS]


__all__ = [n for n in dir() if not n.startswith('__')]
