/* BuildScope shared motion system (vanilla island).
   Loaded with a CSP nonce, deferred, after the vendored Motion One UMD build.

   Rules the whole file obeys:
   - Motion is progressive enhancement: every animated element works with JS off and with
     prefers-reduced-motion. We only ever touch transform/opacity, and we set the resting state
     in CSS so nothing shifts if the script never runs (CLS stays 0).
   - One module, named behaviours. No per-page scripts.
   - No external calls, no eval. ES5-compatible syntax only. */
(function () {
  'use strict';

  var Motion = window.Motion;
  var root = document.documentElement;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function on(el) {
    if (!el) return false;
    el.classList.add('bs-anim-on');
    return true;
  }

  /* 1. Staggered reveal of cards/lists on scroll. IntersectionObserver + Motion. */
  function reveal() {
    var sel = '[data-reveal], .card, .opportunity-card, .lifecycle-card, .stat-card';
    var items = Array.prototype.slice.call(document.querySelectorAll(sel));
    if (!items.length) return;
    // Only elements below the fold participate; above-the-fold content must paint instantly.
    var fold = window.innerHeight;
    var deferred = [];
    items.forEach(function (el) {
      var top = el.getBoundingClientRect().top;
      on(el);
      if (top < fold * 0.9) {
        el.classList.add('bs-in');
      } else {
        deferred.push(el);
      }
    });
    if (reduce || !Motion || !('IntersectionObserver' in window)) {
      deferred.forEach(function (el) { el.classList.add('bs-in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries, obs) {
      var i = 0;
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        obs.unobserve(el);
        Motion.animate(el, { opacity: [0, 1], transform: ['translateY(14px)', 'translateY(0)'] },
          { duration: 0.48, delay: Math.min(i * 0.05, 0.25), easing: [0.22, 1, 0.36, 1] });
        el.classList.add('bs-in');
        i++;
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
    deferred.forEach(function (el) { io.observe(el); });
  }

  /* 2. Count-up for hero stats, ending on the exact snapshot number (never invented). */
  function countUp() {
    var nums = document.querySelectorAll('[data-count]');
    if (!nums.length) return;
    function run(el) {
      var target = parseFloat(el.getAttribute('data-count'));
      if (isNaN(target)) return;
      var suffix = el.getAttribute('data-count-suffix') || '';
      var grouped = el.getAttribute('data-count-grouped') === '1';
      function fmt(n) {
        var s = grouped ? Math.round(n).toLocaleString('en-US') : String(Math.round(n));
        return s + suffix;
      }
      if (reduce || !Motion) { el.textContent = fmt(target); return; }
      var state = { v: 0 };
      Motion.animate(state, { v: target }, {
        duration: 0.9, easing: [0.22, 1, 0.36, 1],
        onUpdate: function () { el.textContent = fmt(state.v); },
        onComplete: function () { el.textContent = fmt(target); }
      });
    }
    if (!('IntersectionObserver' in window)) { nums.forEach(run); return; }
    var io = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        obs.unobserve(entry.target);
        run(entry.target);
      });
    }, { threshold: 0.4 });
    nums.forEach(function (el) { io.observe(el); });
  }

  /* 3. Hover lift + press feedback for cards/buttons (CSS does the work; nothing to do in JS). */

  /* 4. Sticky header that compacts after 24px scroll. */
  function stickyHeader() {
    var header = document.querySelector('.site-header');
    if (!header) return;
    var last = -1;
    function tick() {
      var y = window.scrollY || window.pageYOffset;
      var compact = y > 24;
      if (compact !== last) { header.classList.toggle('bs-compact', compact); last = compact; }
    }
    window.addEventListener('scroll', tick, { passive: true });
    tick();
  }

  /* 6. Segmented control: a sliding indicator behind the active option (purely visual). */
  function segmented() {
    document.querySelectorAll('[data-segmented]').forEach(function (seg) {
      var ind = seg.querySelector('.seg-indicator');
      function move() {
        var active = seg.querySelector('.seg-opt[aria-pressed="true"], .seg-opt[data-active="1"]');
        if (!ind || !active) return;
        if (reduce) { ind.style.transform = 'none'; ind.style.width = '0'; return; }
        ind.style.width = active.offsetWidth + 'px';
        ind.style.transform = 'translateX(' + active.offsetLeft + 'px)';
      }
      seg.addEventListener('click', function () { requestAnimationFrame(move); });
      window.addEventListener('resize', move);
      requestAnimationFrame(move);
    });
  }

  /* 10. Expanding disclosures animate height. */
  function disclosures() {
    document.querySelectorAll('details.bs-anim-height').forEach(function (d) {
      var body = d.querySelector('.details-body') || d.querySelector('div');
      if (!body || reduce || !Motion) return;
      body.style.overflow = 'hidden';
      d.addEventListener('toggle', function () {
        if (d.open) {
          body.style.height = 'auto';
          var h = body.offsetHeight;
          Motion.animate(body, { height: ['0px', h + 'px'] }, { duration: 0.32, easing: [0.22, 1, 0.36, 1] });
        } else {
          var h2 = body.offsetHeight;
          Motion.animate(body, { height: [h2 + 'px', '0px'] }, { duration: 0.2, easing: [0.22, 1, 0.36, 1] });
        }
      });
    });
  }

  /* 8. Smooth page transitions via the View Transitions API when supported. */
  function viewTransitions() {
    if (!document.startViewTransition || reduce) return;
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a || a.target || a.hasAttribute('download')) return;
      var url = a.href;
      if (!url || url.indexOf(location.origin) !== 0 || a.getAttribute('href').charAt(0) === '#') return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
      document.startViewTransition(function () { location.href = url; });
    });
  }

  function init() {
    root.classList.add('bs-js');
    reveal();
    countUp();
    stickyHeader();
    segmented();
    disclosures();
    viewTransitions();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
