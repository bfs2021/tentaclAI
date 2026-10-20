"""Settings → Models: model and effort per task – default (every session, terminal and Remote Control), subagents,
and the guided commands /inventory and /onboard.

Stored in data/control/models.json; written into Claude's ~/.claude/settings.json (model, effortLevel,
env.CLAUDE_CODE_SUBAGENT_MODEL) and the frontmatter of the two commands – by the entrypoint on every start and by
apply() right away. Values and defaults come from tclib (shared with the entrypoint).
"""
import json
import os

import tclib

from . import dockerops
from .config import DATA_DIR

STATE = os.path.join(DATA_DIR, "models.json")
APPLY = r"""
import json, os, sys
sys.path.insert(0, "/usr/local/lib/tentaclai")
import tclib
m = json.loads(sys.argv[1])
p = os.path.expanduser("~/.claude/settings.json")
try:
    s = json.load(open(p))
except (OSError, ValueError):
    s = {}
tclib.apply_models(s, m)
open(p + ".tmp", "w").write(json.dumps(s, indent=2) + "\n")
os.replace(p + ".tmp", p)
tclib.write_model_commands(m)
"""
TASKS = [  # key, effort key, title, help
    ("main", "effort", "Default", "Every Claude session in the sandbox – terminal and Remote Control."),
    ("subagents", None, "Subagents", "Explore, Plan and other subagents Claude starts for searches and side tasks."),
    ("commands", "commands_effort", "/inventory and /onboard", "The guided set-up commands (the turn that runs the command)."),
]


def load():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def current():
    return tclib.models(load())


def apply(m=None):
    """write into the running sandbox (new Claude sessions pick it up)"""
    code, _ = dockerops.run(["python3", "-c", APPLY, json.dumps(m or current())], user=dockerops.CLAUDE_USER)
    return code == 0


def set_values(form):
    """form values → validated state; a select of "custom" takes the text field <key>_custom"""
    new = {}
    for key, effort_key, _, _ in TASKS:
        v = (form.get(key) or "").strip()
        if v == "custom":
            v = (form.get(f"{key}_custom") or "").strip().lower()
        if not tclib.valid_model(v):
            raise ValueError(f"{key}: not a model alias or id")
        new[key] = v
        if effort_key:
            e = (form.get(effort_key) or "").strip()
            if e not in ("",) + tclib.EFFORTS:
                raise ValueError(f"{effort_key}: one of {', '.join(tclib.EFFORTS)}")
            new[effort_key] = e
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE + ".tmp", "w") as f:
        json.dump(new, f, indent=1)
    os.replace(STATE + ".tmp", STATE)
    return apply(tclib.models(new))
