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
    creds = tclib.secret_variables({k: v for k, v in data.items() if k in PARTS})
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
        "tls_off": tclib.tls_unverified(data), "tls_parts": [p for p in tclib.TLS_PARTS if isinstance(data.get(p), dict)],
        "description": data.get("description", ""),
        "kinds": [k for k in PARTS if k in data],
        "docker": docker_info(data.get("docker"), env) if data.get("docker") else None,
        "git": git_info(name, data.get("git")) if data.get("git") else None,
        "db": db_info(data.get("db"), env) if data.get("db") else None,
        "web": web_info(data.get("web"), env) if data.get("web") else None,
        "files": files_info(data.get("files")) if data.get("files") else None,
        # URLs contain no secrets -> show them with ${VAR}s resolved
        "base_url": tclib.render((data.get("api") or {}).get("base_url", ""), env, keep_unknown=True),
        "vars": [{"name": v, "set": bool(env.get(v)), "secret": is_secret(v, creds),
                  "value": "" if is_secret(v, creds) else env.get(v, "")} for v in vars_],
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


PLACEHOLDER_DOMAIN = "example.com"  # {{DOMAIN}} in templates – the user enters the real address, nothing is guessed


def list_templates():
    out = []
    domain = PLACEHOLDER_DOMAIN
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


def variable_users():
    """${VAR} name → services that use it (a token entered for one is used by all of them)"""
    users = {}
    for p in sorted(glob.glob(os.path.join(SERVICES_DIR, "*.json"))):
        n = os.path.basename(p)[:-5]
        if n.startswith("_"):
            continue
        try:
            data = json.load(open(p))
        except (OSError, ValueError):
            continue
        for v in variables({k: x for k, x in data.items() if k in PARTS}):
            users.setdefault(v, []).append(n)
    return users


def _own_variables(data, name):
    """a second service of the same kind (same parts) gets its own variables instead of silently sharing the first one's
    tokens: PATCHMON_TOKEN_KEY → PATCHMON_SKYNET_TOKEN_KEY. Different kinds keep sharing (e.g. GH_TOKEN for api and git)."""
    kinds = {k for k in PARTS if k in data}
    taken = {}
    for p in glob.glob(os.path.join(SERVICES_DIR, "*.json")):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            other = json.load(open(p))
        except (OSError, ValueError):
            continue
        same = {k for k in PARTS if k in other} == kinds
        for v in variables({k: x for k, x in other.items() if k in PARTS}):
            taken[v] = taken.get(v, False) or same
    up = name.upper().replace("-", "_")
    text = json.dumps(data, ensure_ascii=False)
    for v in sorted(variables({k: x for k, x in data.items() if k in PARTS})):
        if not taken.get(v) or v.startswith(up + "_"):
            continue
        new = f"{up}_{v.split('_', 1)[1] if '_' in v else v}"
        while new in taken:
            new += "_2"
        text = text.replace("${" + v + "}", "${" + new + "}")
    return json.loads(text)


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
    domain = PLACEHOLDER_DOMAIN
    text = json.dumps(tpl, ensure_ascii=False).replace("svc NAME ", f"svc {name} ").replace(" NAME ", f" {name} ").replace("svc docker NAME", f"svc docker {name}").replace("{{DOMAIN}}", domain)
    text = text.replace("EXAMPLE_", f"{up}_").replace('"example"', f'"{name}"')
    data = _own_variables(json.loads(text), name)
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


def set_insecure_tls(name, on):
    """switch certificate verification for every TLS part of a service (api, mcp, git, db, web)"""
    data = json.loads(read_raw(name))
    for part in tclib.TLS_PARTS:
        if isinstance(data.get(part), dict):
            if on:
                data[part]["insecure_tls"] = True
            else:
                data[part].pop("insecure_tls", None)
    save_raw(name, json.dumps(data, ensure_ascii=False))


def valid_test_path(path):
    return bool(path) and path.startswith("/") and len(path) <= 300 and not any(c in path for c in " \n\r'\"")


def template_test_path(name):
    """test_path of the template with the same name (services are usually created from it) – a suggestion"""
    if not NAME.match(name):
        return ""
    try:
        with open(os.path.join(TEMPLATES_DIR, f"{name}.json")) as f:
            return json.load(f).get("test_path", "")
    except (OSError, ValueError):
        return ""


def set_test_path(name, path):
    """the endpoint `svc test` calls for the api part – one that needs the token, so a wrong token shows up"""
    if not valid_test_path(path):
        raise ValueError("test path: starts with /, no spaces or quotes")
    data = json.loads(read_raw(name))
    data["test_path"] = path
    save_raw(name, json.dumps(data))


def rename(old, new):
    """services/<old>.json → <new>.json; `svc <old> …` calls in examples and notes follow. Returns the service data.
    Tokens (${VAR} in .env) and the system membership stay; git working copies are moved by the caller (sandbox)."""
    if not NAME.match(new or ""):
        raise ValueError("name: lower-case letters, digits, - and _ (max 41)")
    if new == old:
        raise ValueError("that is the current name")
    if os.path.exists(path_of(new)):
        raise ValueError(f"{new} exists already")
    data = json.loads(read_raw(old))
    call = re.compile(r"(\bsvc\s+(?:(?:docker|compose|db|web|files|info|test|git\s+[a-z]+)\s+)?)" + re.escape(old) + r"\b")
    for key in ("examples", "notes"):
        if isinstance(data.get(key), list):
            data[key] = [call.sub(lambda m: m.group(1) + new, x) if isinstance(x, str) else x for x in data[key]]
    save_raw(new, json.dumps(data, ensure_ascii=False))  # new file first – a failure leaves the old one intact
    os.remove(path_of(old))
    return data


def credential_variables():
    """every ${VAR} some service uses as a credential (see tclib.secret_variables) – computed once per request"""
    found = set()
    for p in glob.glob(os.path.join(SERVICES_DIR, "*.json")):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            found |= tclib.secret_variables(json.load(open(p)))
        except (OSError, ValueError):
            continue
    return found


def secret_check():
    """is_secret for a whole request (settings page, token forms): name hints + credential positions"""
    creds = credential_variables()
    return lambda key: is_secret(key, creds)
