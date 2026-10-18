"""Activity: pending approvals (access mode "confirm") and the audit log written by svc.

svc talks to the /internal/* endpoints with the token in data/control/svc-token; approvals live in memory –
after a restart of tentacontrol, waiting requests end as "error" and svc does not execute them (fail closed).
"""
import asyncio
import itertools
import json
import os
import secrets
import time

from .config import DATA_DIR

TOKEN_FILE = os.path.join(DATA_DIR, "svc-token")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.jsonl")
TIMEOUT = 600

_ids = itertools.count(1)
APPROVALS = {}  # id -> dict


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
    for k in [k for k, a in APPROVALS.items() if a["status"] != "pending" and now - a.get("decided", now) > 3600]:
        APPROVALS.pop(k, None)


def create(service, part, action):
    _expire()
    rid = str(next(_ids))
    APPROVALS[rid] = {"id": rid, "service": str(service)[:60], "part": str(part)[:20], "action": str(action)[:500],
                      "created": time.time(), "status": "pending", "by": "", "event": asyncio.Event()}
    return rid


def decide(rid, approve, who="tentacontrol"):
    a = APPROVALS.get(rid)
    if not a or a["status"] != "pending":
        return False
    a.update(status="approved" if approve else "denied", decided=time.time(), by=who)
    a["event"].set()
    return True


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


def audit_entries(limit=300, service="", decision=""):
    try:
        lines = open(AUDIT_FILE).read().splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if (service and e.get("service") != service) or (decision and e.get("decision") != decision):
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
