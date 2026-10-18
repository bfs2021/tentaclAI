"""SSH hosts in ssh/config.d/*.conf – read, add, edit and remove single Host blocks.

Every host is a block "Host <alias>" with HostName / User / Port / ProxyJump and an optional
"# tentaclai: sudo=<prefix>" marker. Lines we don't manage (HostKeyAlias, IdentityFile, comments …) are kept.
"""
import glob
import os
import re

from .config import SSH_DIR

META = re.compile(r"^\s*#\s*tentacl(?:ai|aude):\s*sudo\s*=\s*(.*?)\s*$")
SUDO_RE = re.compile(r"^[A-Za-z0-9 ._/=-]{1,60}$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
JUMP_RE = re.compile(r"^([A-Za-z0-9._-]{1,32}@)?[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")
MANAGED = {"hostname": "HostName", "user": "User", "port": "Port", "proxyjump": "ProxyJump"}
CONF_D = os.path.join(SSH_DIR, "config.d")


def _files():
    return sorted(glob.glob(os.path.join(CONF_D, "*.conf")))


def hosts():
    res = []
    for f in _files():
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
                block = [{"alias": a, "file": os.path.basename(f), "hostname": "", "user": "", "port": "22",
                          "proxyjump": "", "sudo": "", "hostkeyalias": ""} for a in val.split()]
                res += block
            elif key in MANAGED or key == "hostkeyalias":
                for h in block:
                    h[key] = val
    return res


def get(alias):
    return next((h for h in hosts() if h["alias"] == alias), None)


def sudo_of(alias):
    h = get(alias)
    return h["sudo"] if h else ""


def validate(fields, aliases=None):
    """check user input; raises ValueError with a readable message"""
    if not ALIAS_RE.match(fields.get("alias", "")):
        raise ValueError("alias: letters, digits, . _ - (max 40)")
    if not HOST_RE.match(fields.get("hostname", "")):
        raise ValueError("host: a DNS name or IPv4 address")
    if not USER_RE.match(fields.get("user", "")):
        raise ValueError("user: letters, digits, . _ -")
    port = str(fields.get("port") or "22")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("port: 1–65535")
    jump = fields.get("proxyjump", "")
    if jump and not JUMP_RE.match(jump):
        raise ValueError("ProxyJump: an existing alias or user@host[:port]")
    if jump and jump == fields["alias"]:
        raise ValueError("ProxyJump can't be the host itself")
    sudo = fields.get("sudo", "")
    if sudo and not SUDO_RE.match(sudo):
        raise ValueError("sudo command: letters, digits, space . _ / = - only")
    if aliases is not None and fields["alias"] in aliases:
        raise ValueError(f"alias {fields['alias']} exists already")


def _block_lines(fields, extra=()):
    lines = [f"Host {fields['alias']}\n", f"    HostName {fields['hostname']}\n", f"    User {fields['user']}\n"]
    if str(fields.get("port") or "22") != "22":
        lines.append(f"    Port {fields['port']}\n")
    if fields.get("proxyjump"):
        lines.append(f"    ProxyJump {fields['proxyjump']}\n")
    lines += list(extra)
    if fields.get("sudo"):
        lines.append(f"    # tentaclai: sudo={fields['sudo']}\n")
    return lines


def _write(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def add(fields):
    validate(fields, aliases={h["alias"] for h in hosts()})
    path = os.path.join(CONF_D, f"{fields['alias']}.conf")
    if os.path.exists(path):
        raise ValueError(f"{os.path.basename(path)} exists already")
    os.makedirs(CONF_D, exist_ok=True)
    _write(path, ["# added in tentacontrol\n"] + _block_lines(fields))


def _locate(alias):
    """(file, lines, start, end) of the Host block that holds exactly this alias"""
    for f in _files():
        lines = open(f).read().splitlines(keepends=True)
        start = None
        for i, line in enumerate(lines):
            parts = line.split()
            if parts and parts[0].lower() == "host":
                if start is not None:
                    return f, lines, start, i
                if alias in parts[1:]:
                    if len(parts) > 2:
                        raise ValueError(f"{alias} shares its Host line with other aliases – edit {os.path.basename(f)} by hand")
                    start = i
            elif parts and parts[0].lower() == "match" and start is not None:
                return f, lines, start, i
        if start is not None:
            return f, lines, start, len(lines)
    raise ValueError(f"host {alias} not found")


def update(alias, fields):
    """change HostName/User/Port/ProxyJump/sudo and optionally the alias; keeps unmanaged lines of the block"""
    others = {h["alias"] for h in hosts()} - {alias}
    validate(fields, aliases=others)
    f, lines, start, end = _locate(alias)
    block = lines[start + 1:end]
    # keep unmanaged lines (comments, HostKeyAlias, IdentityFile …) but drop trailing blank lines
    keep = [l for l in block if not META.match(l)
            and (l.split()[0].lower() if l.split() else "") not in MANAGED]
    while keep and not keep[-1].strip():
        keep.pop()
    new = _block_lines(fields, keep)
    if end < len(lines):
        new.append("\n")
    _write(f, lines[:start] + new + lines[end:])
    # a file named after the host follows a rename, if it holds only this host
    target = os.path.join(CONF_D, f"{fields['alias']}.conf")
    single = sum(1 for l in lines if l.split() and l.split()[0].lower() == "host") == 1
    if fields["alias"] != alias and single and os.path.basename(f) == f"{alias}.conf" and not os.path.exists(target):
        os.replace(f, target)


def remove(alias):
    f, lines, start, end = _locate(alias)
    rest = lines[:start] + lines[end:]
    if any(l.split() and l.split()[0].lower() == "host" for l in rest):
        _write(f, rest)
    else:  # the file only held this host
        os.remove(f)


def set_sudo(alias, cmd):
    h = get(alias) or {}
    if not h:
        raise ValueError(f"host {alias} not found")
    fields = dict(h, sudo=(cmd or "").strip())
    update(alias, fields)


def known_name(fields):
    """name under which ssh stores the host key: HostKeyAlias, host, or [host]:port for non-default ports"""
    if fields.get("hostkeyalias"):
        return fields["hostkeyalias"]
    port = str(fields.get("port") or "22")
    return fields["hostname"] if port == "22" else f"[{fields['hostname']}]:{port}"


def public_key():
    try:
        with open(os.path.join(SSH_DIR, "keys", "tentaclai_ed25519.pub")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
