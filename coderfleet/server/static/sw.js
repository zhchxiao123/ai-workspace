const CACHE = 'coderfleet-v1';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.add('/')));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('push', e => {
  let payload = { title: 'CoderFleet', body: '', url: '/m' };
  try { payload = { ...payload, ...e.data.json() }; } catch {}
  e.waitUntil(
    self.registration.showNotification(payload.title, {
      body:    payload.body,
      icon:    '/static/icons/icon.svg',
      badge:   '/static/icons/icon.svg',
      tag:     'coderfleet-task',
      renotify: true,
      data:    { url: payload.url },
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/m';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      const existing = list.find(c => c.url.includes(self.location.origin));
      if (existing) return existing.focus();
      return clients.openWindow(url);
    })
  );
});

self.addEventListener('fetch', e => {
  // Only handle http/https — chrome-extension:// and others are unsupported by Cache API
  if (!e.request.url.startsWith('http')) return;
  // Never intercept API / SSE / WebSocket traffic
  if (e.request.url.includes('/api/')) return;
  if (e.request.method !== 'GET') return;

  // Network-first: try live, fall back to cache
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
