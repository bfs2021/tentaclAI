"""claude.ai connectors (Google Drive, Claude Docs …) come with the signed-in account – built-in tentacles that bypass svc.

They are shown under Tentacles → Services and are off by default: disabled ones are written to deniedMcpServers in
/etc/claude-code/managed-settings.json inside the sandbox (root-owned, Claude can't change it). A denied connector no
longer appears in `claude mcp list`, so the known ones are remembered in data/control/connectors.json.
"""
import json
import os
import re
import time

from . import dockerops, jsonstore
from .config import DATA_DIR

STATE = os.path.join(DATA_DIR, "connectors.json")
MANAGED = "/etc/claude-code/managed-settings.json"
LINE = re.compile(r"^(claude\.ai [^:]+):\s*(\S+)\s*-\s*(.+)$")


def load():
    return jsonstore.load(STATE, {"known": {}})


def _save(s):
    jsonstore.save(STATE, s)


def managed_json(s=None):
    s = s or load()
    denied = [{"serverName": n} for n, c in sorted(s["known"].items()) if not c.get("enabled")]
    return json.dumps({"deniedMcpServers": denied}, indent=1) + "\n"


def apply():
    """write the deny list into the running sandbox (new Claude sessions pick it up)"""
    code, out = dockerops.sandbox_exec(
        ["sh", "-c", f"mkdir -p {os.path.dirname(MANAGED)} && cat > {MANAGED}.tmp && chmod 644 {MANAGED}.tmp && mv {MANAGED}.tmp {MANAGED}"],
        stdin=managed_json(), timeout=20)
    return code == 0


def discover():
    """add connectors Claude Code lists now; new ones start disabled and are denied right away"""
    code, out = dockerops.run(["timeout", "60", "claude", "mcp", "list"], workdir="/workspace", user=dockerops.CLAUDE_USER)
    if code != 0:
        return False
    s = load()
    new = False
    for line in out.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        name = m.group(1)
        c = s["known"].setdefault(name, {"enabled": False, "first_seen": time.time()})
        c.update(url=m.group(2), status=m.group(3).strip()[:60], seen=time.time())
        new = new or not c.get("enabled")  # visible although disabled → deny list not applied yet
    _save(s)
    if new:
        apply()
    return True


def set_enabled(name, on):
    s = load()
    if name not in s["known"]:
        raise ValueError("unknown connector")
    s["known"][name]["enabled"] = bool(on)
    _save(s)
    return apply()


def listing():
    return [dict(c, name=n, short=n.replace("claude.ai ", "")) for n, c in sorted(load()["known"].items())]
