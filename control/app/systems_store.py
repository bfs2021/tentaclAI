"""systems/*.json – groups of services (one host or appliance) with a shared SSH host and rules.

A service joins a system with "system": "<name>" in its own JSON; services without it stand alone.
"""
import glob
import json
import os

import tclib

from . import services_store
from .config import SYSTEMS_DIR
from .services_store import NAME


def path_of(name):
    if not NAME.match(name or ""):
        raise ValueError("name: lowercase letters, digits, - and _")
    return os.path.join(SYSTEMS_DIR, f"{name}.json")


def load_all():
    errors = {}
    data = tclib.load_systems(os.path.dirname(SYSTEMS_DIR), errors)
    data.update({n: {"description": "invalid JSON"} for n in errors})
    out = []
    for name in sorted(data):
        x = data[name]
        x["name"] = name
        x.setdefault("description", "")
        x.setdefault("ssh_host", "")
        x.setdefault("notes", [])
        out.append(x)
    return out


def get(name):
    with open(path_of(name)) as f:
        data = json.load(f)
    data["name"] = name
    return data


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def save_raw(name, data):
    """write a whole system definition (import) – members still come from the services' own "system" field"""
    _central_check("systems", name)
    if not isinstance(data, dict):
        raise ValueError("a system is a JSON object")
    _write(path_of(name), {"name": name, **{k: v for k, v in data.items() if k != "name"}})


def set_service_system(name, system):
    data = json.loads(services_store.read_raw(name))
    if system:
        data["system"] = system
    else:
        data.pop("system", None)
    services_store.save_raw(name, json.dumps(data, ensure_ascii=False))


def create(name):
    p = path_of(name)
    if os.path.exists(p):
        raise ValueError(f"system {name} already exists")
    _write(p, {"name": name, "description": "", "ssh_host": "", "notes": []})


def save(name, description, ssh_host, notes, members):
    """write the system and make exactly <members> point to it"""
    p = path_of(name)
    data = get(name) if os.path.exists(p) else {}
    data.update({"name": name, "description": description.strip(), "ssh_host": ssh_host.strip(),
                 "notes": [n.strip() for n in notes if n.strip()]})
    _write(p, data)
    members = set(members)
    for s in services_store.load_all():
        cur = s["data"].get("system")
        if s["name"] in members and cur != name:
            set_service_system(s["name"], name)
        elif s["name"] not in members and cur == name:
            set_service_system(s["name"], "")


def delete(name):
    _central_check("systems", name)
    for s in services_store.load_all():
        if s["data"].get("system") == name:
            set_service_system(s["name"], "")
    os.remove(path_of(name))


def _central_check(section, key):
    """refuse a change the central server enforces (central management)"""
    from .central import check_write
    check_write(section, key)
