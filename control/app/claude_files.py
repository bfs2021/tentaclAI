"""Claude → Instructions / Memory / settings.json: the files that steer Claude in the sandbox.

- local/CLAUDE.md (site rules, root's file in the stack dir) – edited here like `svc config put local`: backup, 0644
  (Claude imports it with @…, an unreadable file is skipped without a word).
- ~/.claude/CLAUDE.md, the memory and settings.json live in Claude's home: read and written *as user claude* inside the
  sandbox (IN_SANDBOX), never as root in paths Claude controls.
- settings.json is shown without the parts other pages and the entrypoint manage (MANAGED_*), and with secret-looking
  env values masked; saving merges both back unchanged.
"""
import json
import os
import re
import shutil
import time

from . import dockerops
from .config import STACK_DIR, is_secret

LOCAL = os.path.join(STACK_DIR, "local", "CLAUDE.md")
MAX_SIZE = 512 * 1024
MANAGED_TOP = {"skillOverrides": "Skills", "model": "Models", "effortLevel": "Models"}
MANAGED_ENV = {"CLAUDE_CODE_SUBAGENT_MODEL": "Models", "DISABLE_TELEMETRY": "Settings (CLAUDE_TELEMETRY)",
               "DISABLE_ERROR_REPORTING": "Settings (CLAUDE_TELEMETRY)", "DISABLE_BUG_COMMAND": "Settings (CLAUDE_TELEMETRY)",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "the entrypoint"}
MASK = "[hidden – unchanged]"
MEMORY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.md")
PROJECT_NAME = re.compile(r"[A-Za-z0-9_.-][A-Za-z0-9_.-]{0,200}")

# runs as user claude in the sandbox: state | forget <project> <file> | settings <json>
IN_SANDBOX = r"""
import glob, json, os, sys, time, shutil
H = os.path.expanduser("~/.claude")
def text(p, limit=None):
    try:
        if os.path.islink(p):
            return None
        with open(p, errors="replace") as f:
            return f.read(limit) if limit else f.read()
    except OSError:
        return None
def lines(p):
    t = text(p)
    return None if t is None else len(t.splitlines())
act = sys.argv[1]
if act == "state":
    mem = []
    for d in sorted(glob.glob(H + "/projects/*/memory")):
        if os.path.islink(d) or os.path.islink(os.path.dirname(d)):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f.endswith(".md") and os.path.isfile(p) and not os.path.islink(p):
                mem.append({"project": os.path.basename(os.path.dirname(d)), "name": f,
                            "mtime": os.path.getmtime(p), "text": text(p, 20000)})
    print(json.dumps({"claude_md": text(H + "/CLAUDE.md"), "settings": text(H + "/settings.json"), "memory": mem,
                      "base_lines": lines("/etc/tentaclai/CLAUDE.base.md"), "host_docs_lines": lines("/etc/tentaclai/host-docs.md"),
                      "catalog_lines": lines("/workspace/SERVICES.md")}))
elif act == "forget":
    d = os.path.join(H, "projects", sys.argv[2], "memory")
    p = os.path.join(d, sys.argv[3])
    if os.path.islink(d) or os.path.islink(p) or not os.path.isfile(p):
        sys.exit("no such memory file")
    os.remove(p)
    idx = os.path.join(d, "MEMORY.md")
    t = text(idx)
    if t is not None and sys.argv[3] != "MEMORY.md":
        keep = [l for l in t.splitlines(True) if "](" + sys.argv[3] + ")" not in l]
        with open(idx + ".tmp", "w") as f:
            f.write("".join(keep))
        os.replace(idx + ".tmp", idx)
elif act == "settings":
    p = H + "/settings.json"
    if os.path.islink(p):
        sys.exit("settings.json is a symlink")
    if os.path.exists(p):
        shutil.copy2(p, p + ".bak-" + time.strftime("%Y-%m-%d-%H%M%S"))
        for old in sorted(glob.glob(p + ".bak-*"))[:-5]:
            os.remove(old)
    with open(p + ".tmp", "w") as f:
        f.write(sys.argv[2])
    os.replace(p + ".tmp", p)
"""


