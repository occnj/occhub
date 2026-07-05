const STATIC_CACHE = 'oasis-hub-static-v3';
const RUNTIME_CACHE = 'oasis-hub-runtime-v3';
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
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
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

  // Never cache admin pages or the (large, ever-growing) uploads gallery
  if (requestUrl.pathname.startsWith('/admin') || requestUrl.pathname.startsWith('/static/uploads/')) {
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
