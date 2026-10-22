// tentacontrol – toasts, confirm dialogs, small helpers (htmx does the rest)
(function () {
  function toast(msg, kind) {
    var box = document.getElementById("toasts");
    if (!box) return;
    var el = document.createElement("div");
    el.className = "toast " + (kind || "ok");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function () { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 3800);
    setTimeout(function () { el.remove(); }, 4200);
  }
  window.tcToast = toast;

  document.body.addEventListener("toast", function (e) { toast(e.detail.msg, e.detail.kind); });
  document.body.addEventListener("htmx:responseError", function (e) {
    toast("Error " + e.detail.xhr.status + ": " + (e.detail.xhr.responseText || "").slice(0, 140), "err");
  });
  document.body.addEventListener("htmx:sendError", function () { toast("No connection to tentacontrol", "err"); });

  // flash message rendered by the server after a reload
  var flash = document.getElementById("flash");
  if (flash) toast(flash.dataset.msg, flash.dataset.kind);

  // copy-to-clipboard buttons
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    var src = document.querySelector(b.dataset.copy);
    navigator.clipboard.writeText((src.value || src.innerText || src.textContent || "").trim()).then(function () { toast("Copied", "info"); });
  });
  // searchable select: <div data-combo> with hidden input + text input + .combo-opt list
  function initCombo(box) {
    var hidden = box.querySelector("input[type=hidden]"), input = box.querySelector(".combo-input");
    var list = box.querySelector(".combo-list"), empty = box.querySelector(".combo-empty");
    var opts = Array.prototype.slice.call(box.querySelectorAll(".combo-opt"));
    var groups = Array.prototype.slice.call(box.querySelectorAll(".combo-group"));
    var active = null;
    function current() { return opts.filter(function (o) { return o.dataset.value === hidden.value; })[0]; }
    function visible() { return opts.filter(function (o) { return !o.hidden; }); }
    function setActive(o) {
      if (active) active.classList.remove("active");
      active = o || null;
      if (active) { active.classList.add("active"); active.scrollIntoView({ block: "nearest" }); }
    }
    function filter(q) {
      var words = q.toLowerCase().split(/\s+/).filter(Boolean);
      opts.forEach(function (o) {
        var hay = (o.dataset.label + " " + o.dataset.value + " " + (o.dataset.search || "")).toLowerCase();
        o.hidden = !words.every(function (w) { return hay.indexOf(w) >= 0; });
      });
      groups.forEach(function (g) {  // hide a heading when all its options are hidden
        var n = g.nextElementSibling, any = false;
        while (n && n.classList.contains("combo-opt")) { if (!n.hidden) any = true; n = n.nextElementSibling; }
        g.hidden = !any;
      });
      if (empty) empty.hidden = visible().length > 0;
      setActive(visible()[0]);
    }
    function open() {
      list.hidden = false; input.setAttribute("aria-expanded", "true");
      filter("");
      var c = current();
      opts.forEach(function (o) { o.classList.toggle("selected", o === c); });
      setActive(c);
    }
    function close() {
      list.hidden = true; input.setAttribute("aria-expanded", "false");
      var c = current(); input.value = c ? c.dataset.label : "";
    }
    function choose(o) {
      hidden.value = o.dataset.value;
      close();
      box.dispatchEvent(new CustomEvent("combo-change", { bubbles: true, detail: { value: o.dataset.value, desc: o.dataset.desc || "" } }));
    }
    input.addEventListener("focus", function () { open(); input.select(); });
    input.addEventListener("click", function () { if (list.hidden) open(); });
    input.addEventListener("input", function () { if (list.hidden) open(); filter(input.value); });
    input.addEventListener("blur", function () { setTimeout(close, 120); });
    input.addEventListener("keydown", function (e) {
      var v = visible(), i = v.indexOf(active);
      if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) open(); else setActive(v[Math.min(i + 1, v.length - 1)]); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(v[Math.max(i - 1, 0)]); }
      else if (e.key === "Enter") { if (!list.hidden) { e.preventDefault(); if (active) choose(active); } }
      else if (e.key === "Escape") { if (!list.hidden) { e.preventDefault(); close(); } }
    });
    list.addEventListener("mousedown", function (e) { e.preventDefault(); });  // keep focus in the input
    list.addEventListener("click", function (e) { var o = e.target.closest(".combo-opt"); if (o) choose(o); });
    list.addEventListener("mousemove", function (e) { var o = e.target.closest(".combo-opt"); if (o && o !== active) setActive(o); });
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-combo]"), initCombo);

  // "⋯" menus (<details class="menu">): close on outside click, Escape, or after choosing an item
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.menu[open]").forEach(function (m) {
      if (!m.contains(e.target) || e.target.closest(".menu-item")) m.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.querySelectorAll("details.menu[open]").forEach(function (m) { m.removeAttribute("open"); });
  });
})();

// deep link to a settings field (#f-KEY): scroll to it, focus it and flash it once
(function () {
  if (!location.hash.startsWith("#f-")) return;
  var el = document.getElementById(location.hash.slice(1));
  if (!el) return;
  el.scrollIntoView({ block: "center" });
  el.focus({ preventScroll: true });
  el.classList.add("flash");
  setTimeout(function () { el.classList.remove("flash"); }, 2000);
})();

// an empty response (toast / redirect / refresh only) must never wipe the element that sent the request
document.body.addEventListener("htmx:beforeSwap", function (e) {
  if (e.detail.xhr && e.detail.xhr.status < 300 && !(e.detail.serverResponse || "").trim()) e.detail.shouldSwap = false;
});

