"""Skills of Claude Code and the signed-in account – each listed skill costs context in every request.

Settings → Skills sets a mode per skill (on / only as /command / off), written as skillOverrides into Claude's
~/.claude/settings.json (by the entrypoint on every start, by apply() right away). Skills that make no sense in a
headless sandbox start off (tclib.SKILLS_OFF_DEFAULT). Claude Code no longer lists a skill that is off, so the known
ones are remembered in data/control/skills.json; descriptions come from the skill listing in recent session files.
Site skills (local/skills/<name>/SKILL.md, proposed by Claude with `svc config put skill`) are listed here too – `svc sync`
copies them into Claude's home; removing one keeps it as local/skills/<name>.bak-… .
"""
import glob
import json
import os
import re
import shutil
import time

import tclib

from . import dockerops, jsonstore
from .config import DATA_DIR, STACK_DIR
from .usage import session_files

STATE = os.path.join(DATA_DIR, "skills.json")
ROW = re.compile(r"^\|\s*([^|\s][^|]*?)\s*\|\s*([^|]+?)\s*\|\s*~?([\d.]+)(k?)\s*\|$")
LISTED = re.compile(r"^- (\S+?): (.+)$")  # names can hold a colon (anthropic-skills:pdf)
APPLY = r"""
import json, os, sys
sys.path.insert(0, "/usr/local/lib/tentaclai")
import tclib
p = os.path.expanduser("~/.claude/settings.json")
try:
    s = json.load(open(p))
except (OSError, ValueError):
    s = {}
tclib.apply_skill_overrides(s, json.loads(sys.argv[1]))
open(p + ".tmp", "w").write(json.dumps(s, indent=2) + "\n")
os.replace(p + ".tmp", p)
"""


def load():
    try:
        s = json.load(open(STATE))
    except (OSError, ValueError):
        s = {}
    s.setdefault("known", {})
    s.setdefault("modes", {})
    return s


def _save(s):
    jsonstore.save(STATE, s)


def apply(s=None):
    """write the modes into Claude's settings.json in the running sandbox (new Claude sessions pick them up)"""
    code, _ = dockerops.run(["python3", "-c", APPLY, json.dumps(tclib.skill_overrides(s or load()))], user=dockerops.CLAUDE_USER)
    return code == 0


def parse_context(text):
    """the Skills table of `claude -p /context`: name, source, approx. tokens"""
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith("### "):
            inside = line.strip() == "### Skills"
            continue
        m = ROW.match(line.strip()) if inside else None
        if m and m.group(1) != "Skill":
            out[m.group(1)] = {"source": m.group(2), "tokens": int(float(m.group(3)) * (1000 if m.group(4) else 1))}
    return out


def _descriptions(limit=20):
    """name → description from the skill listing Claude Code wrote into recent sessions (first lines only)"""
    found = {}
    files = sorted(session_files(), key=os.path.getmtime, reverse=True)[:limit]
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 60:
                        break
                    if '"skill_listing"' not in line:
                        continue
                    content = (json.loads(line).get("attachment") or {}).get("content") or ""
                    for item in content.split("\n"):
                        m = LISTED.match(item.strip())
                        if m:
                            found.setdefault(m.group(1), m.group(2).strip()[:220])
        except (OSError, ValueError):
            continue
    return found


def discover():
    """`/context` works without a model call – adds the skills Claude Code lists now"""
    code, out = dockerops.run(["timeout", "60", "claude", "-p", "--no-session-persistence", "/context"], workdir="/workspace", user=dockerops.CLAUDE_USER)
    rows = parse_context(out) if code == 0 else {}
    if not rows:
        return False
    s = load()
    for name, r in rows.items():
        k = s["known"].setdefault(name, {"first_seen": time.time()})
        k.update(r, seen=time.time())
    for name, desc in _descriptions().items():
        s["known"].setdefault(name, {"first_seen": time.time()})["description"] = desc
    _save(s)
    if any(tclib.skill_mode(s, n) != "on" for n in rows):
        apply(s)  # listed although not on → settings.json was changed or is out of date
    return True


def set_mode(name, mode):
    if mode not in tclib.SKILL_MODES:
        raise ValueError("unknown mode")
    s = load()
    if name not in tclib.skill_overrides(s) and name not in tclib.site_skills(STACK_DIR):
        raise ValueError("unknown skill")
    if name in tclib.SKILLS_REQUIRED and mode == "off":
        raise ValueError(f"{name} is required by tentaclAI (its buttons start /{name}) – /command only is possible")
    s["modes"][name] = mode
    _save(s)
    return apply(s)


def site_text(name):
    if name not in tclib.site_skills(STACK_DIR):
        raise ValueError("unknown site skill")
    with open(os.path.join(STACK_DIR, "local", "skills", name, "SKILL.md"), errors="replace") as f:
        return f.read()


def save_site(name, text):
    """write a site skill like `svc config put skill` (checked, readable for Claude, last 5 versions kept) – svc sync then
    copies it into Claude's skills"""
    _central_check("skills", name)
    text = text.replace("\r\n", "\n")
    problem = tclib.site_skill_problem(name, text)
    if problem:
        raise ValueError(problem)
    base = os.path.join(STACK_DIR, "local", "skills")
    path = os.path.join(base, name, "SKILL.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for d in (base, os.path.dirname(path)):
        os.chmod(d, 0o755)
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-{time.strftime('%Y-%m-%d-%H%M%S')}")
        for old in sorted(glob.glob(path + ".bak-*"))[:-5]:
            os.remove(old)
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.chmod(path + ".tmp", 0o644)
    os.replace(path + ".tmp", path)


def remove_site(name):
    """take a site skill out of local/skills (kept as <name>.bak-…, the last 5) – svc sync then removes Claude's copy"""
    _central_check("skills", name)
    if name not in tclib.site_skills(STACK_DIR):
        raise ValueError("unknown site skill")
    base = os.path.join(STACK_DIR, "local", "skills")
    bak = os.path.join(base, f"{name}.bak-{time.strftime('%Y-%m-%d-%H%M%S')}")
    while os.path.exists(bak):  # removed twice in one second
        bak += "-1"
    os.rename(os.path.join(base, name), bak)
    for old in sorted(d for d in os.listdir(base) if d.startswith(f"{name}.bak-"))[:-5]:
        shutil.rmtree(os.path.join(base, old), ignore_errors=True)


def listing():
    s = load()
    site = tclib.site_skills(STACK_DIR)
    rows = []
    for name, mode in dict(tclib.skill_overrides(s), **{n: tclib.skill_mode(s, n) for n in site}).items():
        k = s["known"].get(name, {})
        if name in site:  # its own file says best what it is for
            k = dict(k, description=site[name]["description"] or k.get("description"), source=k.get("source") or "site")
        rows.append(dict(k, name=name, mode=mode, default="off" if name in tclib.SKILLS_OFF_DEFAULT else "on",
                         custom=name in s["modes"], seen=k.get("seen") or (name in site), required=name in tclib.SKILLS_REQUIRED,
                         site=name in site, text=site_text(name) if name in site else ""))
    order = {"on": 0, "user-invocable-only": 1, "off": 2}
    return sorted(rows, key=lambda r: (order[r["mode"]], r["name"]))


def _central_check(section, key):
    """refuse a change the central server enforces (central management)"""
    from .central import check_write
    check_write(section, key)
