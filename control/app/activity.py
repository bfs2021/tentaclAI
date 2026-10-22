"""Activity: pending approvals (access mode "confirm") and the audit log written by svc (network decisions: by tentacontrol).

svc talks to the /internal/* endpoints with the token in data/control/svc-token; approvals live in memory –
after a restart of tentacontrol, waiting requests end as "error" and svc does not execute them (fail closed).
"""
import asyncio
import itertools
import json
import os
import re
import secrets
import time
from datetime import datetime

import tclib

from . import dockerops, jsonstore, notify, usage
from .config import DATA_DIR

TOKEN_FILE = os.path.join(DATA_DIR, "svc-token")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.jsonl")
TIMEOUT = 600
GRANT_SECONDS = 15 * 60
NO_GRANT_PARTS = ("config",)  # service files / site rules: every write is approved on its own

_ids = itertools.count(1)
BOOT = secrets.token_hex(4)  # ids start at 1 again after a restart – the browser tells the two apart by this
APPROVALS = {}  # id -> dict
GRANTS = {}  # (service, part) -> {"until": ts, "by": who} – "Approve + 15 min"


def ensure_token():
    """shared secret for svc → tentacontrol (readable in the sandbox through the stack mount)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE + ".tmp", "w") as f:
            f.write(secrets.token_urlsafe(32))
        os.chmod(TOKEN_FILE + ".tmp", 0o600)
        os.replace(TOKEN_FILE + ".tmp", TOKEN_FILE)


def token_ok(value):
    try:
        return bool(value) and secrets.compare_digest(value, open(TOKEN_FILE).read().strip())
    except OSError:
        return False


def audit(service, part, mode, action, decision, detail=""):
    """append one line to the audit log, in svc's format – for what only tentacontrol sees (network decisions); never raises"""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "service": service, "part": part, "mode": mode,
             "action": action[:500], "decision": decision}
    if detail:
        entry["detail"] = detail[:300]
    try:
        import fcntl
        if os.path.exists(AUDIT_FILE) and os.path.getsize(AUDIT_FILE) > 5 * 2**20:
            os.replace(AUDIT_FILE, AUDIT_FILE + ".1")
        with open(AUDIT_FILE, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def audit_network(a, decision, detail=""):
    """a decided request of the egress gateway (unknown destination) – svc never sees these"""
    if a.get("part") == "network":
        audit(a["service"], "network", "confirm", a["action"], decision, detail or a.get("by", ""))


def _expire():
    now = time.time()
    for a in APPROVALS.values():
        if a["status"] == "pending" and now - a["created"] > TIMEOUT:
            a.update(status="expired", decided=now)
            a["event"].set()
            audit_network(a, "expired", "no decision in time")
            notify.emit("expired", f"Approval expired: {a['service']}", a["action"], key=f"expired:{a['id']}")
    for k in [k for k, a in APPROVALS.items() if a["status"] != "pending" and now - a.get("decided", now) > 3600]:
        APPROVALS.pop(k, None)


_ORIGIN_KEYS = {"session": r"(cse_[A-Za-z0-9]{10,60}|[0-9a-f-]{36})", "entry": r"[a-z][a-z-]{0,19}", "tmux": r"[A-Za-z0-9._-]{1,40}",
                "by": r"root", "job": r"[a-z0-9]{6,40}"}


def clean_origin(o):
    """only the known keys with well-formed values – svc reports them, but Claude could have set them"""
    if not isinstance(o, dict):
        return {}
    return {k: str(v) for k, v in o.items() if k in _ORIGIN_KEYS and re.fullmatch(_ORIGIN_KEYS[k], str(v))}


def origin_label(o):
    """'Remote Control · Mailcow prüfen' – where a request came from (a hint: Claude controls these values)"""
    o = clean_origin(o)
    if not o:
        return ""
    if o.get("by") == "root":
        return "tentacontrol / host"
    if o.get("job"):  # set by svc from the job's process marker, not from Claude's variables
        from .scheduled import running_job_name
        return f"Job · {running_job_name(o['job']) or o['job']}"
    parts = []
    if o.get("tmux"):
        parts.append(dockerops.session_label(o["tmux"]))
    elif o.get("entry"):
        parts.append({"cli": "Claude terminal", "sdk-cli": "Claude (headless / Remote Control)"}.get(o["entry"], f"Claude ({o['entry']})"))
    else:
        parts.append("shell (no Claude session)")
    if o.get("session"):
        title = (usage._local()["sessions"].get(o["session"]) or {}).get("title", "")
        parts.append(f"“{title}”" if title else o["session"][:8])
    return " · ".join(parts)


def _again(service, part, action, origin):
    """the same write of the same Claude session asked again after svc handed it back (approval_handoff): still
    pending, or decided and not used yet – a decision is used once, by the first call that sees it"""
    sid = origin.get("session")
    if not sid or part == "network":
        return None
    now = time.time()
    return next((a for a in sorted(APPROVALS.values(), key=lambda a: a["created"])
                 if (a["service"], a["part"], a["action"]) == (service, part, action) and a["origin"].get("session") == sid
                 and not a.get("used") and (a["status"] == "pending" or now - a.get("decided", now) < TIMEOUT)), None)


def use(a):
    """svc learns a decision once – a second call waiting on the same request gets an error and runs nothing
    (network requests are shared on purpose: every connection to that target waits for the one decision)"""
    if a["status"] == "pending" or a["part"] == "network":
        return {"status": "pending", "by": a["by"]}
    if a.get("used"):
        return {"status": "error", "by": "this approval was already used by another call"}
    a["used"] = True
    return {"status": a["status"], "by": a["by"]}


def create(service, part, action, origin=None):
    """new request; approved at once while a grant for this service + part is active; one pending request per network
    target; the same request again for a session that asks once more after svc handed it back"""
    _expire()
    if part == "network":
        same = next((a for a in APPROVALS.values() if a["status"] == "pending" and a["service"] == service and a["part"] == part), None)
        if same:
            return same["id"]
    o = clean_origin(origin)
    same = _again(str(service)[:60], str(part)[:20], str(action)[:500], o)
    if same:
        same["again"] = True
        return same["id"]
    rid = str(next(_ids))
    a = {"id": rid, "service": str(service)[:60], "part": str(part)[:20], "action": str(action)[:500],
         "created": time.time(), "status": "pending", "by": "", "event": asyncio.Event(), "origin": o}
    a["from"] = origin_label(a["origin"])
    g = active_grant(a["service"], a["part"])
    if g:
        a.update(status="approved", decided=time.time(), by=f"grant until {time.strftime('%H:%M', time.localtime(g['until']))} ({g['by']})")
        a["event"].set()
    APPROVALS[rid] = a
    if a["status"] == "pending":
        notify.emit("approval", f"Approval needed: {a['service']} ({a['part']})",
                    a["action"] + (f"\nFrom: {a['from']}" if a["from"] else ""), key=f"approval:{rid}",
                    link=notify.control_link("/activity"))
    return rid


def decide(rid, approve, who="tentacontrol", grant=False, seconds=None):
    a = APPROVALS.get(rid)
    if not a or a["status"] != "pending":
        return False
    a.update(status="approved" if approve else "denied", decided=time.time(), by=who)
    if approve and grant and a["part"] not in NO_GRANT_PARTS:
        GRANTS[(a["service"], a["part"])] = {"until": time.time() + (seconds or GRANT_SECONDS), "by": who}
        a["by"] = who + f" + {(seconds or GRANT_SECONDS) // 60} min"
    a["event"].set()
    return True


def active_grant(service, part):
    g = GRANTS.get((service, part))
    if g and g["until"] > time.time() and part not in NO_GRANT_PARTS:
        return g
    GRANTS.pop((service, part), None)
    return None


def grants():
    now = time.time()
    for k in [k for k, g in GRANTS.items() if g["until"] <= now]:
        GRANTS.pop(k, None)
    return [{"service": s, "part": p, "until_s": time.strftime("%H:%M", time.localtime(g["until"])), **g}
            for (s, p), g in sorted(GRANTS.items(), key=lambda x: x[1]["until"])]


def revoke(service, part):
    return GRANTS.pop((service, part), None) is not None


async def wait(rid, seconds):
    a = APPROVALS.get(rid)
    if not a:
        return {"status": "error", "by": "unknown request (tentacontrol restarted?)"}
    if a["status"] == "pending":
        try:
            await asyncio.wait_for(a["event"].wait(), timeout=max(1, min(seconds, 30)))
        except asyncio.TimeoutError:
            pass
    _expire()
    return use(a)


def pending():
    _expire()
    return sorted((a for a in APPROVALS.values() if a["status"] == "pending"), key=lambda a: a["created"])


def recent_decisions(limit=10):
    done = [a for a in APPROVALS.values() if a["status"] != "pending"]
    return sorted(done, key=lambda a: a.get("decided", 0), reverse=True)[:limit]


SINCE = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}  # time filter of the audit log


