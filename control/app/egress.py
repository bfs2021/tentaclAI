"""Network rules of the egress gateway: own allow/block lists (data/control/egress-rules.json), the shipped default
list and the tentacles (derived live from services and SSH hosts – never stored, so they can't go stale)."""
import json
import os
import re

import tclib

from . import activity, jsonstore, sshhosts
from .config import DATA_DIR, SERVICES_DIR, STACK_DIR
from .envfile import read_env

RULES = os.path.join(DATA_DIR, "egress-rules.json")
CLEARED = os.path.join(DATA_DIR, "egress-cleared")  # time of the last "Clear" – the gateway empties its list after it
SEEN = "/egress/egress-seen.json"
DEFAULT_FILE = "/app/egress-default.txt"
ENTRY_RE = re.compile(r"^((\*\.)?[a-z0-9.-]{1,253}|\d{1,3}(\.\d{1,3}){3}/\d{1,2})(:\d{1,5})?$")


def default_list():
    try:
        return tclib.egress_patterns(open(DEFAULT_FILE).read())
    except OSError:
        return []


def rules():
    r = jsonstore.load(RULES)
    return {"allow": r.get("allow", []), "block": r.get("block", [])}


def _save(r):
    jsonstore.save(RULES, r)


def normalize(entry):
    e = (entry or "").strip().lower().rstrip(".")
    if not ENTRY_RE.match(e):
        raise ValueError("host, *.domain, IP or CIDR (10.0.0.0/8), optionally with :port")
    return e


def add(kind, entry):
    _central_check("network", f"{kind}:{normalize(entry)}")
    e = normalize(entry)
    if kind == "block":
        host, _, port = e.rpartition(":") if ":" in e else (e, "", "443")
        if any(tclib.egress_match(p, host.lstrip("*."), int(port or 443)) for p in default_list()):
            raise ValueError(f"{e} is on the built-in list (Claude Code itself) and can't be blocked")
    r = rules()
    other = "block" if kind == "allow" else "allow"
    host = e.rsplit(":", 1)[0] if ":" in e else e
    r[other] = [x for x in r[other] if x not in (e, host)]  # an entry lives in one list only (host:port or the plain host)
    if e not in r[kind]:
        r[kind].append(e)
    _save(r)
    return e


def remove(kind, entry):
    _central_check("network", f"{kind}:{entry}")
    r = rules()
    r[kind] = [x for x in r[kind] if x != entry]
    _save(r)


def still_blocked(entry):
    """a wildcard on the blocklist that keeps blocking this host:port after it was allowed, else ''"""
    host, _, port = entry.rpartition(":") if ":" in entry else (entry, "", "443")
    return next((p for p in rules()["block"] if tclib.egress_match(p, host, int(port or 443))), "")


def direct_ip():
    """what enforce does with connections to a bare IP address that no rule covers: block (default) or ask"""
    v = (read_env().get("EGRESS_DIRECT_IP") or "block").strip().lower()
    return v if v in ("ask", "block") else "block"


def cleared():
    try:
        return float(open(CLEARED).read().strip())
    except (OSError, ValueError):
        return 0.0


def clear_seen():
    import time
    with open(CLEARED, "w") as f:
        f.write(str(time.time()))


def mode():
    m = (read_env().get("EGRESS_MODE") or "permissive").strip().lower()
    return m if m in ("permissive", "enforce") else "permissive"


def tentacles():
    """{(host, port|None): name} of all active services and SSH hosts"""
    return tclib.reach_hosts(tclib.load_services(SERVICES_DIR, tclib.load_systems(STACK_DIR)), read_env(), sshhosts.hosts())


def policy():
    """what the gateway needs – no secrets"""
    r = rules()
    grants = {g["service"]: g["until"] for g in activity.grants() if g["part"] == "network"}
    return {"mode": mode(), "direct_ip": direct_ip(), "cleared": cleared(), "allow": r["allow"], "block": r["block"], "grants": grants,
            "tentacles": [[h, p, n] for (h, p), n in tentacles().items()]}


def origin(host, port, tent=None, r=None):
    """(kind, label): default (built-in, never blocked) / blocked / tentacle / allow / unknown"""
    tent = tentacles() if tent is None else tent
    r = rules() if r is None else r
    return tclib.egress_rule(host, port, (), default_list(), r["block"], r["allow"], tent)


def seen_rows():
    try:
        seen = json.load(open(SEEN))
    except (OSError, ValueError):
        seen = {}
    tent, r, since = tentacles(), rules(), cleared()
    rows = []
    for key, e in seen.items():
        if e.get("last", 0) <= since:
            continue
        host, _, port = key.rpartition(":")
        kind, label = origin(host, int(port) if port.isdigit() else 0, tent, r)
        rows.append(dict(e, host=host, port=port, kind=kind, label=label))
    return sorted(rows, key=lambda x: -x.get("last", 0))


def _central_check(section, key):
    """refuse a change the central server enforces (central management)"""
    from .central import check_write
    check_write(section, key)
