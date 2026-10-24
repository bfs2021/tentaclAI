"""The audit log as tentacontrol sees it: writing its own entries (user actions, sign-ins, network, jobs, system),
reading for Logs → Audit (with origin labels and links), export, the ⚠ badge for flagged entries, retention.

Format, files, rotation and search are tclib's (`tclib.audit_*`) – the same code svc uses in the sandbox.
"""
import asyncio
import json
import os
import time

import tclib

from . import jsonstore
from .config import DATA_DIR
from .envfile import read_env

FLAGS_SEEN = os.path.join(DATA_DIR, "flags-seen.json")
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


RANGES = {"15m": 900, "1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}  # the time picker of Logs → Audit
SCAN = 20_000  # entries read for the facets and the histogram


def _sources():
    """source(e) → readable source ("Claude terminal · “title”", "Job · backup", the user's address …) and link(e) –
    remembered per origin for one request (the labels read the session and job files)"""
    from .activity import origin_label, origin_link
    seen = {}

    def label(e):
        o = tclib.audit_origin(e)
        if not o:
            return e.get("ip", "")
        key = tuple(sorted(o.items()))
        if key not in seen:
            seen[key] = (origin_label(o) or tclib.audit_source(e), origin_link(o))
        return seen[key][0]

    def link(e):
        o = tclib.audit_origin(e)
        return seen.get(tuple(sorted(o.items())), ("", ""))[1] if o else ""
    return label, link


def _when(s, end=False):
    """epoch of "YYYY-MM-DDTHH:MM" (or a date: its start, its end with end=True); 0 if empty or unreadable"""
    for fmt, add in (("%Y-%m-%dT%H:%M", 60 if end else 0), ("%Y-%m-%d", 86400 if end else 0)):
        try:
            return time.mktime(time.strptime(s, fmt)) + add
        except ValueError:
            continue
    return 0


def window(rng="24h", start="", end=""):
    """(since, until) in epoch seconds: a RANGES key, "all", or custom start/end (local time, end inclusive)"""
    if rng == "custom" or start or end:
        return _when(start), _when(end, end=True)
    return (time.time() - RANGES[rng], 0) if rng in RANGES else (0, 0)


def _viewed(entries, label, link):
    for e in entries:
        if "_view" not in e:
            e["_view"] = tclib.audit_view(e, label)
        e["_link"] = link(e)
        e["_raw"] = json.dumps({k: v for k, v in e.items() if not k.startswith("_")}, ensure_ascii=False, indent=1)
    return entries


def search(q="", rng="24h", start="", end="", after="", limit=None):
    """(entries, more) newest first for the table; each entry carries _view (tclib.audit_view), _link and _raw"""
    t0, t1 = window(rng, start, end)
    label, link = _sources()
    found, more = tclib.audit_read(DATA_DIR, limit=limit or PAGE, since=t0, until=t1, query=q, source=label, after=after)
    return _viewed(found, label, link), more


def overview(q="", rng="24h", start="", end=""):
    """the page in one pass over up to SCAN entries of the time range → (entries, more, overview): the first PAGE
    matches for the table (as search), and facets, histogram and the number of matches"""
    t0, t1 = window(rng, start, end)
    label, link = _sources()
    terms = tclib.audit_query(q)
    scan, capped = tclib.audit_read(DATA_DIR, limit=SCAN, since=t0, until=t1)
    for e in scan:
        e["_view"] = tclib.audit_view(e, label)
    hits = [e for e in scan if not tclib.audit_fails(e, e["_view"], terms)]
    now = time.time()
    lo = tclib.audit_epoch(scan[-1]["ts"]) if scan and (capped or not t0) else (t0 or now - 3600)
    hi = min(t1, now) if t1 else now
    width = tclib.audit_bucket(max(60, hi - lo))
    ov = {"facets": tclib.audit_facets(scan, terms), "total": len(hits), "capped": capped, "width": width,
          "bars": tclib.audit_histogram(hits, lo, hi, width)}
    return _viewed(hits[:PAGE], label, link), len(hits) > PAGE or capped, ov


APPROVALS_QUERY = "mode:confirm"  # requests that needed an approval: confirm tentacles and unknown network destinations


def approvals(limit=30, days=30):
    """the latest decided requests (approved, denied, expired …) for the Approvals page – newest first"""
    label, link = _sources()
    found, more = tclib.audit_read(DATA_DIR, limit=limit, since=time.time() - days * 86400, query=APPROVALS_QUERY, source=label)
    return _viewed(found, label, link), more


def export(fmt, q="", rng="24h", start="", end=""):
    """every matching entry as export lines (tclib.audit_export) – no page limit"""
    t0, t1 = window(rng, start, end)
    label, _ = _sources()
    found, _ = tclib.audit_read(DATA_DIR, limit=500_000, since=t0, until=t1, query=q, source=label)
    for e in found:
        e.setdefault("_view", tclib.audit_view(e, label))
    return tclib.audit_export(found, fmt)


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
    from . import central
    days = max(tclib.audit_retention(read_env()), central.retention_floor())  # keep what the central server lacks
    n = tclib.audit_prune(DATA_DIR, days)
    if n:
        print(f"audit log: {n} archive file(s) older than the retention removed", flush=True)


class Follower:
    """new entries of the active file since the last poll – starts at its end; after a rotation the rest of the moved
    file comes first. Only whole lines; a broken line is skipped."""

    def __init__(self, data_dir=None):
        self.dir = data_dir or DATA_DIR
        self.path = os.path.join(self.dir, tclib.AUDIT_FILE)
        try:
            st = os.stat(self.path)
            self.ino, self.pos = st.st_ino, st.st_size
        except OSError:
            self.ino, self.pos = None, 0

    def _read(self, path, pos):
        """(entries, new position) from pos to the last complete line"""
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                data = f.read()
        except OSError:
            return [], pos
        end = data.rfind(b"\n") + 1
        out = []
        for line in data[:end].splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
        return out, pos + end

    def _moved(self):
        """the archive the active file was rotated into (same inode)"""
        arch = os.path.join(self.dir, tclib.AUDIT_ARCHIVE)
        try:
            names = sorted(os.listdir(arch), reverse=True)[:5]
        except OSError:
            return None
        for n in names:
            p = os.path.join(arch, n)
            try:
                if os.stat(p).st_ino == self.ino:
                    return p
            except OSError:
                pass
        return None

    def poll(self):
        out = []
        try:
            st = os.stat(self.path)
        except OSError:
            return out
        if self.ino is not None and st.st_ino != self.ino:
            moved = self._moved()
            if moved:
                out, _ = self._read(moved, self.pos)
            self.ino, self.pos = st.st_ino, 0
        elif self.ino is None:
            self.ino, self.pos = st.st_ino, 0
        if st.st_size < self.pos:  # truncated
            self.pos = 0
        new, self.pos = self._read(self.path, self.pos)
        return out + new


async def follow_loop(hooks, every=2):
    """hand every new entry to the extensions' on_audit hooks (hooks() → [(fn, name)], asked each round, so a disabled
    extension drops out at once) – a failing hook is logged and doesn't stop the others"""
    def deliver(entries):
        for fn, name in hooks():
            for e in entries:
                try:
                    fn(dict(e))
                except Exception as err:  # noqa: BLE001
                    print(f"extension {name}: on_audit: {err}", flush=True)
    f = Follower()
    while True:
        try:
            entries = await asyncio.to_thread(f.poll)
            if entries:
                await asyncio.to_thread(deliver, entries)
        except Exception as e:  # noqa: BLE001
            print(f"audit follower: {e}", flush=True)
        await asyncio.sleep(every)


async def prune_loop():
    while True:
        try:
            await asyncio.to_thread(prune)
        except Exception as e:  # noqa: BLE001
            print(f"audit prune: {e}", flush=True)
        await asyncio.sleep(6 * 3600)
