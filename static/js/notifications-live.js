/* Keeps the bell current while the page stays open.

   Notifications are written by other people's actions, so without this the
   count only changed when the person happened to reload. The poll is small
   (two counts and anything newer than the last row seen) and stops while the
   tab is in the background. */
(() => {
  'use strict';
  const bell = document.querySelector('[data-live-url]');
  if (!bell) return;
  const url = bell.dataset.liveUrl;
  const INTERVAL = 25000;
  const list = document.querySelector('[data-notification-list]');
  let last = Number(list?.querySelector('[data-id]')?.dataset.id || 0);
  let timer;

  function badge(count) {
    let pill = bell.querySelector('.notification-count');
    if (!count) { pill?.remove(); return; }
    if (!pill) {
      pill = document.createElement('span');
      pill.className = 'notification-count';
      bell.appendChild(pill);
    }
    pill.textContent = count > 9 ? '9+' : String(count);
  }

  function row(item) {
    const link = document.createElement('a');
    link.href = item.url;
    link.className = 'notification-row' + (item.unread ? ' unread' : '');
    link.dataset.drawer = '';
    link.dataset.id = item.id;
    const icon = list.querySelector('.notification-row .decision-icon');
    if (icon) link.appendChild(icon.cloneNode(true));
    const body = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = item.title;
    const task = document.createElement('p');
    task.className = 'muted';
    task.textContent = item.task;
    const when = document.createElement('small');
    when.className = 'muted';
    when.textContent = item.created;
    body.append(title, task, when);
    link.appendChild(body);
    if (item.unread) {
      const dot = document.createElement('span');
      dot.className = 'online-dot';
      link.appendChild(dot);
    }
    return link;
  }

  async function poll() {
    if (document.hidden) return;
    let data;
    try {
      const response = await fetch(`${url}?after=${last}`, {credentials: 'same-origin',
        headers: {'X-Requested-With': 'fetch'}});
      if (!response.ok) return;  // A signed-out session redirects to the login page.
      data = await response.json();
    } catch (_) { return; }
    badge(data.unread);
    const active = document.querySelector('.nav-count');
    if (active) active.textContent = data.active;
    if (!list || !data.items.length) return;
    // Newest first from the server; inserting each one at the top keeps that order.
    data.items.slice().reverse().forEach(item => {
      if (list.querySelector(`[data-id="${item.id}"]`)) return;
      list.querySelector('.empty-state')?.remove();
      list.prepend(row(item));
      last = Math.max(last, item.id);
    });
  }

  const start = () => { clearInterval(timer); timer = setInterval(poll, INTERVAL); };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { poll(); start(); } });
  window.addEventListener('focus', poll);
  start();
  poll();
})();
