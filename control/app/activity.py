"""Activity: pending approvals (access mode "confirm") and the audit log written by svc.

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

from . import dockerops, notify, usage
from .config import DATA_DIR

TOKEN_FILE = os.path.join(DATA_DIR, "svc-token")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.jsonl")
TIMEOUT = 600
GRANT_SECONDS = 15 * 60
NO_GRANT_PARTS = ("config",)  # service files / site rules: every write is approved on its own

_ids = itertools.count(1)
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


def _expire():
    now = time.time()
    for a in APPROVALS.values():
        if a["status"] == "pending" and now - a["created"] > TIMEOUT:
            a.update(status="expired", decided=now)
            a["event"].set()
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


def create(service, part, action, origin=None):
    """new request; approved at once while a grant for this service + part is active; one pending request per network target"""
    _expire()
    if part == "network":
        same = next((a for a in APPROVALS.values() if a["status"] == "pending" and a["service"] == service and a["part"] == part), None)
        if same:
            return same["id"]
    rid = str(next(_ids))
    a = {"id": rid, "service": str(service)[:60], "part": str(part)[:20], "action": str(action)[:500],
         "created": time.time(), "status": "pending", "by": "", "event": asyncio.Event(), "origin": clean_origin(origin)}
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
    return {"status": a["status"], "by": a["by"]}


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
        if q and not any(q in str(e.get(k, "")).lower() for k in ("action", "service", "detail", "error")):
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