def _run(*args):
    return dockerops.run(["python3", "-c", IN_SANDBOX, *args], user=dockerops.CLAUDE_USER)


def state():
    """everything the Claude pages show – None if the sandbox doesn't run"""
    code, out = _run("state")
    if code != 0:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- local/CLAUDE.md (site rules)

def read_local():
    try:
        with open(LOCAL, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def save_local(text):
    """write the site rules like `svc config put local` – returns True if the file appeared or vanished (the
    generated CLAUDE.md imports it only when it has content, so it needs a refresh then)"""
    text = text.replace("\r\n", "\n")
    if len(text.encode()) > MAX_SIZE:
        raise ValueError("file too large")
    if text and not text.endswith("\n"):
        text += "\n"
    old = read_local() if os.path.exists(LOCAL) else None
    if old == text:
        return False
    if old is not None:
        shutil.copy2(LOCAL, f"{LOCAL}.bak-{time.strftime('%Y-%m-%d-%H%M%S')}")
    os.makedirs(os.path.dirname(LOCAL), exist_ok=True)
    with open(LOCAL + ".tmp", "w") as f:
        f.write(text)
    os.chmod(LOCAL + ".tmp", 0o644)
    os.replace(LOCAL + ".tmp", LOCAL)
    return bool(old and old.strip()) != bool(text.strip())


# ---------------------------------------------------------------- memory

def forget(project, name):
    if not PROJECT_NAME.fullmatch(project or "") or project in (".", "..") or not MEMORY_NAME.fullmatch(name or ""):
        raise ValueError("invalid memory entry")
    code, out = _run("forget", project, name)
    if code != 0:
        raise ValueError(out.strip()[-200:] or "could not delete")


# ---------------------------------------------------------------- settings.json

def editable(settings):
    """the user's part of settings.json: managed keys removed, secret-looking env values masked"""
    s = {k: v for k, v in settings.items() if k not in MANAGED_TOP}
    env = {k: (MASK if is_secret(k) else v) for k, v in (settings.get("env") or {}).items() if k not in MANAGED_ENV}
    if env:
        s["env"] = env
    else:
        s.pop("env", None)
    return s


def merge(current, text):
    """edited JSON text → the full settings.json: managed parts and masked values from the current file"""
    try:
        new = json.loads(text)
    except ValueError as e:
        raise ValueError(f"invalid JSON – {e}")
    if not isinstance(new, dict):
        raise ValueError("settings.json is a JSON object { … }")
    env = new.get("env", {})
    if not isinstance(env, dict):
        raise ValueError('"env" is an object of name → value')
    for k in MANAGED_TOP:
        if k in new:
            raise ValueError(f'"{k}" is set on the {MANAGED_TOP[k]} page')
    for k in MANAGED_ENV:
        if k in env:
            raise ValueError(f'env "{k}" is managed by {MANAGED_ENV[k]}')
    old_env = current.get("env") or {}
    for k, v in env.items():
        if v == MASK:
            if k not in old_env:
                raise ValueError(f'env "{k}": enter a value')
            env[k] = old_env[k]
    out = dict(new)
    for k in MANAGED_TOP:
        if k in current:
            out[k] = current[k]
    env.update({k: v for k, v in old_env.items() if k in MANAGED_ENV})
    if env:
        out["env"] = env
    else:
        out.pop("env", None)
    return out


def load_settings(raw):
    try:
        s = json.loads(raw or "{}")
    except ValueError:
        return None
    return s if isinstance(s, dict) else None


def save_settings(text):
    st = state()
    if st is None:
        raise ValueError("tentaclAI is not running")
    current = load_settings(st["settings"])
    if current is None:
        raise ValueError("the current settings.json is not valid JSON – fix it in a terminal first")
    full = merge(current, text)
    code, out = _run("settings", json.dumps(full, indent=2, ensure_ascii=False) + "\n")
    if code != 0:
        raise ValueError(out.strip()[-200:] or "could not write settings.json")
