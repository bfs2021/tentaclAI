"""Central management – this installation as an agent of a central server (tentaclAI Enterprise). Protocol: docs/central.md.

The agent connects to the server, never the other way round: every 30 s a heartbeat, the profile when it changed, the
audit entries not sent yet and – when it changed – the configuration (the export document, plain secrets left out).
A profile (signed by the server) says which items of which parts (backup.SECTIONS) are enforced or defaults; they are
applied through backup.preview/apply, enforced ones are locked (check_write, locks.json for svc) and reverted when
changed around tentacontrol. Without the server the last profile stays in force and audit entries wait.

State in DATA_DIR/central/ (root only): agent.key (Ed25519, PEM), state.json, profile.json (the signed profile as
received), locks.json (enforced item ids – svc reads it), ca.pem (optional: the server's CA).
"""
import base64
import contextlib
import hashlib
import json
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import jsonstore
from .config import DATA_DIR

DIR = os.path.join(DATA_DIR, "central")
KEY_FILE = os.path.join(DIR, "agent.key")
STATE = os.path.join(DIR, "state.json")
PROFILE = os.path.join(DIR, "profile.json")
LOCKS = os.path.join(DIR, "locks.json")
CA_FILE = os.path.join(DIR, "ca.pem")
API = "/api/agent/v1"
CODE_PREFIX = "tce1."
INTERVAL = 30
AUDIT_BATCH = 500
AUDIT_BACKLOG_DAYS = 7
TIMEOUT = 15
MODES = ("enforced", "default")

_local = threading.local()  # .applying: writes of the agent itself pass check_write
_lock = threading.RLock()   # one sync at a time


class CentralError(Exception):
    """a problem with the server or the profile – the message is for the admin"""


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def fingerprint(pub_b64):
    h = hashlib.sha256(b64d(pub_b64)).hexdigest()[:16].upper()
    return " ".join(h[i:i + 4] for i in range(0, 16, 4))


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def areas():
    """what a server may manage: the parts of the export, and modules"""
    from .backup import SECTIONS
    return dict(SECTIONS, modules="Modules")


# ---------------------------------------------------------------- state

def state():
    return jsonstore.load(STATE) or {}


def _save(st):
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    jsonstore.save(STATE, st, mode=0o600)


def _update(**kw):
    with _lock:
        st = state()
        st.update(kw)
        _save(st)
        return st


def enrolled():
    st = state()
    return bool(st.get("agent_id")) and not st.get("revoked")


