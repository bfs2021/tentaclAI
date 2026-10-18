"""tclib – logic shared by the tentaclAI sandbox tools (svc, tc-scan, claude-instructions) and tentacontrol.

One implementation for: .env files, ${VAR} placeholders, services and systems, and SSH hosts in ssh/config.d.
Standard library only – it is copied into both images.
"""
import glob
import json
import os
import re

# ---------------------------------------------------------------- .env and ${VAR}

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def read_env(path):
    """KEY=value lines of a compose .env file (comments and quotes handled); {} if missing"""
    values = {}
    try:
        with open(path) as f:
            for line in f:
                m = ENV_LINE.match(line)
                if m:
                    values[m.group(1)] = unquote(m.group(2))
    except OSError:
        pass
    return values


def variables(obj):
    """all ${VAR} names used in a (nested) value; keys starting with _ are ignored"""
    found = set()
    if isinstance(obj, str):
        found.update(VAR.findall(obj))
    elif isinstance(obj, list):
        for v in obj:
            found |= variables(v)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if not k.startswith("_"):
                found |= variables(v)
    return found


def render(obj, env, keep_unknown=False):
    """replace ${VAR} with values from env; unknown ones become '' (or stay as they are with keep_unknown)"""
    if isinstance(obj, str):
        return VAR.sub(lambda m: env.get(m.group(1)) or (m.group(0) if keep_unknown else ""), obj)
    if isinstance(obj, list):
        return [render(v, env, keep_unknown) for v in obj]
    if isinstance(obj, dict):
        return {k: render(v, env, keep_unknown) for k, v in obj.items() if not k.startswith("_")}
    return obj


def missing(obj, env):
    return sorted(v for v in variables(obj) if not env.get(v))


# ---------------------------------------------------------------- services and systems

PARTS = ("api", "mcp", "git", "docker", "db", "web", "files")


def _load_dir(path, errors=None):
    """name -> parsed JSON of <path>/*.json (files starting with _ are templates/examples)"""
    items = {}
    for p in sorted(glob.glob(os.path.join(path, "*.json"))):
        base = os.path.basename(p)
        if base.startswith("_"):
            continue
        try:
            with open(p) as f:
                data = json.load(f)
        except ValueError as e:
            if errors is not None:
                errors[base[:-5]] = str(e)
            continue
        data.setdefault("name", base[:-5])
        items[base[:-5]] = data
    return items


def load_systems(stack_dir, errors=None):
    return _load_dir(os.path.join(stack_dir, "systems"), errors)


def load_services(services_dir, systems=None, errors=None):
    """services by name; with systems given, docker (mode ssh) and files parts inherit the system's SSH host"""
    services = _load_dir(services_dir, errors)
    for svc in services.values():
        sysd = (systems or {}).get(svc.get("system", ""))
        if not sysd or not sysd.get("ssh_host"):
            continue
        d, f = svc.get("docker"), svc.get("files")
        if d and d.get("mode") == "ssh" and not d.get("ssh_host"):
            d["ssh_host"] = sysd["ssh_host"]
        if f and not f.get("ssh_host"):
            f["ssh_host"] = sysd["ssh_host"]
    return services


TLS_PARTS = ("api", "mcp", "git", "db", "web")


def tls_unverified(svc):
    """parts whose TLS certificates are not verified (insecure_tls: true)"""
    return [p for p in TLS_PARTS if isinstance(svc.get(p), dict) and svc[p].get("insecure_tls")]


def kinds(svc):
    return [k for k in PARTS if k in svc]


def enabled(svc):
    return svc.get("enabled", True) is not False


def status(svc, env):
    """(state, missing vars): state is 'disabled', 'missing' or 'active'"""
    if not enabled(svc):
        return "disabled", []
    miss = sorted(set().union(*[set(missing(svc.get(p, {}), env)) for p in PARTS]))
    return ("missing", miss) if miss else ("active", [])


def status_text(svc, env):
    state, miss = status(svc, env)
    return "missing: " + ", ".join(miss) if state == "missing" else state


# ---------------------------------------------------------------- SSH hosts (ssh/config.d/*.conf)

