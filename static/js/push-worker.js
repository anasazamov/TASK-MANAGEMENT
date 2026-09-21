/* Runs while the site is closed: shows the notification and opens the task. */
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = data.title || 'Topshiriq nazorati';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/static/img/favicon.svg',
    badge: '/static/img/favicon.svg',
    tag: data.url || 'task',
    data: {url: data.url || '/notifications/'},
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data?.url || '/notifications/';
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(windows => {
    for (const client of windows) {
      if (client.url.includes(url) && 'focus' in client) return client.focus();
    }
    return self.clients.openWindow(url);
  }));
});