def origin_group(o):
    """host (tentacontrol / host) · job · session – for the audit log's From filter"""
    o = clean_origin(o)
    if not o:
        return ""
    if o.get("by") == "root":
        return "host"
    return "job" if o.get("job") else "session"


def origin_link(o):
    """where to look next: the job's run, or the terminal of a session that can be reattached without starting anything"""
    o = clean_origin(o)
    if o.get("job"):
        return f"/jobs#run-{o['job']}"
    return {"claude-workspace": "/terminal?p=claude", "claude-rc": "/terminal?p=rc"}.get(o.get("tmux", ""), "")


def _epoch(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (TypeError, ValueError):
        return 0


FLAGS_SEEN = os.path.join(DATA_DIR, "flags-seen.json")
_flags = {"key": None, "times": []}


def _flag_times():
    """when each flagged entry was written – cached by the audit log's size and mtime (the sidebar asks every 5 s)"""
    try:
        st = os.stat(AUDIT_FILE)
    except OSError:
        return []
    key = (st.st_size, st.st_mtime)
    if _flags["key"] != key:
        times = []
        with open(AUDIT_FILE) as f:
            for line in f:
                if '"flagged"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("decision") == "flagged":
                    times.append(_epoch(e.get("ts")))
        _flags.update(key=key, times=times)
    return _flags["times"]


def unseen_flags():
    """possible prompt injections flagged since Activity was last opened (the ⚠ badge next to Activity, the Overview);
    the first call starts counting from now, so older entries don't light it up after an update"""
    seen = (jsonstore.load(FLAGS_SEEN) or {}).get("ts")
    if seen is None:
        mark_flags_seen()
        return 0
    return sum(1 for t in _flag_times() if t > seen)


def mark_flags_seen():
    jsonstore.save(FLAGS_SEEN, {"ts": time.time()})


def audit_entries(limit=300, service="", decision="", origin="", since="", q=""):
    """newest first; origin is a group (session/job/host) or "job:<name>", since a key of SINCE (anything else: no limit), q a text search"""
    try:
        lines = open(AUDIT_FILE).read().splitlines()
    except OSError:
        return []
    cutoff = time.time() - SINCE[since] if since in SINCE else 0
    q = q.strip().lower()
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if cutoff and _epoch(e.get("ts")) < cutoff:
            break  # the file is in time order – everything further back is older
        if (service and e.get("service") != service) or (decision and e.get("decision") != decision):
            continue
        if q and not any(q in str(e.get(k, "")).lower() for k in ("action", "service", "detail", "error", "excerpts")):
            continue
        e["from_label"] = origin_label(e.get("from"))
        if origin and (e["from_label"] != f"Job · {origin[4:]}" if origin.startswith("job:") else origin_group(e.get("from")) != origin):
            continue
        e["from_link"] = origin_link(e.get("from"))
        if e.get("mode") in tclib.ACCESS_ALIASES:  # entries written before v0.16
            e["mode"] = tclib.ACCESS_ALIASES[e["mode"]]
        out.append(e)
        if len(out) >= limit:
            break
    return out
