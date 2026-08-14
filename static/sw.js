const STATIC_CACHE = 'oasis-hub-static-v8';
const RUNTIME_CACHE = 'oasis-hub-runtime-v8';
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
  '/static/reveal.js?v=2',
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
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache =>
      Promise.all(PRECACHE.map(url =>
        fetch(new Request(url, { headers: { 'X-SW-Precache': '1' } }))
          .then(response => {
            if (!response.ok) throw new Error('Precache failed for ' + url);
            return cache.put(url, response);
          })
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
      || requestUrl.pathname.endsWith('/next-steps')) {
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
          return cachedPage || caches.match(OFFLINE_URL);
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
