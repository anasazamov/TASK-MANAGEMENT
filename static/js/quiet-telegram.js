/* Telegram's own SDK narrates every message it exchanges with the client
   ("[Telegram.WebView] > postEvent"), unconditionally — there is no debug flag
   to turn it off, and inside the Mini App it fills the console. Only that
   traffic log is dropped here; the SDK's [Telegram.WebApp] warnings, which
   report real problems, are left alone.

   localStorage['sic-telegram-log'] = '1' brings the traffic back. */
(() => {
  'use strict';
  try { if (localStorage.getItem('sic-telegram-log') === '1') return; } catch (_) {}
  const log = console.log.bind(console);
  console.log = (...args) => {
    if (typeof args[0] === 'string' && args[0].startsWith('[Telegram.WebView]')) return;
    log(...args);
  };
})();
