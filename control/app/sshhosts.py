"""Read ssh/config.d/*.conf for the SSH page."""
import glob
import os
import re

from .config import SSH_DIR


META = re.compile(r"^\s*#\s*tentacl(?:ai|aude):\s*sudo\s*=\s*(.*?)\s*$")
SUDO_RE = re.compile(r"^[A-Za-z0-9 ._/=-]{1,60}$")


def hosts():
    res = []
    for f in sorted(glob.glob(os.path.join(SSH_DIR, "config.d", "*.conf"))):
        block = []  # all aliases of the current Host line
        for line in open(f):
            m = META.match(line)
            if m:
                for h in block:
                    h["sudo"] = m.group(1)
                continue
            parts = line.strip().split(None, 1)
            if not parts or parts[0].startswith("#"):
                continue
            key, val = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
            if key == "host":
                block = [{"alias": a, "file": os.path.basename(f), "hostname": "", "user": "", "port": "22", "sudo": ""}
                         for a in val.split()]
                res += block
            elif key in ("hostname", "user", "port"):
                for h in block:
                    h[key] = val
    return res


def sudo_of(alias):
    return next((h["sudo"] for h in hosts() if h["alias"] == alias), "")


def set_sudo(alias, cmd):
    """write/remove '# tentaclai: sudo=<cmd>' in the Host block of <alias>"""
    cmd = (cmd or "").strip()
    if cmd and not SUDO_RE.match(cmd):
        raise ValueError("invalid sudo command (letters, digits, space . _ / = - only)")
    for f in sorted(glob.glob(os.path.join(SSH_DIR, "config.d", "*.conf"))):
        lines = open(f).read().splitlines(keepends=True)
        out, cur, found = [], False, False
        for line in lines:
            parts = line.split()
            if parts and parts[0].lower() == "host":
                cur = alias in parts[1:]
                out.append(line)
                if cur:
                    found = True
                    if cmd:
                        out.append(f"    # tentaclai: sudo={cmd}\n")
                continue
            if cur and META.match(line):
                continue
            out.append(line)
        if found:
            tmp = f + ".tmp"
            with open(tmp, "w") as fh:
                fh.writelines(out)
            os.chmod(tmp, os.stat(f).st_mode & 0o777)
            os.replace(tmp, f)
            return
    raise ValueError(f"host {alias} not found")


def public_key():
    try:
        with open(os.path.join(SSH_DIR, "keys", "tentaclai_ed25519.pub")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
