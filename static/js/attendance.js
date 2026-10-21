/* Daily attendance without page reloads.

   A day chip or the date field swaps only the day block (the server renders it
   with ?partial=1), keeps the address bar in step so Back works, and, while
   today is shown, refreshes the block so a new arrival appears by itself. The
   links stay ordinary links: without JavaScript the page simply reloads. */
(() => {
  'use strict';
  const root = document.querySelector('[data-att-root]');
  if (!root) return;
  const base = root.dataset.url;
  const field = document.querySelector('[data-att-date]');
  const INTERVAL = 30000;
  let pending = null;
  let loading = false;

  const current = () => root.querySelector('[data-att-block]');

  async function show(date, {push = true, quiet = false} = {}) {
    pending?.abort();
    pending = new AbortController();
    loading = true;
    if (!quiet) root.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(`${base}?date=${encodeURIComponent(date)}&partial=1`, {
        credentials: 'same-origin', headers: {'X-Requested-With': 'fetch'}, signal: pending.signal});
      // A signed-out session is redirected to the login page: load it properly.
      if (!response.ok || response.redirected) throw new Error(String(response.status));
      root.innerHTML = await response.text();
      if (field) field.value = current()?.dataset.date || date;
      if (push) history.pushState({date}, '', `${base}?date=${date}`);
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (!quiet) window.location.href = `${base}?date=${encodeURIComponent(date)}`;
    } finally {
      loading = false;
      root.removeAttribute('aria-busy');
    }
  }

  root.addEventListener('click', event => {
    const link = event.target.closest('[data-att-day]');
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    show(link.dataset.attDay);
  });

  field?.addEventListener('change', () => { if (field.value) show(field.value); });
  field?.form.addEventListener('submit', event => {
    event.preventDefault();
    if (field.value) show(field.value);
  });

  window.addEventListener('popstate', () => {
    const date = new URLSearchParams(window.location.search).get('date') || field?.defaultValue;
    if (date) show(date, {push: false});
  });

  // Today's list fills up through the morning: refresh it quietly while visible,
  // but never over a day being loaded, nor while an excuse form is open or typed in.
  const busyHere = () => root.querySelector('details[open]') || document.activeElement?.closest('[data-att-root] form');
  setInterval(() => {
    const block = current();
    if (loading || busyHere() || document.hidden || !block || block.dataset.today !== '1') return;
    show(block.dataset.date, {push: false, quiet: true});
  }, INTERVAL);
})();