// [data-toggle=<id>] folds a hidden "add" form in and out; opening focuses its first field
document.addEventListener("click", function (e) {
  var btn = e.target.closest("[data-toggle]");
  if (!btn) return;
  var box = document.getElementById(btn.dataset.toggle), open = box.hidden;
  box.hidden = !open; btn.setAttribute("aria-expanded", open); btn.classList.toggle("open", open);
  var f = open && box.querySelector("input:not([type=hidden]), select, textarea");
  if (f) f.focus();
});

// pending approvals while tentacontrol is open in some tab: count in the tab title and on the favicon, a desktop
// notification when the tab isn't in front (e.g. working in claude.ai) – a long poll, not a timer: browsers slow down
// timers of hidden tabs to once a minute, an answered request is handled at once
(function () {
  if (!document.querySelector(".sidebar")) return;
  var PREF = "tc-desktop-notify", SEEN = "tc-approvals-notified";
  var title = document.title, icon = document.querySelector("link[rel=icon]"), iconHref = icon && icon.href, iconImg = null;
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function supported() { return "Notification" in window && window.isSecureContext; }
  function enabled() { return supported() && Notification.permission === "granted" && store(PREF) !== "off"; }

  function badge(n) {
    document.title = (n ? "(" + n + ") " : "") + title;
    if (!icon) return;
    if (!n) { icon.href = iconHref; return; }
    function draw() {
      var c = document.createElement("canvas"); c.width = c.height = 32;
      var g = c.getContext("2d");
      g.drawImage(iconImg, 0, 0, 32, 32);
      g.beginPath(); g.arc(23, 9, 9, 0, 2 * Math.PI); g.fillStyle = "#e5484d"; g.fill();
      g.fillStyle = "#fff"; g.font = "bold 13px sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(n > 9 ? "9+" : String(n), 23, 10);
      icon.href = c.toDataURL("image/png");
    }
    if (iconImg && iconImg.complete) return draw();
    iconImg = new Image(); iconImg.onload = draw; iconImg.src = iconHref;
  }

  var shown = {};  // id → Notification of this tab, closed once the request is decided (here, in the banner or elsewhere)
  function notify(d) {
    var seen = {};
    try { seen = JSON.parse(store(SEEN) || "{}"); } catch (e) { seen = {}; }
    if (seen.boot !== d.boot) seen = { boot: d.boot, ids: [] };
    var ids = d.pending.map(function (a) { return a.id; });
    var fresh = d.pending.filter(function (a) { return seen.ids.indexOf(a.id) < 0; });
    seen.ids = ids;  // only what is still pending – a shared list, so a second open tab doesn't notify again
    store(SEEN, JSON.stringify(seen));
    Object.keys(shown).forEach(function (id) { if (ids.indexOf(id) < 0) { shown[id].close(); delete shown[id]; } });
    if (!fresh.length || !enabled() || (document.hasFocus() && !document.hidden)) return;
    fresh.forEach(function (a) {
      var n = new Notification(a.part === "network" ? "Network: " + a.service : "Approval needed: " + a.service + " (" + a.part + ")",
        { body: a.action + (a.from ? "\nfrom " + a.from : ""), tag: "tc-approval-" + d.boot + "-" + a.id, requireInteraction: true,
          icon: "/static/apple-touch-icon.png" });
      n.onclick = function () { window.focus(); n.close(); location.href = "/activity#a-" + a.id; };
      shown[a.id] = n;
    });
  }

  function poll(sig) {
    fetch("/approvals/watch?wait=" + (sig ? 25 : 0) + "&seen=" + encodeURIComponent(sig || ""), { credentials: "same-origin" })
      .then(function (r) { return /json/.test(r.headers.get("content-type") || "") ? r.json() : Promise.reject(r.status); })
      .then(function (d) { badge(d.pending.length); notify(d); poll(d.sig); })
      .catch(function () { setTimeout(function () { poll(""); }, 15000); });  // signed out or tentacontrol restarting
  }
  poll("");

  // the switch on Activity: asks the browser once, then turns the notifications of this browser on and off
  var btn = document.querySelector("[data-desktop-notify]");
  function show() {
    if (!btn) return;
    btn.hidden = false;
    var label = btn.querySelector("span");
    if (!supported()) { label.textContent = "Desktop notifications: need https"; btn.disabled = true; return; }
    var p = Notification.permission;
    label.textContent = "Desktop notifications: " + (p === "denied" ? "blocked" : enabled() ? "on" : "off");
    btn.classList.toggle("on", enabled());
  }
  if (btn) btn.addEventListener("click", function () {
    var p = Notification.permission;
    if (p === "denied") return window.tcToast("Blocked in this browser – allow notifications in the site settings of this page", "warn");
    if (p === "default") return Notification.requestPermission().then(function () { store(PREF, "on"); show(); });
    store(PREF, enabled() ? "off" : "on"); show();
    if (enabled()) new Notification("tentaclAI", { body: "Desktop notifications are on for approvals.", icon: "/static/apple-touch-icon.png" });
  });
  show();
})();

// deep link to an approval (#a-ID, from a notification or svc): scroll to it and flash it once the list is loaded
document.body.addEventListener("htmx:afterSwap", function () {
  if (!location.hash.startsWith("#a-") || document.body.dataset.approvalShown === location.hash) return;
  var el = document.getElementById(location.hash.slice(1));
  if (!el) return;
  document.body.dataset.approvalShown = location.hash;
  el.scrollIntoView({ block: "center" });
  el.classList.add("flash");
});
