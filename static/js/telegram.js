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
  if (!config) return;
  // Inside Telegram the account follows the Telegram user, so a session left by
  // whoever this Telegram account belonged to before must be replaced, not reused.
  const opener = app.initDataUnsafe?.user?.id;
  const signedIn = config.dataset.authenticated === 'true';
  const marked = config.dataset.sessionTelegram;
  if (signedIn && opener && String(opener) === marked) return;
  if (signedIn && !marked) {
    // A session from before this check, or a password sign-in: verify it once,
    // then leave it alone so a refused sign-in cannot loop.
    const flag = 'sic-telegram-checked';
    try { if (sessionStorage.getItem(flag)) return; sessionStorage.setItem(flag, '1'); } catch (_) { return; }
  }

  async function signIn() {
    const response = await fetch(config.dataset.loginUrl, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: app.initData})});
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      if (data.switched) app.showAlert?.(`${data.name} sifatida kirildi.`);
      location.replace(config.dataset.homeUrl);
      return;
    }
    const note = document.querySelector('[data-telegram-note]');
    if (note) note.textContent = data.message || 'Telegram orqali kirib bo‘lmadi.';
    if (data.error === 'not_linked' && data.bot) {
      const link = document.querySelector('[data-telegram-bot]');
      if (link) {
        // ?start= makes the chat open with a START button, so one tap reaches us.
        const url = `https://t.me/${String(data.bot).replace(/^@/, '')}?start=login`;
        link.href = url;
        link.hidden = false;
        // A Mini App cannot open a t.me address in a tab of its own; the client
        // has to be asked to switch to the chat, or the button does nothing.
        link.addEventListener('click', (event) => {
          if (!app.openTelegramLink) return;
          event.preventDefault();
          app.openTelegramLink(url);
        });
      }
    }
  }
  signIn().catch(() => {
    const note = document.querySelector('[data-telegram-note]');
    if (note) note.textContent = 'Server bilan aloqa yo‘q. Internetni tekshirib qayta oching.';
  });
})();
