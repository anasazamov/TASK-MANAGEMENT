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

  const note = document.querySelector('[data-telegram-note]');
  const say = text => { if (note) note.textContent = text; };
  const supports = version => Boolean(app.isVersionAtLeast?.(version));

  // Every one of these leaves the Mini App, and each is refused by some client,
  // silently: the SDK only logs that the method is not supported. So the last
  // step is a plain navigation, which every client follows.
  function openBot(url) {
    if (app.openTelegramLink && supports('6.1')) { app.openTelegramLink(url); return; }
    location.href = url;
  }

  async function attempt() {
    const response = await fetch(config.dataset.loginUrl, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: app.initData})});
    return {ok: response.ok, data: await response.json().catch(() => ({}))};
  }

  function done(data) {
    if (data.switched) app.showAlert?.(`${data.name} sifatida kirildi.`);
    location.replace(config.dataset.homeUrl);
  }

  // Newer clients can hand over the phone number without leaving the app: the
  // number goes to the bot as a contact message, which links the account, and
  // the sign-in is retried while that update travels.
  async function shareNumber(button) {
    button.disabled = true;
    const granted = await new Promise(resolve => {
      try { app.requestContact(ok => resolve(Boolean(ok))); } catch (_) { resolve(false); }
    });
    if (!granted) {
      button.disabled = false;
      say('Raqam ulashilmadi. Qayta urinib ko‘ring yoki botni oching.');
      return;
    }
    say('Raqam yuborildi, hisob tekshirilmoqda…');
    for (let tries = 0; tries < 5; tries++) {
      await new Promise(resolve => setTimeout(resolve, 1200));
      const {ok, data} = await attempt().catch(() => ({ok: false, data: {}}));
      if (ok) { done(data); return; }
    }
    button.disabled = false;
    say('Raqam hali bog‘lanmadi. Ro‘yxatda shu raqam borligini rahbaringizdan so‘rang.');
  }

  async function signIn() {
    const {ok, data} = await attempt();
    if (ok) { done(data); return; }
    say(data.message || 'Telegram orqali kirib bo‘lmadi.');
    if (data.error !== 'not_linked' || !data.bot) return;
    const share = document.querySelector('[data-telegram-contact]');
    if (share && app.requestContact && supports('6.9')) {
      share.hidden = false;
      share.addEventListener('click', () => shareNumber(share));
    }
    const link = document.querySelector('[data-telegram-bot]');
    if (!link) return;
    // ?start= makes the chat open with a START button, so one tap reaches us.
    const url = `https://t.me/${String(data.bot).replace(/^@/, '')}?start=login`;
    link.href = url;
    link.hidden = false;
    // Opening a tab of its own is what a Mini App webview refuses to do.
    link.removeAttribute('target');
    link.addEventListener('click', event => { event.preventDefault(); openBot(url); });
  }
  signIn().catch(() => say('Server bilan aloqa yo‘q. Internetni tekshirib qayta oching.'));
})();
