/* Searchable selects: long employee lists are picked by typing, not by scrolling.

   The original <select> stays in the form and keeps its value, so server-side
   rendering, the agent filling a form and browser validation all keep working;
   the combobox above it is only a way to reach an option. */
(() => {
  'use strict';
  const MIN_OPTIONS = 8;

  // Uzbek names are written with several apostrophes and cases: fold them away
  // so "A'zamov", "Aʼzamov" and "azamov" all match the same person.
  const fold = text => (text || '').toLowerCase()
    .replace(/[ʻʼ‘’'`´]/g, '')
    .replace(/\s+/g, ' ').trim();

  function enhance(select) {
    if (select.dataset.searchReady === 'true' || select.multiple || select.disabled) return;
    if (select.dataset.search === 'off') return;
    if (select.options.length < MIN_OPTIONS && select.dataset.search !== 'on') return;
    select.dataset.searchReady = 'true';

    const box = document.createElement('div');
    box.className = 'select-search';
    select.parentNode.insertBefore(box, select);
    box.appendChild(select);

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'select-search-input';
    input.autocomplete = 'off';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    const list = document.createElement('ul');
    list.className = 'select-search-list';
    list.hidden = true;
    list.setAttribute('role', 'listbox');
    list.id = (select.id || `select-${Math.random().toString(36).slice(2)}`) + '-list';
    input.setAttribute('aria-controls', list.id);
    box.append(input, list);

    // The label points at the hidden select; clicking it must reach the box.
    const label = select.id ? document.querySelector(`label[for="${CSS.escape(select.id)}"]`) : null;
    if (label) {
      input.id = select.id + '-search';
      label.setAttribute('for', input.id);
    } else {
      input.setAttribute('aria-label', select.getAttribute('aria-label') || 'Tanlash');
    }

    const empty = [...select.options].find(option => option.value === '');
    input.placeholder = (empty ? empty.textContent : '').trim() || 'Qidirish…';

    const chosen = () => select.options[select.selectedIndex];
    const labelText = () => {
      const option = chosen();
      return option && option.value ? option.textContent.trim() : '';
    };
    const showValue = () => { input.value = labelText(); };
    showValue();

    let active = -1;

    function render(query) {
      const needle = fold(query);
      list.replaceChildren();
      active = -1;
      [...select.options].forEach(option => {
        if (!option.value && needle) return;  // "Ijrochini tanlang" is not a match
        if (needle && !fold(option.textContent).includes(needle)) return;
        const item = document.createElement('li');
        item.className = 'select-search-option';
        item.textContent = option.textContent.trim() || input.placeholder;
        item.dataset.value = option.value;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.value === select.value));
        if (option.disabled) item.dataset.disabled = 'true';
        list.appendChild(item);
      });
      if (!list.children.length) {
        const none = document.createElement('li');
        none.className = 'select-search-empty';
        none.textContent = 'Topilmadi';
        list.appendChild(none);
      }
    }

    const items = () => [...list.querySelectorAll('.select-search-option:not([data-disabled])')];

    function highlight(index) {
      const options = items();
      options.forEach(item => item.classList.remove('active'));
      active = index;
      const item = options[index];
      if (!item) return;
      item.classList.add('active');
      item.scrollIntoView({block: 'nearest'});
    }

    function open() {
      if (!list.hidden) return;
      render('');
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      box.classList.add('open');
      const current = items().findIndex(item => item.dataset.value === select.value);
      highlight(current < 0 ? 0 : current);
      input.select();
    }

    function close() {
      if (list.hidden) return;
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      box.classList.remove('open');
      showValue();
    }

    function pick(value) {
      select.value = value;
      // Filters and any other listener react as if the person used the select.
      select.dispatchEvent(new Event('input', {bubbles: true}));
      select.dispatchEvent(new Event('change', {bubbles: true}));
      close();
    }

    input.addEventListener('focus', open);
    input.addEventListener('mousedown', () => { if (list.hidden) setTimeout(open, 0); });
    input.addEventListener('input', () => {
      if (list.hidden) { list.hidden = false; input.setAttribute('aria-expanded', 'true'); box.classList.add('open'); }
      render(input.value);
      highlight(0);
    });
    input.addEventListener('keydown', event => {
      const options = items();
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (list.hidden) { open(); return; }
        const step = event.key === 'ArrowDown' ? 1 : -1;
        highlight((active + step + options.length) % (options.length || 1));
      } else if (event.key === 'Enter') {
        if (list.hidden) return;  // Enter without the list open submits the form.
        event.preventDefault();
        if (options[active]) pick(options[active].dataset.value);
      } else if (event.key === 'Escape') {
        if (!list.hidden) { event.stopPropagation(); close(); }
      } else if (event.key === 'Tab') {
        close();
      }
    });
    list.addEventListener('mousedown', event => {
      const item = event.target.closest('.select-search-option');
      if (!item || item.dataset.disabled) return;
      event.preventDefault();  // Keep the focus so the blur does not reopen anything.
      pick(item.dataset.value);
    });
    document.addEventListener('mousedown', event => { if (!box.contains(event.target)) close(); });
    input.addEventListener('blur', () => setTimeout(() => { if (!box.contains(document.activeElement)) close(); }, 0));

    // The agent and any server-rendered reset change the select directly.
    select.addEventListener('change', () => { if (list.hidden) showValue(); });
    // A required select cannot show its own message while it is out of sight.
    select.addEventListener('invalid', () => setTimeout(() => input.focus(), 0));
  }

  function mount(root = document) {
    root.querySelectorAll('select').forEach(enhance);
  }

  window.SelectSearch = {mount, enhance};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mount());
  else mount();
})();
