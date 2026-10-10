/* BuildScope filter controls island (vanilla, no deps).

   Progressive enhancement only. Every control it upgrades is a real <select>/<input> inside a
   real <form>; if this script never runs, the native control still submits and filters. When it
   runs, a branded listbox replaces the native popup, so Android no longer shows a black system
   sheet. The native element is kept in the DOM (visually hidden) as the form field of record.

   ARIA: combobox + listbox pattern. Keyboard: Up/Down move, Home/End jump, type-to-filter, Enter
   selects, Escape closes and returns focus to the trigger. On mobile the popup is a bottom sheet
   (rounded top, drag handle, sticky search, Apply/Clear) with a focus trap via window.BSDialog. */
(function () {
  'use strict';

  function el(tag, cls, attrs) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  var uid = 0;
  function nextId(p) { uid++; return p + '-' + uid; }

  /* Build a branded listbox over a native <select>. */
  function enhanceSelect(select) {
    if (!select || select.closest('.bs-combobox')) return;   // idempotent
    if (select.multiple) return;                              // multi handled elsewhere
    var label = document.querySelector('label[for="' + select.id + '"]');
    var options = Array.prototype.map.call(select.options, function (o) {
      return { value: o.value, label: o.textContent.trim(), selected: o.selected, disabled: o.disabled };
    });
    var searchable = options.length > 8;   // long lists (city, type) get a filter field

    select.classList.add('bs-select-native');
    select.setAttribute('tabindex', '-1');
    select.setAttribute('aria-hidden', 'true');

    var wrap = el('div', 'bs-combobox');
    var trigger = el('button', 'bs-combobox-trigger', {
      type: 'button', 'aria-haspopup': 'listbox', 'aria-expanded': 'false'
    });
    trigger.id = nextId('bs-cb');
    var valueSpan = el('span', 'bs-combobox-value');
    trigger.appendChild(valueSpan);
    trigger.appendChild(el('span', 'bs-combobox-caret', { 'aria-hidden': 'true' }));

    var pop = el('div', 'bs-combobox-pop', { role: 'presentation', hidden: '' });
    var listId = nextId('bs-lb');
    var search = null;
    if (searchable) {
      var searchWrap = el('div', 'bs-combobox-searchwrap');
      search = el('input', 'bs-combobox-search', {
        type: 'text', role: 'combobox', 'aria-expanded': 'true',
        'aria-controls': listId, 'aria-autocomplete': 'list', placeholder: 'Type to filter…'
      });
      searchWrap.appendChild(search);
      pop.appendChild(searchWrap);
    }
    var list = el('ul', 'bs-listbox', { id: listId, role: 'listbox' });
    if (label) list.setAttribute('aria-label', label.textContent.trim());
    pop.appendChild(list);
    var footer = el('div', 'bs-combobox-footer');
    var clearBtn = el('button', 'btn btn-small btn-ghost', { type: 'button' }); clearBtn.textContent = 'Clear';
    var applyBtn = el('button', 'btn btn-small btn-primary', { type: 'button' }); applyBtn.textContent = 'Apply';
    footer.appendChild(clearBtn); footer.appendChild(applyBtn);
    pop.appendChild(footer);

    select.parentNode.insertBefore(wrap, select);   // wrap replaces the select's position
    wrap.appendChild(select);                       // native select lives inside (visually hidden)
    wrap.appendChild(trigger);
    wrap.appendChild(pop);

    var activeIndex = -1;
    var visible = options.slice();

    function currentLabel() {
      var sel = options[select.selectedIndex];
      return sel ? sel.label : (options[0] ? options[0].label : '');
    }
    function paintTrigger() { valueSpan.textContent = currentLabel(); }
    function setActive(i) {
      activeIndex = i;
      Array.prototype.forEach.call(list.children, function (li, idx) {
        li.classList.toggle('is-active', idx === i);
        if (idx === i) { trigger.setAttribute('aria-activedescendant', li.id); li.scrollIntoView({ block: 'nearest' }); }
      });
    }
    function renderList() {
      var q = (search ? search.value : '').toLowerCase();
      visible = options.filter(function (o) { return !q || o.label.toLowerCase().indexOf(q) !== -1; });
      list.textContent = '';
      visible.forEach(function (o, i) {
        var li = el('li', 'bs-option', { role: 'option', id: nextId('bs-opt') });
        li.dataset.value = o.value;
        if (o.selected) li.setAttribute('aria-selected', 'true');
        li.appendChild(el('span', 'bs-option-check', { 'aria-hidden': 'true' }));
        var t = el('span', 'bs-option-label'); t.textContent = o.label; li.appendChild(t);
        if (o.count != null) { var c = el('span', 'bs-option-count'); c.textContent = o.count; li.appendChild(c); }
        li.addEventListener('click', function () { choose(i); });
        li.addEventListener('mousemove', function () { setActive(i); });
        list.appendChild(li);
      });
      activeIndex = -1;
    }
    var popHome = wrap;   // where the popup belongs in the DOM when closed
    function open() {
      // Reparent to <body>: a transformed/filtered ancestor (the mobile filter sheet) would
      // otherwise become the containing block and break the fixed bottom-sheet positioning.
      document.body.appendChild(pop);
      pop.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      wrap.classList.add('is-open');
      if (search) search.value = '';
      renderList();
      if (search) { search.focus(); }
      else {
        var selIdx = visible.findIndex(function (o) { return o.selected; });
        setActive(selIdx >= 0 ? selIdx : 0);
      }
      if (window.matchMedia('(max-width: 720px)').matches && window.BSDialog) {
        window.BSDialog.open({ trigger: trigger, panel: pop, backdrop: null, bodyClass: 'sheet-open', dialogRole: true });
        pop._bsDialogManaged = true;
      }
      document.addEventListener('keydown', onKey, true);
      document.addEventListener('click', onDocClick, true);
    }
    function close(refocus) {
      if (pop.hidden) return;
      pop.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      wrap.classList.remove('is-open');
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('click', onDocClick, true);
      if (pop._bsDialogManaged && window.BSDialog && window.BSDialog.isOpen()) window.BSDialog.close();
      pop._bsDialogManaged = false;
      if (pop.parentNode !== popHome) popHome.appendChild(pop);
      if (refocus) trigger.focus();
    }
    function choose(i) {
      var o = visible[i];
      if (!o) return;
      select.value = o.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      options.forEach(function (x) { x.selected = (x.value === o.value); });
      paintTrigger();
      close(true);
      if (select.form && wrap.dataset.bsAutoSubmit === '1') select.form.submit();
    }
    function onKey(e) {
      if (pop.hidden) return;
      var k = e.key;
      if (k === 'Escape') { e.preventDefault(); close(true); return; }
      if (k === 'ArrowDown') { e.preventDefault(); setActive(Math.min(activeIndex + 1, visible.length - 1)); return; }
      if (k === 'ArrowUp') { e.preventDefault(); setActive(Math.max(activeIndex - 1, 0)); return; }
      if (k === 'Home') { e.preventDefault(); setActive(0); return; }
      if (k === 'End') { e.preventDefault(); setActive(visible.length - 1); return; }
      if (k === 'Enter') { e.preventDefault(); if (activeIndex >= 0) choose(activeIndex); return; }
      if (search && document.activeElement !== search) { search.focus(); }
    }
    function onDocClick(e) { if (!wrap.contains(e.target)) close(false); }

    trigger.addEventListener('click', function () { if (pop.hidden) open(); else close(true); });
    clearBtn.addEventListener('click', function () { select.value = ''; options.forEach(function (x) { x.selected = x.value === ''; }); paintTrigger(); renderList(); if (search) search.focus(); });
    applyBtn.addEventListener('click', function () { close(true); if (select.form) select.form.submit(); });
    if (search) search.addEventListener('input', function () { renderList(); });
    select.addEventListener('change', paintTrigger);
    paintTrigger();
  }

  function enhanceAll(rootEl) {
    (rootEl || document).querySelectorAll('[data-bs-control] select:not(.bs-select-native)').forEach(enhanceSelect);
  }

  /* Applied-filter chips: read current query and offer one-tap removal. */
  function appliedChips() {
    var bar = document.querySelector('[data-applied-filters]');
    if (!bar) return;
    var params = new URLSearchParams(location.search);
    var labels = { q: 'Keyword', city: 'City', project_type: 'Type', classification: 'Signal', procurement_status: 'Status', date_from: 'From', date_to: 'To' };
    var count = 0;
    Object.keys(labels).forEach(function (key) {
      var v = params.get(key);
      if (!v) return;
      count++;
      var chip = el('span', 'filter-chip');
      var t = el('span'); t.textContent = labels[key] + ': ' + v; chip.appendChild(t);
      var x = el('button', 'filter-chip-x', { type: 'button', 'aria-label': 'Remove ' + labels[key] + ' filter' });
      x.textContent = '\u00d7';
      x.addEventListener('click', function () {
        params.delete(key);
        location.search = params.toString();
      });
      chip.appendChild(x);
      bar.appendChild(chip);
    });
    if (params.get('include_unverified')) {
      count++;
      var chip2 = el('span', 'filter-chip');
      chip2.textContent = 'Including unverified';
      var x2 = el('button', 'filter-chip-x', { type: 'button', 'aria-label': 'Remove unverified toggle' }); x2.textContent = '\u00d7';
      x2.addEventListener('click', function () { params.delete('include_unverified'); location.search = params.toString(); });
      chip2.appendChild(x2); bar.appendChild(chip2);
    }
    if (count) {
      var clearAll = el('a', 'filter-chip filter-chip-clear');
      clearAll.textContent = 'Clear all';
      clearAll.href = location.pathname;
      bar.appendChild(clearAll);
      bar.hidden = false;
    }
  }

  function init() {
    document.documentElement.classList.add('bs-controls');
    enhanceAll();
    appliedChips();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  window.BSControls = { enhanceAll: enhanceAll };
})();
