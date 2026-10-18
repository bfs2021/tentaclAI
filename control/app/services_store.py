"""services/*.json – same rules as the `svc` CLI in the claude container."""
import glob
import json
import os
import re

import tclib

from .config import SERVICES_DIR, STACK_DIR, is_secret  # noqa: F401  (STACK_DIR re-exported)
from .envfile import read_env

TEMPLATES_DIR = os.path.join(SERVICES_DIR, "_templates")

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
PARTS = tclib.PARTS
variables = tclib.variables


def path_of(name):
    if not NAME.match(name):
        raise ValueError("name: lowercase letters, digits, - and _")
    return os.path.join(SERVICES_DIR, f"{name}.json")


def load_all():
    env = read_env()
    errors = {}
    data = tclib.load_services(SERVICES_DIR, errors=errors)
    out = [describe(n, d, env) for n, d in data.items()] + [describe(n, {}, env, e) for n, e in errors.items()]
    return sorted(out, key=lambda x: x["name"])


def describe(name, data, env=None, error=None):
    env = env if env is not None else read_env()
    vars_ = sorted(variables({k: v for k, v in data.items() if k in PARTS}))
    missing = [v for v in vars_ if not env.get(v)]
    enabled = data.get("enabled", True) is not False
    if error:
        state, label = "error", "JSON error"
    elif not enabled:
        state, label = "off", "disabled"
    elif missing:
        state, label = "warn", "token missing"
    else:
        state, label = "ok", "active"
    return {
        "name": name, "data": data, "error": error, "enabled": enabled, "system": data.get("system", ""),
        "description": data.get("description", ""),
        "kinds": [k for k in PARTS if k in data],
        "docker": docker_info(data.get("docker"), env) if data.get("docker") else None,
        "git": git_info(name, data.get("git")) if data.get("git") else None,
        "db": db_info(data.get("db"), env) if data.get("db") else None,
        "web": web_info(data.get("web"), env) if data.get("web") else None,
        "files": files_info(data.get("files")) if data.get("files") else None,
        # URLs contain no secrets -> show them with ${VAR}s resolved
        "base_url": tclib.render((data.get("api") or {}).get("base_url", ""), env, keep_unknown=True),
        "vars": [{"name": v, "set": bool(env.get(v)), "secret": is_secret(v),
                  "value": "" if is_secret(v) else env.get(v, "")} for v in vars_],
        "missing": missing, "state": state, "label": label,
    }


def git_info(name, git):
    path = git.get("path") or f"/workspace/repos/{name}"
    host_path = path.replace("/workspace", os.path.join(STACK_DIR, "workspace"), 1) if path.startswith("/workspace") else None
    cloned = bool(host_path and os.path.isdir(os.path.join(host_path, ".git")))
    return {"url": git.get("url", ""), "access": git.get("access", "ro"), "branch": git.get("branch", ""),
            "path": path, "cloned": cloned, "auth": (git.get("auth") or {}).get("type", "none")}


def _sub(v, env):
    return tclib.render(str(v or ""), env, keep_unknown=True)


def db_info(d, env):
    """connection target only – user/password stay hidden"""
    engine = "mysql" if (d.get("engine") or "postgres").lower() in ("mysql", "mariadb") else "postgres"
    return {"engine": engine, "access": d.get("access", "ro"),
            "target": f"{_sub(d.get('host'), env)}:{_sub(d.get('port'), env) or ('3306' if engine == 'mysql' else '5432')}/{_sub(d.get('database'), env)}"}


def web_info(w, env):
    return {"url": _sub(w.get("base_url"), env), "auth": (w.get("auth") or {}).get("type", "none")}


def files_info(f):
    return {"target": f"{f.get('ssh_host') or '(system SSH host)'}:{f.get('path', '')}", "access": f.get("access", "ro"),
            "sudo": bool(f.get("sudo"))}


def docker_info(d, env):
    mode = d.get("mode", "socket")
    return {"mode": mode, "access": d.get("access", "ro"), "stacks_dir": _sub(d.get("stacks_dir"), env),
            "target": _sub(d.get("socket", "/var/run/docker.sock"), env) if mode == "socket" else d.get("ssh_host", "")}


def list_templates():
    out = []
    domain = read_env().get("TC_DOMAIN") or "example.lan"
    for p in sorted(glob.glob(os.path.join(TEMPLATES_DIR, "*.json"))):
        try:
            with open(p) as f:
                data = json.load(f)
        except ValueError:
            continue
        meta = data.get("_meta", {})
        out.append({"id": os.path.basename(p)[:-5], "title": meta.get("title", os.path.basename(p)[:-5]),
                    "category": meta.get("category", "General"), "description": data.get("description", ""),
                    "setup": meta.get("setup", "").replace("{{DOMAIN}}", domain), "kinds": [k for k in PARTS if k in data]})
    order = ["Docker hosts", "Smart Home", "Automation", "Docker & Infra", "Monitoring", "Network", "Security", "Apps", "Development",
             "Git repos", "Databases", "Web sources", "Files", "General"]
    out.sort(key=lambda t: (order.index(t["category"]) if t["category"] in order else 99, t["title"].lower()))
    return out


def read_raw(name):
    with open(path_of(name)) as f:
        return f.read()


def save_raw(name, text):
    data = json.loads(text)  # raises ValueError on invalid JSON
    if not isinstance(data, dict):
        raise ValueError("JSON must be an object")
    data["name"] = name
    p = path_of(name)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def create(name, template=""):
    p = path_of(name)
    if os.path.exists(p):
        raise ValueError(f"{name} already exists")
    src = os.path.join(TEMPLATES_DIR, f"{template}.json") if template else os.path.join(SERVICES_DIR, "_template.json")
    if template and not NAME.match(template):
        raise ValueError("invalid template")
    try:
        with open(src) as f:
            tpl = json.load(f)
    except (FileNotFoundError, ValueError):
        tpl = {"description": "", "api": {"base_url": "https://", "auth": {"type": "none"}}}
    setup = (tpl.get("_meta") or {}).get("setup")
    tpl = {k: v for k, v in tpl.items() if not k.startswith("_")}
    if setup:
        tpl["notes"] = [f"Setup: {setup}"] + tpl.get("notes", [])
    up = name.upper().replace("-", "_")
    domain = read_env().get("TC_DOMAIN") or "example.lan"
    text = json.dumps(tpl, ensure_ascii=False).replace("svc NAME ", f"svc {name} ").replace(" NAME ", f" {name} ").replace("svc docker NAME", f"svc docker {name}").replace("{{DOMAIN}}", domain)
    text = text.replace("EXAMPLE_", f"{up}_").replace('"example"', f'"{name}"')
    data = json.loads(text)
    data = {"name": name, **{k: v for k, v in data.items() if k != "name"}}
    save_raw(name, json.dumps(data, ensure_ascii=False))


def set_enabled(name, enabled):
    data = json.loads(read_raw(name))
    if enabled:
        data.pop("enabled", None)
    else:
        data["enabled"] = False
    save_raw(name, json.dumps(data))


def delete(name):
    os.remove(path_of(name))
