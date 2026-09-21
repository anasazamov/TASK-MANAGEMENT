/* Inside Telegram: sign in with the signed init data, then behave like the app. */
(() => {
  'use strict';
  const app = window.Telegram?.WebApp;
  if (!app?.initData) return;
  const root = document.documentElement;
  root.dataset.telegram = 'true';
  app.ready();
  app.expand();
  try { app.setHeaderColor('secondary_bg_color'); } catch (_) {}

  // Follow the theme of the client the app is embedded in, not the saved website one.
  const applyTheme = () => { root.dataset.theme = app.colorScheme === 'light' ? 'light' : 'dark'; };
  applyTheme();
  app.onEvent?.('themeChanged', applyTheme);

  // Telegram draws the back button; the site's own pages stay uncluttered.
  const back = app.BackButton;
  if (back) {
    const home = new URL(document.getElementById('telegram-config')?.dataset.homeUrl || '/', location.origin);
    if (location.pathname === home.pathname && !location.search) back.hide();
    else { back.show(); back.onClick(() => history.length > 1 ? history.back() : location.assign(home.pathname)); }
  }

  const config = document.getElementById('telegram-config');
  if (!config || config.dataset.authenticated === 'true') return;

  async function signIn() {
    const response = await fetch(config.dataset.loginUrl, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: app.initData})});
    const data = await response.json().catch(() => ({}));
    if (response.ok) { location.replace(config.dataset.homeUrl); return; }
    const note = document.querySelector('[data-telegram-note]');
    if (note) note.textContent = data.message || 'Telegram orqali kirib bo‘lmadi.';
    if (data.error === 'not_linked' && data.bot) {
      const link = document.querySelector('[data-telegram-bot]');
      if (link) { link.href = `https://t.me/${data.bot}`; link.hidden = false; }
    }
  }
  signIn().catch(() => {
    const note = document.querySelector('[data-telegram-note]');
    if (note) note.textContent = 'Server bilan aloqa yo‘q. Internetni tekshirib qayta oching.';
  });
})();
