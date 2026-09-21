/* Subscribes this browser to task notifications; the site may then be closed. */
(() => {
  'use strict';
  const config = document.getElementById('push-config');
  if (!config) return;
  const box = document.querySelector('[data-push]');
  const button = box?.querySelector('[data-push-toggle]');
  const status = box?.querySelector('[data-push-status]');
  const csrf = config.querySelector('[name=csrfmiddlewaretoken]').value;
  const key = config.dataset.pushKey;
  const askedKey = `sic-push-asked:${config.dataset.pushUser}`;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function show(message, busy = false) {
    if (!box) return;
    status.textContent = message;
    button.disabled = busy || !supported || !key;
  }
  function bytes(value) {
    const padded = (value + '='.repeat((4 - value.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(padded), char => char.charCodeAt(0));
  }
  // Every step waits on the browser, which may simply never answer: an unanswered
  // prompt or a worker that cannot start would otherwise wait for ever.
  function limit(promise, ms, message) {
    return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(message)), ms))]);
  }
  async function api(url, body) {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error('Obunani saqlab bo‘lmadi. Sahifani yangilab qayta urinib ko‘ring.');
  }
  const remember = () => { try { localStorage.setItem(askedKey, '1'); } catch (_) {} };
  const asked = () => { try { return localStorage.getItem(askedKey) === '1'; } catch (_) { return true; } };

  async function current() {
    const registration = await navigator.serviceWorker.getRegistration('/');
    return registration ? await registration.pushManager.getSubscription() : null;
  }
  function render(subscription) {
    if (!box) return;
    const on = Boolean(subscription);
    const state = {granted: 'ruxsat berilgan', denied: 'bloklangan', default: 'so‘ralmagan'}[Notification.permission];
    box.querySelector('[data-push-state]').textContent =
      `Holat: ${on ? 'obuna bor' : 'obuna yo‘q'} · brauzer ruxsati: ${state} · manzil: ${location.origin}`;
    button.textContent = on ? 'Bildirishnomani o‘chirish' : 'Bildirishnomani yoqish';
    button.classList.toggle('primary', !on);
    button.classList.toggle('secondary', on);
    show(on ? 'Yoqilgan. Sayt yopiq bo‘lsa ham, brauzer ochiq turganda xabar keladi.'
       : Notification.permission === 'denied' ? 'Brauzer bu sayt uchun bildirishnomani bloklagan (so‘rov bir necha marta yopilgan). Manzil satrining chap tomonidagi belgini bosing → «Sayt sozlamalari» → «Bildirishnomalar» → «Ruxsat berish», so‘ng sahifani yangilang.'
       : 'O‘chirilgan. Yangi topshiriq va muddat haqida xabar olish uchun yoqing.');
  }

  async function enable() {
    const permission = await limit(Notification.requestPermission(), 60000,
      'Brauzer so‘rovi javobsiz qoldi. Qayta bosib, «Ruxsat berish»ni tanlang.');
    remember();
    if (permission !== 'granted') return null;
    const registration = await limit(navigator.serviceWorker.register(config.dataset.workerUrl, {scope: '/'}), 20000,
      'Bildirishnoma xizmati ishga tushmadi. Sayt haqiqiy HTTPS sertifikati bilan ochilishi kerak.');
    await limit(navigator.serviceWorker.ready, 20000,
      'Bildirishnoma xizmati faollashmadi. Sahifani yangilab qayta urinib ko‘ring.');
    const subscription = await limit(registration.pushManager.subscribe(
      {userVisibleOnly: true, applicationServerKey: bytes(key)}), 20000,
      'Brauzer obunani ochmadi. Internet aloqasini tekshiring.');
    await api(config.dataset.subscribeUrl, subscription.toJSON());
    return subscription;
  }

  if (!supported || !key) {
    show(!key ? 'Bildirishnomalar serverda sozlanmagan. Administrator VAPID kalitlarini qo‘shsin.'
              : 'Bu brauzer bildirishnomani qo‘llamaydi. HTTPS va zamonaviy brauzer kerak.');
    return;
  }

  button?.addEventListener('click', async () => {
    show('Kutib turing…', true);
    try {
      const existing = await current();
      if (existing) {
        await api(config.dataset.unsubscribeUrl, {endpoint: existing.endpoint});
        await existing.unsubscribe();
        render(null);
        return;
      }
      render(await enable());
    } catch (error) {
      const certificate = error.name === 'SecurityError' || /SSL|certificate|secure context/i.test(error.message || '');
      show(certificate ? 'Sayt sertifikati brauzerga ishonchli emas, shuning uchun bildirishnoma xizmati ishlamaydi. Haqiqiy HTTPS sertifikati kerak.'
        : error.message || 'Bildirishnomani yoqib bo‘lmadi.');
      button.disabled = false;
    }
  });

  (async () => {
    const subscription = await current().catch(() => null);
    render(subscription);
    // Ask once per browser after signing in. Chrome blocks a site whose prompt is
    // ignored repeatedly, so a dismissed prompt is never raised again by itself:
    // the notifications page keeps the button.
    if (subscription || asked() || Notification.permission !== 'default') return;
    remember();
    try { render(await enable()); } catch (_) { render(null); }
  })();
})();
