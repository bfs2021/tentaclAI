"""Service-Health: a round of `svc test` over the monitored services, the history and what the page shows.

Files in data/control/ext/tentaclai_health/ (extapi.data_dir):
  settings.json            interval (minutes), fails (failed rounds in a row before a notification), skip (names)
  state.json               last round, per service: state, detail, since, checked, fails, notified
  history-YYYY-MM-DD.jsonl one line per round: {"t": unix time, "r": {service: "ok"|"partial"|"fail"}} – kept 8 days
"""
import glob
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import extapi

NAME = "tentaclai_health"
DEFAULTS = {"interval": 5, "fails": 2, "skip": []}
INTERVALS = (1, 5, 15, 30, 60)
FAILS = (1, 2, 3, 5)
KEEP_DAYS = 8
SLOTS, SLOT = 48, 1800  # the 24 h bar: 48 slots of 30 minutes
WORKERS = 4
RANK = {"ok": 0, "partial": 1, "fail": 2}

_lock = threading.Lock()


def settings():
    s = dict(DEFAULTS)
    s.update(extapi.load_json(NAME, "settings.json", {}) or {})
    s["interval"] = s["interval"] if s["interval"] in INTERVALS else DEFAULTS["interval"]
    s["fails"] = s["fails"] if s["fails"] in FAILS else DEFAULTS["fails"]
    s["skip"] = sorted({str(x) for x in s["skip"] if isinstance(x, str)})
    return s


def save_settings(interval, fails, skip):
    extapi.save_json(NAME, "settings.json", {"interval": int(interval) if int(interval) in INTERVALS else DEFAULTS["interval"],
                                             "fails": int(fails) if int(fails) in FAILS else DEFAULTS["fails"],
                                             "skip": sorted(set(skip))})


def state():
    s = extapi.load_json(NAME, "state.json", {}) or {}
    s.setdefault("last", 0)
    s.setdefault("services", {})
    return s


def parse(out):
    """svc test's output → (state, detail); None when the sandbox doesn't run (nothing was tested)"""
    lines = [l.strip() for l in (out or "").splitlines() if l.strip().startswith(("✓", "✗"))]
    if not lines:
        if "not running" in (out or ""):
            return None
        return "fail", ((out or "").strip().splitlines() or ["no answer"])[-1][:300]
    bad = [l for l in lines if l.startswith("✗")]
    st = "ok" if not bad else ("partial" if len(bad) < len(lines) else "fail")
    return st, " · ".join(bad or lines)[:500]


def monitored():
    """(checked, not checked) – usable services minus skipped; the rest with the reason"""
    skip = set(settings()["skip"])
    checked, other = [], []
    for s in extapi.services():
        if s["state"] != "ok":
            other.append(dict(s, why={"off": "disabled", "warn": "token missing", "error": "invalid file"}.get(s["state"], s["state"])))
        elif s["name"] in skip:
            other.append(dict(s, why="not monitored"))
        else:
            checked.append(s)
    return checked, other


def due(now=None):
    return (now or time.time()) - state()["last"] >= settings()["interval"] * 60 - 5


def run_round(force=False, now=None):
    """test every monitored service once (when due, or force) – False when a round runs already or wasn't due"""
    if not _lock.acquire(blocking=False):
        return False
    try:
        if not force and not due(now):
            return False
        services, _ = monitored()
        with ThreadPoolExecutor(WORKERS) as pool:
            outs = dict(zip([s["name"] for s in services], pool.map(lambda s: extapi.svc("test", s["name"])[1], services)))
        now = now or time.time()
        results = {n: parse(o) for n, o in outs.items()}
        if results and all(r is None for r in results.values()):
            return False  # the sandbox is down – that's the container event's business, not a service outage
        record(now, results)
        return True
    finally:
        _lock.release()


