"""The audit log as tentacontrol sees it: writing its own entries (user actions, sign-ins, network, jobs, system),
reading for Logs → Audit (with origin labels and links), export, the ⚠ badge for flagged entries, retention.

Format, files, rotation and search are tclib's (`tclib.audit_*`) – the same code svc uses in the sandbox.
"""
import asyncio
import os
import time

import tclib

from . import jsonstore
from .config import DATA_DIR
from .envfile import read_env

FLAGS_SEEN = os.path.join(DATA_DIR, "flags-seen.json")
SINCE = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}  # the time filter of Logs → Audit
PAGE = 200


def write(category, service, action, decision="ok", actor="", part="", mode="", origin=None, **extra):
    """one entry from tentacontrol – never raises (the audit must not break what it records)"""
    try:
        tclib.audit_write(DATA_DIR, tclib.audit_entry(category, service, action, decision, actor=actor, part=part, mode=mode,
                                                      origin=origin, **extra))
    except Exception as e:  # noqa: BLE001
        print(f"audit log not writable: {e}", flush=True)


def user(request, category, service, action, decision="ok", user=None, **extra):
    """an action of the signed-in user (or a sign-in attempt – then `user` is the name that was typed): actor user,
    with the username and the client's address"""
    if user is None:
        from .auth import username
        try:
            user = username(request)
        except AssertionError:  # no session (yet)
            user = ""
    write(category, service, action, decision, actor="user", user=user, ip=request.client.host if request.client else "", **extra)


def _enrich(e):
    from .activity import origin_label, origin_link
    o = tclib.audit_origin(e)
    e["from_label"] = origin_label(o) or ((f"{e['user']} · tentacontrol" if e.get("user") else "tentacontrol")
                                          if e["actor"] == "user" else "")
    e["from_link"] = origin_link(o)
    return e


def _origin_match(origin):
    """the From filter: session / job / host, or job:<name>"""
    from .activity import origin_group, origin_label
    if not origin:
        return None
    if origin.startswith("job:"):
        return lambda e: origin_label(tclib.audit_origin(e)) == f"Job · {origin[4:]}"
    return lambda e: origin_group(tclib.audit_origin(e)) == origin


def window(since="", start="", end=""):
    """(since, until) in epoch seconds from the filter: a SINCE key, or own dates (YYYY-MM-DD, end inclusive)"""
    def day(s, add=0):
        try:
            return time.mktime(time.strptime(s, "%Y-%m-%d")) + add
        except ValueError:
            return 0
    if start or end:
        return day(start), day(end, 86400) if end else 0
    return (time.time() - SINCE[since], 0) if since in SINCE else (0, 0)


def entries(limit=None, since="24h", start="", end="", q="", after="", origin="", **filters):
    """(entries, more) newest first for the page – filters: category, actor, service, decision"""
    t0, t1 = window(since, start, end)
    found, more = tclib.audit_read(DATA_DIR, limit=limit or PAGE, since=t0, until=t1, filters=filters, q=q, after=after,
                                   match=_origin_match(origin))
    return [_enrich(e) for e in found], more


def export(fmt, since="24h", start="", end="", q="", origin="", **filters):
    """every matching entry as export lines (tclib.audit_export) – no page limit"""
    t0, t1 = window(since, start, end)
    found, _ = tclib.audit_read(DATA_DIR, limit=500_000, since=t0, until=t1, filters=filters, q=q, match=_origin_match(origin))
    return tclib.audit_export(found, fmt)


def known(limit=3000):
    """services and job names of recent entries – the choices of the filters"""
    found, _ = tclib.audit_read(DATA_DIR, limit=limit, since=time.time() - SINCE["30d"])
    from .activity import origin_label
    jobs = {origin_label(tclib.audit_origin(e))[6:] for e in found if e.get("origin_job")}
    return sorted({e.get("service", "") for e in found} - {""}), sorted(jobs - {""})


# ---------------------------------------------------------------- flagged entries: the ⚠ badge next to Logs

_flags = {"ino": None, "pos": 0, "times": []}


def _flag_times():
    """when each flagged entry was written – the sidebar asks every 5 s, so only what was appended since is read"""
    path = os.path.join(DATA_DIR, tclib.AUDIT_FILE)
    try:
        st = os.stat(path)
    except OSError:
        return _flags["times"]
    if _flags["ino"] != st.st_ino or st.st_size < _flags["pos"]:
        _flags.update(ino=st.st_ino, pos=0)  # rotated: the old file's flags stay counted
    if st.st_size > _flags["pos"]:
        import json
        with open(path, "rb") as f:
            f.seek(_flags["pos"])
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1  # a line still being written is read next time
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            if '"flagged"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("decision") == "flagged":
                _flags["times"].append(tclib.audit_epoch(e.get("ts")))
        _flags["pos"] += end
        _flags["times"] = _flags["times"][-1000:]
    return _flags["times"]


def unseen_flags():
    """possible prompt injections flagged since Logs → Audit was last opened (the ⚠ badge, the Overview);
    the first call starts counting from now, so older entries don't light it up after an update"""
    seen = (jsonstore.load(FLAGS_SEEN) or {}).get("ts")
    if seen is None:
        mark_flags_seen()
        return 0
    return sum(1 for t in _flag_times() if t > seen)


def mark_flags_seen():
    jsonstore.save(FLAGS_SEEN, {"ts": time.time()})


# ---------------------------------------------------------------- retention

def prune():
    n = tclib.audit_prune(DATA_DIR, tclib.audit_retention(read_env()))
    if n:
        print(f"audit log: {n} archive file(s) older than the retention removed", flush=True)


async def prune_loop():
    while True:
        try:
            await asyncio.to_thread(prune)
        except Exception as e:  # noqa: BLE001
            print(f"audit prune: {e}", flush=True)
        await asyncio.sleep(6 * 3600)
