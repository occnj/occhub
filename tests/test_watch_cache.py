import time

import youtube


def _video(title='t'):
    return [{'slot': 1, 'title': title, 'url': 'u', 'video_id': 'abcdefghijk', 'thumbnail': '', 'published': '',
             'source': 'youtube', 'is_live': False, 'is_active': True}]


def test_watch_page_hits_youtube_once_per_window(client, monkeypatch):
    calls = []
    monkeypatch.setattr(youtube, 'fetch_youtube_feed_videos', lambda src, limit=10: calls.append(1) or _video())
    monkeypatch.setattr(youtube, 'fetch_youtube_live_video', lambda src: None)
    youtube._SERMON_CACHE.clear()
    for _ in range(5):
        assert client.get('/watch-sermon').status_code == 200
    assert len(calls) == 1


def test_last_good_list_is_served_when_youtube_fails(monkeypatch):
    monkeypatch.setattr(youtube, 'fetch_youtube_feed_videos', lambda src, limit=10: _video('good'))
    monkeypatch.setattr(youtube, 'fetch_youtube_live_video', lambda src: None)
    youtube._SERMON_CACHE.clear()
    assert youtube.get_sermon_videos()[0]['title'] == 'good'
    for key, (_, vids) in list(youtube._SERMON_CACHE.items()):
        youtube._SERMON_CACHE[key] = (time.time() - 10_000, vids)           # expire it
    monkeypatch.setattr(youtube, 'fetch_youtube_feed_videos', lambda src, limit=10: [])
    assert youtube.get_sermon_videos()[0]['title'] == 'good'
