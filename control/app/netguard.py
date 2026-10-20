"""Who may talk to tentacontrol from the sandbox network.

tentacontrol sits in the sandbox network because svc (in the sandbox) asks it for approvals – which also put the admin
UI, the login and the gateway endpoints within Claude's reach. Requests are classified by their source address:
  sandbox container → only the svc endpoints (/internal/approvals…, /internal/event; they also need the svc token)
  egress gateway    → only /internal/egress/*
  anyone else       → everything except /internal/egress/* (UI and login still need the password)
The containers are found by name in Docker's DNS, so a recreated container with a new address is recognised.
"""
import socket
import time

from .config import CLAUDE_CONTAINER

EGRESS_NAME = "egress"  # network alias of the gateway in the sandbox network
SANDBOX_PATHS = ("/internal/approvals", "/internal/event")
_cache = {}  # name -> (ts, set of addresses)


def _resolve(name):
    ts, ips = _cache.get(name, (0.0, set()))
    if time.time() - ts < 5:
        return ips
    try:
        ips = {i[4][0] for i in socket.getaddrinfo(name, None)}
    except OSError:
        ips = set()
    _cache[name] = (time.time(), ips)
    return ips


def source(ip):
    """'sandbox', 'egress' or 'other'"""
    if ip and ip in _resolve(CLAUDE_CONTAINER):
        return "sandbox"
    if ip and ip in _resolve(EGRESS_NAME):
        return "egress"
    return "other"


def allowed(path, ip):
    kind = source(ip)
    if path.startswith("/internal/egress/"):
        return kind == "egress"
    if kind == "sandbox":
        return any(path == p or path.startswith(p + "/") for p in SANDBOX_PATHS)
    if kind == "egress":
        return False
    return True
