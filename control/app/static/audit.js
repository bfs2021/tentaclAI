// Logs → Audit: row details, facet filter and "more…", remembered facet state, drag-to-zoom on the histogram, mobile filters
(function () {
  'use strict';
  var KEY = 'tc-audit-facets';
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } }
  function save(v) { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* private window */ } }

  // a click on a row (not on a link or button inside it) opens its details
  document.addEventListener('click', function (ev) {
    var copy = ev.target.closest('[data-copy]');
    if (copy) {
      var pre = copy.parentNode.querySelector('pre');
      if (pre && navigator.clipboard) navigator.clipboard.writeText(pre.textContent).then(function () { copy.textContent = 'Copied'; });
      return;
    }
    var row = ev.target.closest('[data-audit-row]');
    if (!row || ev.target.closest('a, button, summary, input')) return;
    var detail = row.nextElementSibling;
    if (!detail || !detail.classList.contains('audit-detail')) return;
    detail.hidden = !detail.hidden;
    row.setAttribute('aria-expanded', String(!detail.hidden));
    row.classList.toggle('open', !detail.hidden);
  });
  document.addEventListener('keydown', function (ev) {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches && ev.target.matches('[data-audit-row]')) {
      ev.preventDefault();
      ev.target.querySelector('td').click();
    }
  });

  function init() {
    // facets: open / closed as last time (only fields without a chosen value)
    var state = load();
    document.querySelectorAll('details.facet').forEach(function (d) {
      var f = d.dataset.facet;
      if (f in state && !d.querySelector('.facet-v.on, .facet-v.off')) d.open = state[f];
      d.addEventListener('toggle', function () { var s = load(); s[f] = d.open; save(s); });
    });
    document.querySelectorAll('[data-facet-more]').forEach(function (b) {
      b.addEventListener('click', function () { b.closest('details').classList.add('all'); b.remove(); });
    });
    var filter = document.querySelector('[data-facet-filter]');
    if (filter) filter.addEventListener('input', function () {
      var t = filter.value.trim().toLowerCase();
      document.querySelectorAll('.audit-facets').forEach(function (box) { box.classList.toggle('filtering', !!t); });
      document.querySelectorAll('.facet-v').forEach(function (v) { v.classList.toggle('hit', !!t && v.dataset.v.indexOf(t) >= 0); });
      if (t) document.querySelectorAll('details.facet').forEach(function (d) { d.open = !!d.querySelector('.facet-v.hit'); });
    });
    var toggle = document.querySelector('[data-audit-filters]');
    if (toggle) toggle.addEventListener('click', function () { document.querySelector('.audit-layout').classList.toggle('show-side'); });

    // histogram: drag across bars → that span as the time range
    var svg = document.querySelector('[data-hist]');
    if (!svg) return;
    var bars = Array.prototype.slice.call(svg.querySelectorAll('a[data-i]'));
    var start = null, end = null, moved = false;
    function bar(ev) { var a = ev.target.closest && ev.target.closest('a[data-i]'); return a ? +a.dataset.i : null; }
    function mark() {
      bars.forEach(function (a) {
        var i = +a.dataset.i;
        a.classList.toggle('sel', start !== null && i >= Math.min(start, end) && i <= Math.max(start, end));
      });
    }
    function local(t) {
      var d = new Date(t * 1000), p = function (n) { return (n < 10 ? '0' : '') + n; };
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
    }
    svg.addEventListener('mousedown', function (ev) { var i = bar(ev); if (i === null) return; ev.preventDefault(); start = end = i; moved = false; mark(); });
    svg.addEventListener('mousemove', function (ev) { if (start === null) return; var i = bar(ev); if (i !== null && i !== end) { end = i; moved = true; mark(); } });
    svg.addEventListener('click', function (ev) { if (moved) ev.preventDefault(); });
    document.addEventListener('mouseup', function () {
      if (start === null) return;
      var a = Math.min(start, end), b = Math.max(start, end);
      start = null;
      if (!moved) { mark(); return; }
      var url = new URL(location.href);
      url.searchParams.set('range', 'custom');
      url.searchParams.set('from', local(+bars[a].dataset.from));
      url.searchParams.set('to', local(+bars[b].dataset.to - 1));
      ['since', 'start', 'end'].forEach(function (k) { url.searchParams.delete(k); });
      location.href = url.toString();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