META = re.compile(r"^\s*#\s*tentacl(?:ai|aude):\s*(sudo|access)\s*=\s*(.*?)\s*$")
SSH_ACCESS = ("ro", "rw", "confirm")
SSH_ACCESS_DEFAULT = "confirm"
SUDO_RE = re.compile(r"^[A-Za-z0-9 ._/=-]{1,60}$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
JUMP_RE = re.compile(r"^([A-Za-z0-9._-]{1,32}@)?[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")
SSH_KEYS = {"hostname": "HostName", "user": "User", "port": "Port", "proxyjump": "ProxyJump"}


def _conf_files(conf_d):
    return sorted(glob.glob(os.path.join(conf_d, "*.conf")))


def ssh_hosts(conf_d):
    """every alias of every Host block: alias, file, hostname, user, port, proxyjump, hostkeyalias, sudo, access"""
    res = []
    for f in _conf_files(conf_d):
        block = []  # all aliases of the current Host line
        for line in open(f):
            m = META.match(line)
            if m:
                for h in block:
                    h[m.group(1)] = m.group(2)
                continue
            parts = line.strip().split(None, 1)
            if not parts or parts[0].startswith("#"):
                continue
            key, val = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
            if key == "host":
                block = [{"alias": a, "file": os.path.basename(f), "hostname": "", "user": "", "port": "22",
                          "proxyjump": "", "sudo": "", "hostkeyalias": "", "access": SSH_ACCESS_DEFAULT}
                         for a in val.split() if "*" not in a]
                res += block
            elif key in SSH_KEYS or key == "hostkeyalias":
                for h in block:
                    h[key] = val
    return res


def ssh_host(conf_d, alias):
    return next((h for h in ssh_hosts(conf_d) if h["alias"] == alias), None)


def ssh_sudo(conf_d, alias):
    h = ssh_host(conf_d, alias)
    return h["sudo"] if h else ""


def ssh_access(conf_d, alias):
    """access mode of `svc ssh <alias>`: ro = no ssh commands, confirm = each needs approval, rw = allowed"""
    h = ssh_host(conf_d, alias)
    mode = (h or {}).get("access") or SSH_ACCESS_DEFAULT
    return mode if mode in SSH_ACCESS else "ro"


def ssh_validate(fields, aliases=None):
    """check user input for a host; raises ValueError with a readable message"""
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
    if fields.get("access", SSH_ACCESS_DEFAULT) not in SSH_ACCESS:
        raise ValueError("access: ro, rw or confirm")
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
    if fields.get("access", SSH_ACCESS_DEFAULT) != SSH_ACCESS_DEFAULT:
        lines.append(f"    # tentaclai: access={fields['access']}\n")
    return lines


def _write(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ssh_add(conf_d, fields, comment="added in tentacontrol"):
    ssh_validate(fields, aliases={h["alias"] for h in ssh_hosts(conf_d)})
    path = os.path.join(conf_d, f"{fields['alias']}.conf")
    if os.path.exists(path):
        raise ValueError(f"{os.path.basename(path)} exists already")
    os.makedirs(conf_d, exist_ok=True)
    _write(path, [f"# {comment}\n"] + _block_lines(fields))


def _locate(conf_d, alias):
    """(file, lines, start, end) of the Host block that holds exactly this alias"""
    for f in _conf_files(conf_d):
        lines = open(f).read().splitlines(keepends=True)
        start = None
        for i, line in enumerate(lines):
            parts = line.split()
            if parts and parts[0].lower() in ("host", "match"):
                if start is not None:
                    return f, lines, start, i
                if parts[0].lower() == "host" and alias in parts[1:]:
                    if len(parts) > 2:
                        raise ValueError(f"{alias} shares its Host line with other aliases – edit {os.path.basename(f)} by hand")
                    start = i
        if start is not None:
            return f, lines, start, len(lines)
    raise ValueError(f"host {alias} not found")


def ssh_update(conf_d, alias, fields):
    """change HostName/User/Port/ProxyJump/sudo and optionally the alias; keeps unmanaged lines of the block"""
    others = {h["alias"] for h in ssh_hosts(conf_d)} - {alias}
    ssh_validate(fields, aliases=others)
    f, lines, start, end = _locate(conf_d, alias)
    keep = [l for l in lines[start + 1:end] if not META.match(l)
            and (l.split()[0].lower() if l.split() else "") not in SSH_KEYS]
    while keep and not keep[-1].strip():
        keep.pop()
    new = _block_lines(fields, keep)
    if end < len(lines):
        new.append("\n")
    _write(f, lines[:start] + new + lines[end:])
    # a file named after the host follows a rename, if it holds only this host
    target = os.path.join(conf_d, f"{fields['alias']}.conf")
    single = sum(1 for l in lines if l.split() and l.split()[0].lower() == "host") == 1
    if fields["alias"] != alias and single and os.path.basename(f) == f"{alias}.conf" and not os.path.exists(target):
        os.replace(f, target)


def ssh_remove(conf_d, alias):
    f, lines, start, end = _locate(conf_d, alias)
    rest = lines[:start] + lines[end:]
    if any(l.split() and l.split()[0].lower() == "host" for l in rest):
        _write(f, rest)
    else:  # the file only held this host
        os.remove(f)


def ssh_set_sudo(conf_d, alias, cmd):
    h = ssh_host(conf_d, alias)
    if not h:
        raise ValueError(f"host {alias} not found")
    ssh_update(conf_d, alias, dict(h, sudo=(cmd or "").strip()))


def ssh_known_name(fields):
    """name under which ssh stores the host key: HostKeyAlias, host, or [host]:port for non-default ports"""
    if fields.get("hostkeyalias"):
        return fields["hostkeyalias"]
    port = str(fields.get("port") or "22")
    return fields["hostname"] if port == "22" else f"[{fields['hostname']}]:{port}"
