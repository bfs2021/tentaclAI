"""Network rules of the egress gateway: own allow/block lists (data/control/egress-rules.json), the shipped default
list and the tentacles (derived live from services and SSH hosts – never stored, so they can't go stale)."""
import json
import os
import re

import tclib

from . import activity, sshhosts
from .config import DATA_DIR, SERVICES_DIR, STACK_DIR
from .envfile import read_env

RULES = os.path.join(DATA_DIR, "egress-rules.json")
SEEN = "/egress/egress-seen.json"
DEFAULT_FILE = "/app/egress-default.txt"
ENTRY_RE = re.compile(r"^(\*\.)?[a-z0-9.-]{1,253}(:\d{1,5})?$")


def default_list():
    try:
        return tclib.egress_patterns(open(DEFAULT_FILE).read())
    except OSError:
        return []


def rules():
    try:
        r = json.load(open(RULES))
    except (OSError, ValueError):
        r = {}
    return {"allow": r.get("allow", []), "block": r.get("block", [])}


def _save(r):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RULES + ".tmp", "w") as f:
        json.dump(r, f, indent=1)
    os.replace(RULES + ".tmp", RULES)


def normalize(entry):
    e = (entry or "").strip().lower().rstrip(".")
    if not ENTRY_RE.match(e):
        raise ValueError("host, *.domain, host:port or *.domain:port")
    return e


def add(kind, entry):
    e = normalize(entry)
    r = rules()
    other = "block" if kind == "allow" else "allow"
    r[other] = [x for x in r[other] if x != e]  # an entry lives in one list only
    if e not in r[kind]:
        r[kind].append(e)
    _save(r)
    return e


def remove(kind, entry):
    r = rules()
    r[kind] = [x for x in r[kind] if x != entry]
    _save(r)


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
    return {"mode": mode(), "allow": r["allow"], "block": r["block"], "grants": grants,
            "tentacles": [[h, p, n] for (h, p), n in tentacles().items()]}


def origin(host, port, tent=None, r=None):
    """(kind, label): blocked / tentacle / default / allow / unknown"""
    tent = tentacles() if tent is None else tent
    r = rules() if r is None else r
    if any(tclib.egress_match(p, host, port) for p in r["block"]):
        return "blocked", "blocklist"
    name = tent.get((host, port)) or tent.get((host, None))
    if name:
        return "tentacle", name
    if any(tclib.egress_match(p, host, port) for p in default_list()):
        return "default", "built-in"
    if any(tclib.egress_match(p, host, port) for p in r["allow"]):
        return "allow", "own allowlist"
    return "unknown", ""


def seen_rows():
    try:
        seen = json.load(open(SEEN))
    except (OSError, ValueError):
        seen = {}
    tent, r = tentacles(), rules()
    rows = []
    for key, e in seen.items():
        host, _, port = key.rpartition(":")
        kind, label = origin(host, int(port) if port.isdigit() else 0, tent, r)
        rows.append(dict(e, host=host, port=port, kind=kind, label=label))
    return sorted(rows, key=lambda x: -x.get("last", 0))
