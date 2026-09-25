(() => {
  'use strict';
  let bindings;
  function mount() {
  bindings?.abort();
  bindings = new AbortController();
  const on = (element, name, callback) => element?.addEventListener(name, callback, {signal: bindings.signal});
  const root = document.documentElement;
  const themeButton = document.querySelector('[data-theme-toggle]');
  const updateThemeLabel = () => themeButton?.setAttribute('aria-label', root.dataset.theme === 'dark' ? 'Yorug‘ rejimga o‘tish' : 'Tungi rejimga o‘tish');
  updateThemeLabel();
  on(themeButton, 'click', () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('sic-theme', root.dataset.theme); } catch (_) {}
    updateThemeLabel();
  });
  const menu = document.querySelector('[data-menu]');
  const sidebar = document.getElementById('sidebar');
  const narrowScreen = window.matchMedia('(max-width: 700px)');
  const syncSidebar = () => { if (sidebar) sidebar.inert = narrowScreen.matches && !document.body.classList.contains('menu-open'); };
  const closeMenu = () => { document.body.classList.remove('menu-open'); menu?.setAttribute('aria-expanded', 'false'); syncSidebar(); };
  on(menu, 'click', () => { const open = document.body.classList.toggle('menu-open'); menu.setAttribute('aria-expanded', String(open)); syncSidebar(); });
  on(narrowScreen, 'change', syncSidebar);
  syncSidebar();
  on(document.querySelector('[data-menu-close]'), 'click', closeMenu);
  document.querySelectorAll('[data-dismiss]').forEach(button => on(button, 'click', () => button.closest('.message').remove()));
  document.querySelectorAll('form[data-confirm]').forEach(form => on(form, 'submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));
  const drawer = document.getElementById('task-drawer');
  const content = document.getElementById('drawer-content');
  let lastTrigger;
  let currentLoad;
  bindings.signal.addEventListener('abort', () => currentLoad?.abort());
  on(document, 'click', async event => {
    const link = event.target.closest('a[data-drawer]');
    if (!link || !drawer || !drawer.showModal || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    currentLoad?.abort();
    currentLoad = new AbortController();
    lastTrigger = link;
    drawer.dataset.agentPath = new URL(link.href).pathname;
    // The panel is for a quick look; this keeps the way out to the whole page.
    const expand = drawer.querySelector('[data-drawer-expand]');
    if (expand) expand.href = link.href;
    content.textContent = 'Yuklanmoqda…';
    if (!drawer.open) drawer.showModal();
    try {
      const url = new URL(link.href); url.searchParams.set('panel', '1');
      const response = await fetch(url, { signal: currentLoad.signal, credentials: 'same-origin' });
      if (!response.ok || response.redirected) { window.location.assign(link.href); return; }
      const html = await response.text();
      // Only our authenticated, server-rendered detail fragment is inserted.
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      content.replaceChildren(...Array.from(parsed.body.childNodes));
      window.SelectSearch?.mount(content);
    } catch (error) {
      if (error.name !== 'AbortError') { drawer.close(); window.location.assign(link.href); }
    }
  });
  on(document.querySelector('[data-drawer-close]'), 'click', () => drawer.close());
  on(drawer, 'click', event => { if (event.target === drawer && event.clientX < drawer.getBoundingClientRect().left) drawer.close(); });
  on(drawer, 'close', () => { currentLoad?.abort(); lastTrigger?.focus(); });
  on(document, 'keydown', event => { if (event.key === 'Escape') closeMenu(); });

  // Long option lists (employees, departments) get a search box of their own.
  window.SelectSearch?.mount();

  const dueInput = document.getElementById('id_due_at');
  const tashkentValue = date => {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Tashkent', year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date);
    const p = Object.fromEntries(parts.map(item => [item.type,item.value]));
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  };
  document.querySelectorAll('[data-due-hours]').forEach(button => on(button, 'click', () => {
    if (dueInput) dueInput.value = tashkentValue(new Date(Date.now() + Number(button.dataset.dueHours) * 3600000));
  }));
  on(document.querySelector('[data-due-clear]'), 'click', () => { if (dueInput) dueInput.value = ''; });

  }
  window.TaskPage = {mount};
  mount();
})();
