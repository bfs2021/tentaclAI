import os

STACK_DIR = os.environ.get("STACK_DIR", "/opt/tentaclai")
ENV_FILE = os.path.join(STACK_DIR, ".env")
SERVICES_DIR = os.path.join(STACK_DIR, "services")
SYSTEMS_DIR = os.path.join(STACK_DIR, "systems")
SSH_DIR = os.path.join(STACK_DIR, "ssh")
HOME_DIR = os.path.join(STACK_DIR, "data", "home")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
CLAUDE_CONTAINER = os.environ.get("CLAUDE_CONTAINER", "tentaclai")
CLAUDE_SERVICE = os.environ.get("CLAUDE_SERVICE", "claude")
COMPOSE = ["docker", "compose", "--project-directory", STACK_DIR]

# .env keys that hold secrets: never sent to the browser
SECRET_HINTS = ("TOKEN", "KEY", "PASSWORD", "SECRET")


def is_secret(key: str) -> bool:
    return any(h in key.upper() for h in SECRET_HINTS)


# Structured settings page (everything else shows up under "Weitere Variablen").
# (key, label, help, kind) – kind: text | bool | readonly
SETTINGS_GROUPS = [
    ("General", [
        ("TC_HOSTNAME", "Host name", "how Claude refers to the host tentaclAI runs on", "text"),
        ("TC_DOMAIN", "Internal domain", "prefills templates, e.g. n8n.<domain>", "text"),
        ("CONTROL_URL", "tentacontrol address", "for the setup link and help, e.g. https://tentacontrol.example.lan", "text"),
        ("TZ", "Time zone", "e.g. Europe/Vienna", "text"),
        ("CLAUDE_CODE_VERSION", "Claude Code version", "latest or a fixed version – applied with the next update", "text"),
        ("GIT_USER_NAME", "Git name", "for commits in /workspace", "text"),
        ("GIT_USER_EMAIL", "Git email", "", "text"),
    ]),
    ("Remote control", [
        ("RC_ENABLED", "Remote control enabled", "on/off – applied with “Restart” on the overview", "bool"),
        ("RC_NAME", "Session name", "how the session appears in claude.ai/code and the app", "text"),
        ("RC_WORKDIR", "Start directory", "e.g. /workspace or the stacks directory of a local docker host", "text"),
        ("RC_EXTRA_ARGS", "Extra arguments", "appended to “claude remote-control”", "text"),
        ("RC_RESTART_DELAY", "Restart delay (s)", "wait time before an ended session restarts (default 30)", "text"),
    ]),
    ("Paths & mounts", [
        ("TC_DIR", "Stack directory", "set by ./tentaclai.sh init", "readonly"),
        ("DOCKER_SOCKET", "Docker socket of this host", "only for tentacontrol itself (runs tentaclai); Claude's Docker access comes from docker services", "text"),
        ("HOST_DOCS", "Host documentation", "optional: Markdown file about the host, imported into Claude's instructions", "text"),
        ("CA_CERT", "Own root CA", "optional: PEM file for internal https services", "text"),
    ]),
    ("Updates", [
        ("UPDATE_CHECK", "Check for new releases", "asks the GitHub API every 6 hours (no token, no data sent) – off: no requests", "bool"),
        ("UPDATE_REPO", "Release repository", "owner/repo whose releases are checked – default mwaldegg/tentaclai (change for a fork)", "text"),
    ]),
    ("Resources", [
        ("CLAUDE_CPUS", "CPU limit", "number of cores for tentaclai", "text"),
        ("CLAUDE_MEMORY", "Memory limit", "e.g. 8g", "text"),
    ]),
]

# read at container start (not by svc) -> claude container must be recreated
RESTART_VARS = {"TZ", "TC_HOSTNAME", "CONTROL_URL", "GIT_USER_NAME", "GIT_USER_EMAIL", "GH_TOKEN",
                "ANTHROPIC_API_KEY", "HOST_DOCS", "CA_CERT", "CLAUDE_CPUS", "CLAUDE_MEMORY"}
# RC_* are read by rc-loop on every restart -> "Restart" of remote control is enough
RC_VARS = {"RC_ENABLED", "RC_NAME", "RC_WORKDIR", "RC_EXTRA_ARGS", "RC_RESTART_DELAY"}


def site():
    """non-secret site values for templates/pages"""
    from .envfile import read_env
    env = read_env()
    return {
        "hostname": env.get("TC_HOSTNAME") or "this host",
        "domain": env.get("TC_DOMAIN") or "example.lan",
        "control_url": env.get("CONTROL_URL") or "",
        "tc_dir": env.get("TC_DIR") or STACK_DIR,
    }
