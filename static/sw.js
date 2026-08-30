const STATIC_CACHE = 'oasis-hub-static-v10';
const RUNTIME_CACHE = 'oasis-hub-runtime-v10';
const OFFLINE_URL = '/hub';

const PRECACHE = [
  '/',
  '/gate',
  '/hub',
  '/calendar',
  '/connect',
  '/about',
  '/social',
  '/manifest.json',
  '/static/reveal.js?v=3',
  '/static/transitions.css?v=1',
  '/static/apple-touch-icon.png',
  '/static/icon-192x192.png',
  '/static/icon-512x512.png',
  '/static/logo.png',
  '/static/oasis_icon.png',
  '/static/gate.jpg',
  '/static/splash.jpg',
  '/static/sermon.png'
];

self.addEventListener('install', event => {
  // Precache requests carry X-SW-Precache so the server's analytics tracker
  // (member.py track()) can tell them apart from real page visits.
  // One unreachable URL must not abort the whole install, or the worker never
  // updates and the device stays pinned to the previously cached assets.
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache =>
      Promise.all(PRECACHE.map(url =>
        fetch(new Request(url, { headers: { 'X-SW-Precache': '1' } }))
          .then(response => (response.ok ? cache.put(url, response) : null))
          .catch(() => null)
      ))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => ![STATIC_CACHE, RUNTIME_CACHE].includes(key))
          .map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== self.location.origin) {
    return;
  }

  // Never cache admin pages or the (large, ever-growing) uploads gallery.
  // Oasis Next Steps downloads are file attachments, not pages — caching one
  // would hand a stale sheet back on the next tap and waste the runtime cache.
  if (requestUrl.pathname.startsWith('/admin')
      || requestUrl.pathname.startsWith('/static/uploads/')
      || requestUrl.pathname.indexOf('/next-steps') !== -1) {
    return;
  }

  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const copy = response.clone();
          caches.open(RUNTIME_CACHE).then(cache => cache.put(event.request, copy));
          return response;
        })
        .catch(async () => {
          const cachedPage = await caches.match(event.request);
          if (cachedPage) return cachedPage;
          const offline = await caches.match(OFFLINE_URL);
          if (offline) return offline;
          // Resolving with undefined here aborts the navigation and leaves a
          // blank window, so always hand back a real page.
          return new Response(
            '<!doctype html><meta charset="utf-8">'
            + '<meta name="viewport" content="width=device-width,initial-scale=1">'
            + '<title>Oasis Hub</title>'
            + '<body style="font-family:-apple-system,sans-serif;background:#F6EFE4;'
            + 'color:#1d1d1f;display:flex;align-items:center;justify-content:center;'
            + 'height:100vh;margin:0;text-align:center;padding:24px">'
            + '<div><p>You are offline.</p>'
            + '<p><a href="/hub" style="color:#13677A">Try again</a></p></div>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        })
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) {
        return cached;
      }

      return fetch(event.request).then(response => {
        if (response && response.status === 200) {
          const copy = response.clone();
          caches.open(RUNTIME_CACHE).then(cache => cache.put(event.request, copy));
        }
        return response;
      });
    })
  );
});
