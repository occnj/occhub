"""YouTube lookups for the Watch page (feed, live check) with a short cache."""
from core import *

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

_SERMON_CACHE = {}          # channel source -> (fetched_at, videos)
SERMON_CACHE_SECONDS = 300  # 5 minutes

def get_sermon_videos(settings=None):
    """Sermon list for the Watch page. The YouTube lookups are slow (up to 6s each) and
    block a gunicorn worker, so the result is kept for a few minutes per worker. If
    YouTube is down the last good list is served instead of nothing."""
    settings = settings or all_settings()
    key = ((settings.get('sermon_channel_url') or '').strip()
           or (settings.get('social_youtube_url') or '').strip())
    manual = tuple((settings.get(f'sermon_video_{i}_title'), settings.get(f'sermon_video_{i}_url')) for i in range(1, 11))
    cache_key = (key, manual)
    hit = _SERMON_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < SERMON_CACHE_SECONDS:
        return hit[1]
    videos = _load_sermon_videos(settings)
    # Only cache a live result; if the YouTube calls failed, prefer an older good list.
    if videos and videos[0].get('source') != 'manual':
        _SERMON_CACHE[cache_key] = (time.time(), videos)
    elif hit:
        _SERMON_CACHE[cache_key] = (time.time() - SERMON_CACHE_SECONDS + 60, hit[1])  # retry in a minute
        return hit[1]
    else:
        _SERMON_CACHE[cache_key] = (time.time(), videos)
    return videos

def _load_sermon_videos(settings):
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



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]