def _key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        with open(KEY_FILE, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    except FileNotFoundError:
        pass
    priv = Ed25519PrivateKey.generate()
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with os.fdopen(os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(pem)
    return priv


def _public(priv):
    from cryptography.hazmat.primitives import serialization
    return b64e(priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


# ---------------------------------------------------------------- HTTP

def parse_code(code):
    """{url, token, key} from an enrollment code – CentralError if it isn't one"""
    code = "".join(str(code or "").split())
    if not code.startswith(CODE_PREFIX):
        raise CentralError("not an enrollment code (it starts with tce1.)")
    try:
        c = json.loads(b64d(code[len(CODE_PREFIX):]))
    except (ValueError, UnicodeDecodeError):
        raise CentralError("the enrollment code is damaged") from None
    if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c.get(k) for k in ("url", "token", "key")):
        raise CentralError("the enrollment code is incomplete")
    if not c["url"].startswith(("https://", "http://")):
        raise CentralError("the enrollment code has no server address")
    if c.get("ca") is not None and (not isinstance(c["ca"], str) or "BEGIN CERTIFICATE" not in c["ca"]):
        raise CentralError("the certificate in the enrollment code is not PEM")
    c["url"] = c["url"].rstrip("/")
    return c


def _ssl():
    ctx = ssl.create_default_context()
    if os.path.isfile(CA_FILE):
        ctx.load_verify_locations(CA_FILE)
    return ctx


def _http(method, url, body, headers):
    """(status, body bytes) – the transport; tests replace it"""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl() if url.startswith("https") else None) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _call(method, path, payload=None, signed=True, base=None, raw=False):
    st = state()
    base = base or st.get("url")
    body = canonical(payload) if payload is not None else b""
    headers = {"Content-Type": "application/json", "User-Agent": "tentacontrol-agent/1"}
    if signed:
        ts, nonce = str(int(time.time())), secrets.token_urlsafe(16)
        msg = "\n".join([method, API + path, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()
        headers.update({"X-TC-Agent": st["agent_id"], "X-TC-Time": ts, "X-TC-Nonce": nonce,
                        "X-TC-Signature": b64e(_key().sign(msg))})
    try:
        status, data = _http(method, base + API + path, body if method != "GET" else None, headers)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise CentralError(f"can't reach the server: {reason}") from None
    if status == 410:
        _update(revoked=True, last_error="the server revoked this installation")
        raise CentralError("the server revoked this installation")
    if raw and status == 200:
        return data
    try:
        answer = json.loads(data or b"{}")
    except ValueError:
        answer = {}
    if status != 200:
        raise CentralError(f"server: {answer.get('error') or answer.get('detail') or f'HTTP {status}'}")
    return answer


# ---------------------------------------------------------------- enrolling and leaving

def enroll(code, name, allowed, ca_pem=""):
    """join the server of an enrollment code; allowed = the parts the server may manage. Returns the state."""
    from . import instance
    from .envfile import read_env
    from .web import TC_VERSION
    if enrolled():
        raise CentralError("this installation is enrolled already – leave first")
    c = parse_code(code)
    allowed = sorted(a for a in allowed if a in areas())
    ca_pem = (ca_pem or "").strip() or c.get("ca", "").strip()  # pasted here before the one the server put into the code
    if ca_pem:
        if "BEGIN CERTIFICATE" not in ca_pem:
            raise CentralError("the CA certificate is not PEM")
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        with open(CA_FILE, "w") as f:
            f.write(ca_pem + "\n")
    elif os.path.exists(CA_FILE):
        os.remove(CA_FILE)  # left by an earlier attempt
    env = read_env()
    priv = _key()
    answer = _call("POST", "/enroll", {"token": c["token"], "public_key": _public(priv), "instance_id": instance.instance_id()[0],
                                       "hostname": env.get("TC_HOSTNAME") or socket.gethostname(), "version": TC_VERSION,
                                       "name": (name or env.get("TC_HOSTNAME") or socket.gethostname()).strip()[:60],
                                       "api": 1, "areas": allowed}, signed=False, base=c["url"])
    key = answer.get("central_key") or ""
    if not key or fingerprint(key) != c["key"]:
        raise CentralError("the server's key doesn't match the enrollment code – not enrolled")
    now = time.time()
    st = {"url": c["url"], "agent_id": answer["agent_id"], "central_key": key, "central_name": answer.get("central_name") or c["url"],
          "name": name, "areas": allowed, "enrolled": now, "profile_rev": 0, "applied": {},
          "audit_cursor": {"ts": now - AUDIT_BACKLOG_DAYS * 86400, "id": ""}, "config_hash": "", "last_ok": now, "last_error": ""}
    _save(st)
    _audit("enrolled", f"{st['central_name']} as {st['agent_id']} – may manage: {', '.join(allowed) or 'nothing'}")
    return st


def leave():
    """leave the server (told when it can be reached) – locks go, applied values stay"""
    st = state()
    if not st.get("agent_id"):
        raise CentralError("not enrolled")
    prof = _profile()
    if prof and prof.get("allow_leave") is False and not st.get("revoked"):
        raise CentralError("the server's profile doesn't allow leaving here – ask the server's admin to revoke this installation")
    if not st.get("revoked"):
        with contextlib.suppress(CentralError, KeyError):
            _call("POST", "/leave", {})
    for f in (STATE, PROFILE, LOCKS):
        with contextlib.suppress(FileNotFoundError):
            os.remove(f)
    _audit("left", st.get("central_name", ""))


# ---------------------------------------------------------------- locks

def locks():
    return set((jsonstore.load(LOCKS) or {}).get("enforced") or [])


def locked(section, key):
    return f"{section}:{key}" in locks()


@contextlib.contextmanager
def applying():
    _local.applying = True
    try:
        yield
    finally:
        _local.applying = False


def check_write(section, key):
    """ValueError when the server enforces this item – called by the stores before they write"""
    if getattr(_local, "applying", False) or not os.path.exists(LOCKS):
        return
    if locked(section, key):
        raise ValueError(f"{section} {key} is managed by {state().get('central_name') or 'the central server'} – change it there")


# ---------------------------------------------------------------- the profile

def _profile():
    p = jsonstore.load(PROFILE)
    return (p or {}).get("profile")


def _verify(doc, st):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        prof, sig = doc["profile"], b64d(doc["sig"])
        Ed25519PublicKey.from_public_bytes(b64d(st["central_key"])).verify(sig, canonical(prof))
    except (KeyError, TypeError, ValueError, InvalidSignature):
        raise CentralError("the profile's signature doesn't match the server's key – not applied") from None
    if prof.get("agent_id") != st["agent_id"]:
        raise CentralError("the profile is for another agent – not applied")
    return prof


def _h(v):
    return hashlib.sha256(canonical(v)).hexdigest()[:16]


def reconcile(prof=None):
    """apply a profile (default: the stored one) – returns the report {rev, results, drift, missing, refused, touched,
    restart}; touched: parts written (services … need a sync), restart: settings that need a restart"""
    from . import backup
    from .envfile import read_env
    st = state()
    prof = prof if prof is not None else _profile()
    if not prof:
        return {"rev": 0, "results": [], "drift": [], "missing": [], "refused": [], "touched": [], "restart": []}
    allowed = set(st.get("areas") or [])
    sections = {s: v for s, v in (prof.get("sections") or {}).items() if s in backup.SECTIONS}
    refused = sorted(s for s in sections if s not in allowed) + (["modules"] if prof.get("modules") and "modules" not in allowed else [])
    sections = {s: v for s, v in sections.items() if s in allowed}
    modes = {k: v for k, v in (prof.get("modes") or {}).items() if v in MODES}
    doc = dict({"format": backup.FORMAT, "version": backup.VERSION, "variables": []}, **sections)
    rows = backup.preview(doc) if sections else {}
    applied = dict(st.get("applied") or {})
    results, drift, pick = [], [], set()
    managed_ids = set()
    for sec, rs in rows.items():
        for r in rs:
            rid, mode = r["id"], modes.get(r["id"], "enforced")
            managed_ids.add(rid)
            if r["status"] == "invalid":
                results.append({"id": rid, "result": "invalid", "msg": r["error"]})
            elif r["status"] == "same":
                applied[rid] = r["hash_new"]
            elif mode == "enforced":
                pick.add(rid)
                if rid in applied:
                    drift.append({"id": rid, "kind": "reverted"})
            elif r["status"] == "new" and rid not in applied:
                pick.add(rid)
            elif r["status"] == "changed" and applied.get(rid) == r["hash_old"]:
                pick.add(rid)  # still the previous profile value: the new default replaces it
            else:
                drift.append({"id": rid, "kind": "overridden" if r["status"] == "changed" else "removed locally"})
    restart, touched = [], set()
    if pick:
        old_env = read_env()
        with applying():
            res, _ = backup.apply(doc, pick)
        hashes = {r["id"]: r["hash_new"] for rs in rows.values() for r in rs}
        for r in res:
            if r["ok"]:
                applied[r["id"]] = hashes.get(r["id"], "")
                touched.add(r["id"].split(":", 1)[0])
            results.append({"id": r["id"], "result": "applied" if r["ok"] else "failed", "msg": r["msg"]})
        from .config import RESTART_VARS
        changed = {r["id"].split(":", 1)[1] for r in res if r["ok"] and r["id"].startswith("settings:")}
        restart = sorted(RESTART_VARS & changed)
        if restart:
            from .web import mark_restart
            mark_restart(old_env, set(restart))
    drift += _local_items(prof, sections, managed_ids)
    results += _modules(prof, allowed, applied, drift)
    applied = {k: v for k, v in applied.items() if k in managed_ids or k.startswith("modules:")}
    enforced = sorted(rid for rid in managed_ids if modes.get(rid, "enforced") == "enforced")
    enforced += sorted(f"modules:{n}" for n, m in (prof.get("modules") or {}).items()
                       if "modules" in allowed and (m or {}).get("mode", "enforced") == "enforced")
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    jsonstore.save(LOCKS, {"central": st.get("central_name", ""), "enforced": enforced}, mode=0o644)
    missing = backup.missing(doc) if sections else []
    _update(applied=applied, applied_rev=prof.get("rev", 0), drift=drift, missing=missing, refused=refused)
    for r in results:
        if r["result"] in ("applied", "failed"):
            _audit(f"profile {r['result']}: {r['id']}", r["msg"], decision="ok" if r["result"] == "applied" else "failed")
    for d in drift:
        if d["kind"] == "reverted":
            _audit(f"local change reverted: {d['id']}", "enforced by the central profile", decision="denied")
    return {"rev": prof.get("rev", 0), "results": results, "drift": drift, "missing": missing, "refused": refused,
            "touched": sorted(touched), "restart": restart}


def _local_items(prof, sections, managed_ids):
    """local items in parts where the profile allows none – reported, never deleted"""
    from . import backup
    out = []
    flags = prof.get("local_items") or {}
    current, _ = backup.export([s for s in sections if flags.get(s) is False])
    for s in (s for s in sections if flags.get(s) is False):
        data = current.get(s)
        keys = []
        if isinstance(data, dict) and s == "network":
            keys = [f"{kind}:{e}" for kind, entries in data.items() if isinstance(entries, list) for e in entries]
        elif isinstance(data, dict):
            keys = list(data)
        elif isinstance(data, list):
            keys = [h.get("alias") for h in data if isinstance(h, dict)]
        out += [{"id": f"{s}:{k}", "kind": "local item"} for k in keys if f"{s}:{k}" not in managed_ids]
    return out


def _modules(prof, allowed, applied, drift):
    """built-in modules on/off and packages from the server (installed only when signed by tentaclAI)"""
    from . import extensions, packages
    mods = prof.get("modules") or {}
    if not mods or "modules" not in allowed:
        return []
    out = []
    for name, m in mods.items():
        m = m or {}
        rid = f"modules:{name}"
        try:
            pkg = m.get("package")
            if pkg:
                have = packages.installed_versions().get(name)
                if have != pkg.get("version"):
                    data = _call("GET", f"/packages/{name}/{pkg['version']}", raw=True)
                    if pkg.get("sha256") and hashlib.sha256(data).hexdigest() != pkg["sha256"]:
                        raise CentralError("the package from the server doesn't match the profile's checksum")
                    info = packages.install(data)  # signed by tentaclAI only (allow_unsigned is False)
                    out.append({"id": rid, "result": "applied", "msg": f"package {info['version']} installed – restart tentacontrol"})
            want = m.get("enabled")
            if want is not None and extensions.origin(name) not in ("configured", "signed"):
                on = name in extensions.enabled()
                if on != bool(want) and (m.get("mode", "enforced") == "enforced" or rid not in applied):
                    with applying():
                        extensions.set_enabled(name, bool(want))
                    out.append({"id": rid, "result": "applied", "msg": "enabled" if want else "disabled"})
                elif on != bool(want):
                    drift.append({"id": rid, "kind": "overridden"})
            applied[rid] = _h(m)
        except (CentralError, packages.PackageError, ValueError, KeyError) as e:
            out.append({"id": rid, "result": "failed", "msg": str(e)})
    return out


# ---------------------------------------------------------------- audit shipping

def _unsent(limit=AUDIT_BATCH):
    """(entries oldest first, more waiting) after the cursor"""
    import tclib
    cur = state().get("audit_cursor") or {}
    since, after = float(cur.get("ts") or 0), cur.get("id") or ""
    newer = []
    for line in tclib.audit_lines(DATA_DIR, since - 1):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if after and e.get("id") == after:
            break
        t = tclib.audit_epoch(e.get("ts"))
        if t and t < since:
            break
        newer.append(e)
    newer.reverse()
    return newer[:limit], len(newer) > limit


def ship_audit():
    """send what is waiting, in batches – returns the number sent"""
    import tclib
    sent = 0
    for _ in range(20):  # at most 10 000 per sync
        batch, more = _unsent()
        if not batch:
            break
        _call("POST", "/audit", {"entries": batch})
        last = batch[-1]
        _update(audit_cursor={"ts": tclib.audit_epoch(last.get("ts")) or time.time(), "id": last.get("id", "")})
        sent += len(batch)
        if not more:
            break
    return sent


def retention_floor():
    """days the local audit retention must at least keep, so nothing unsent is pruned (0 = no constraint)"""
    if not enrolled():
        return 0
    ts = float((state().get("audit_cursor") or {}).get("ts") or 0)
    return int((time.time() - ts) // 86400) + 2 if ts else 0


# ---------------------------------------------------------------- one sync

def _status(st):
    from . import extensions, instance, services_store
    from .envfile import read_env
    from .web import TC_VERSION
    env = read_env()
    _, waiting = _unsent(1)
    return {"name": st.get("name") or env.get("TC_HOSTNAME", ""), "hostname": env.get("TC_HOSTNAME") or socket.gethostname(),
            "instance_id": instance.instance_id()[0], "version": TC_VERSION, "edition": extensions.edition(),
            "modules": [{"name": e["name"], "origin": e["origin"], "version": e.get("version", ""), "active": e["active"]}
                        for e in extensions.available()],
            "services": [{"name": s["name"], "parts": s["kinds"], "state": s["state"], "enabled": s["enabled"]}
                         for s in services_store.load_all()],
            "audit": {"sent_through": (st.get("audit_cursor") or {}).get("id", ""), "waiting": bool(waiting)},
            "profile": {"rev": st.get("applied_rev", 0), "drift": st.get("drift") or [], "missing": st.get("missing") or [],
                        "refused": st.get("refused") or []},
            "areas": st.get("areas") or []}


def sync():
    """heartbeat, profile, audit, config – one round; returns the report of the profile run (or None).
    CentralError when the server can't be reached (the state keeps the error)."""
    from . import backup
    with _lock:
        st = state()
        if not st.get("agent_id") or st.get("revoked"):
            return None
        try:
            hb = _call("POST", "/heartbeat", _status(st))
            report = None
            if hb.get("profile_rev") != st.get("profile_rev"):
                doc = _call("GET", "/profile")
                prof = _verify(doc, st)
                jsonstore.save(PROFILE, doc, mode=0o600)
                _update(profile_rev=prof.get("rev", 0))
                _audit("profile received", f"{prof.get('name', '')} rev {prof.get('rev')}")
            report = reconcile()
            key = _h({k: report[k] for k in ("rev", "results", "drift", "missing", "refused")})
            if key != state().get("report_hash"):
                _call("POST", "/report", {k: report[k] for k in ("rev", "results", "drift", "missing", "refused")})
                _update(report_hash=key)
            ship_audit()
            cfg, _ = backup.export([s for s in backup.SECTIONS])
            cfg.pop("created", None)
            ch = _h(cfg)
            if ch != state().get("config_hash") or hb.get("want_config"):
                _call("POST", "/config", {"config": cfg, "hash": ch})
                _update(config_hash=ch)
            _update(last_ok=time.time(), last_error="")
            return report
        except CentralError as e:
            _update(last_error=str(e), last_try=time.time())
            raise


async def loop():
    """the background sync – started with tentacontrol; quiet while not enrolled"""
    import asyncio
    from .web import sync_services
    await asyncio.sleep(10)
    while True:
        if enrolled():
            try:
                report = await asyncio.to_thread(sync)
                if report and set(report["touched"]) & {"ssh_hosts", "systems", "services", "instructions", "skills"}:
                    await sync_services()
            except CentralError:
                pass
            except Exception as e:  # noqa: BLE001 – never end the loop
                print(f"central: sync failed: {e}", flush=True)
                _update(last_error=f"internal error: {e}")
        await asyncio.sleep(INTERVAL)


def _audit(action, detail="", decision="ok"):
    from . import auditlog
    auditlog.write("admin", "central", f"central: {action}", decision, actor="system", detail=detail[:300])


def view():
    """for Settings → Central management"""
    st = state()
    prof = _profile() or {}
    last = st.get("last_ok")
    return {"enrolled": bool(st.get("agent_id")), "revoked": bool(st.get("revoked")), "st": st, "profile": prof,
            "locks": sorted(locks()), "areas": areas(), "ca": os.path.isfile(CA_FILE),
            "last_ok": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)) if last else ""}