def record(now, results):
    """store one round: history line, current state, notifications"""
    results = {n: r for n, r in results.items() if r is not None}
    st, cfg = state(), settings()
    with os.fdopen(os.open(_history_file(now), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as f:
        f.write(json.dumps({"t": int(now), "r": {n: r[0] for n, r in results.items()}}) + "\n")
    cur = st["services"]
    for name in list(cur):
        if name not in results:
            cur.pop(name)  # no longer monitored
    for name, (s, detail) in results.items():
        c = cur.setdefault(name, {"state": s, "since": int(now), "fails": 0, "notified": False})
        if c["state"] != s:
            c["state"], c["since"] = s, int(now)
        c["detail"], c["checked"] = detail, int(now)
        c["fails"] = 0 if s == "ok" else c["fails"] + 1
        link = extapi.control_link(f"/x/{NAME}/")
        if s != "ok" and c["fails"] >= cfg["fails"] and not c["notified"]:
            c["notified"], c["down_since"] = True, int(now) - (c["fails"] - 1) * cfg["interval"] * 60
            title = f"{name} is down" if s == "fail" else f"{name}: a part fails"
            extapi.notify("health_down", title, detail, key=f"health-down-{name}", link=link)
        elif s == "ok" and c["notified"]:
            c["notified"] = False
            extapi.notify("health_up", f"{name} is up again", f"down for {ago(now - c.pop('down_since', now))}",
                          key=f"health-up-{name}-{int(now)}", link=link)
    st["last"] = int(now)
    extapi.save_json(NAME, "state.json", st)
    prune(now)


def _history_file(t):
    return os.path.join(extapi.data_dir(NAME), time.strftime("history-%Y-%m-%d.jsonl", time.gmtime(t)))


def prune(now):
    oldest = time.strftime("history-%Y-%m-%d.jsonl", time.gmtime(now - KEEP_DAYS * 86400))
    for p in glob.glob(os.path.join(extapi.data_dir(NAME), "history-*.jsonl")):
        if os.path.basename(p) < oldest:
            os.remove(p)


def history(since):
    """rounds since a time: [(t, {service: state})]"""
    rows = []
    for p in sorted(glob.glob(os.path.join(extapi.data_dir(NAME), "history-*.jsonl"))):
        if os.path.basename(p) < time.strftime("history-%Y-%m-%d.jsonl", time.gmtime(since)):
            continue
        with open(p) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r["t"] >= since:
                        rows.append((r["t"], r["r"]))
                except (ValueError, KeyError, TypeError):
                    continue
    return rows


def availability(rows, name):
    """share of rounds with state ok in %, None without a round"""
    seen = [r[name] for _, r in rows if name in r]
    return round(100 * sum(s == "ok" for s in seen) / len(seen), 1) if seen else None


def bar(rows, name, now):
    """the 24 h bar: SLOTS entries, oldest first – the worst state per 30 minutes, "" without a round"""
    out = [""] * SLOTS
    start = now - SLOTS * SLOT
    for t, r in rows:
        if name in r and t >= start:
            i = min(int((t - start) // SLOT), SLOTS - 1)
            if not out[i] or RANK.get(r[name], 2) > RANK.get(out[i], 0):
                out[i] = r[name]
    return out


def ago(seconds):
    s = int(max(seconds, 0))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    if s < 172800:
        return f"{round(s / 3600)} h"
    return f"{round(s / 86400)} d"


def down_count():
    """services the badge counts: failing at least `fails` rounds in a row"""
    need = settings()["fails"]
    return sum(1 for c in state()["services"].values() if c["state"] != "ok" and c["fails"] >= need)


def overview(now=None):
    """everything the page shows"""
    now = now or time.time()
    st, cfg = state(), settings()
    checked, other = monitored()
    rows7 = history(now - 7 * 86400)
    rows1 = [r for r in rows7 if r[0] >= now - 86400]
    systems = {s["name"]: s for s in extapi.systems()}
    out = []
    for s in checked:
        c = st["services"].get(s["name"], {})
        out.append(dict(s, state=c.get("state", ""), detail=c.get("detail", ""),
                        since=ago(now - c["since"]) if c.get("since") else "",
                        checked=ago(now - c["checked"]) if c.get("checked") else "",
                        day=availability(rows1, s["name"]), week=availability(rows7, s["name"]),
                        bar=bar(rows1, s["name"], now)))
    out.sort(key=lambda x: (-RANK.get(x["state"], -1) if x["state"] else 0, x["name"]))
    groups = {}
    for s in out:
        groups.setdefault(s["system"] or "", []).append(s)
    sysview = []
    for name, items in sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0])):
        states = [i["state"] for i in items if i["state"]]
        worst = max(states, key=lambda x: RANK[x]) if states else ""
        weeks = [i["week"] for i in items if i["week"] is not None]
        sysview.append({"name": name or "Standalone", "description": (systems.get(name) or {}).get("description", ""),
                        "total": len(items), "ok": states.count("ok"), "state": worst,
                        "week": round(sum(weeks) / len(weeks), 1) if weeks else None})
    return {"services": out, "other": other, "systems": sysview, "settings": cfg,
            "last": ago(now - st["last"]) if st["last"] else "",
            "next": ago(max(st["last"] + cfg["interval"] * 60 - now, 0)) if st["last"] else "",
            "running": _lock.locked(), "down": down_count()}
