"""Fast tests (no Docker): tclib, parsers, connector/skill logic and every tentacontrol page with dummy data.

The app reads STACK_DIR/DATA_DIR at import, so a fixture stack is created before anything from control/app is imported.
Docker is replaced by fake answers of `dockerops.run` (the commands tentacontrol runs in the sandbox).
"""
import json
import os
import shutil
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
sys.path[:0] = [os.path.join(ROOT, "lib"), os.path.join(ROOT, "control")]

STACK = tempfile.mkdtemp(prefix="tcquick-")
os.environ["TZ"] = "UTC"  # session timestamps and the usage page use the same day
time.tzset()
os.environ.update(STACK_DIR=STACK, DATA_DIR=os.path.join(STACK, "data", "control"), VENDOR_DIR=os.path.join(STACK, "vendor"),
                  CLAUDE_CONTAINER="tcquick-none")


def _stack():
    for d in ("services/_templates", "systems", "ssh/config.d", "data/control", "data/claude/.claude/projects/-workspace", "vendor", "local"):
        os.makedirs(os.path.join(STACK, d), exist_ok=True)
    with open(os.path.join(STACK, ".env"), "w") as f:
        f.write("TC_DIR=%s\nTC_HOSTNAME=testhost\nTC_DOMAIN=site.test\nAPI_TOKEN=secret-123\nRC_ENABLED=true\nNOTIFY_URL_PHONE=ntfy://x/y\nODD_ACCESS=visible-if-broken-123\n" % STACK)
    services = {
        "demoapi": {"description": "Demo API", "system": "box", "api": {"base_url": "https://api.example.test", "auth": {"type": "bearer", "token": "${API_TOKEN}"}},
                    "mcp": {"url": "https://api.example.test/mcp"}, "notes": "read only", "examples": ["svc demoapi GET /items"]},
        "demodb": {"db": {"engine": "postgres", "host": "db.example.test", "database": "x", "user": "u", "password": "${DB_PW}", "access": "ro"}},
        "demohost": {"system": "box", "docker": {"mode": "ssh", "access": "confirm", "stacks_dir": "/srv/stacks"},
                     "files": {"root": "/srv", "access": "ro"}, "web": {"base_url": "https://wiki.example.test"}},
        "credsvc": {"api": {"base_url": "https://c.example.test", "auth": {"type": "header", "header": "X-Key", "value": "${ODD_ACCESS}"}}},
        "demogit": {"git": {"url": "git@git.example.test:me/repo.git", "access": "rw"}, "enabled": False},
    }
    for name, svc in services.items():
        with open(os.path.join(STACK, "services", f"{name}.json"), "w") as f:
            json.dump(svc, f)
    with open(os.path.join(STACK, "services", "_templates", "n8n.json"), "w") as f:
        json.dump({"_meta": {"title": "n8n", "category": "Automation", "setup": "token"}, "api": {"base_url": "https://n8n.{{DOMAIN}}"}}, f)
    with open(os.path.join(STACK, "services", "_templates", "demoapi.json"), "w") as f:
        json.dump({"_meta": {"title": "Demo"}, "api": {"base_url": "https://x"}, "test_path": "/items?limit=1"}, f)
    with open(os.path.join(STACK, "services", "_templates", "tokapi.json"), "w") as f:
        json.dump({"api": {"base_url": "https://tok.{{DOMAIN}}", "auth": {"type": "basic", "user": "${TOKAPI_KEY}", "password": "${TOKAPI_SECRET}"}}}, f)
    with open(os.path.join(STACK, "services", "_templates", "tokgit.json"), "w") as f:
        json.dump({"git": {"url": "https://git.{{DOMAIN}}/r.git", "auth": {"type": "token", "token": "${TOKAPI_KEY}"}}}, f)
    with open(os.path.join(STACK, "systems", "box.json"), "w") as f:
        json.dump({"description": "Demo server", "ssh_host": "box", "rules": "ask before restarts"}, f)
    with open(os.path.join(STACK, "ssh", "config.d", "box.conf"), "w") as f:
        f.write("Host box\n    HostName box.example.test\n    User root\n    # tentaclai: sudo=sudo -n\n    # tentaclai: access=rw\n")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    reply = {"type": "assistant", "timestamp": now, "sessionId": "s1", "entrypoint": "cli",
             "message": {"id": "m1", "model": "claude-opus-5-5", "role": "assistant", "content": [{"type": "text", "text": "hi"}],
                         "usage": {"input_tokens": 2, "cache_read_input_tokens": 24000, "cache_creation_input_tokens": 20000, "output_tokens": 100}}}
    rows = [{"type": "user", "message": {"role": "user", "content": "hello"}},
            {"type": "attachment", "attachment": {"type": "skill_listing", "content":
                "- inventory: System inventory – scan hosts\n- dataviz: Use this skill for charts\n"
                "- anthropic-skills:pdf: Use this skill whenever the user wants to do anything with PDF files\n- claude-api: Reference for the Claude API"}},
            reply, reply,  # streamed replies are written more than once
            {"type": "ai-title", "aiTitle": "Demo session", "sessionId": "s1"}]
    with open(os.path.join(STACK, "data/claude/.claude/projects/-workspace/s1.jsonl"), "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)


_stack()


def fake_out(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


ANSWERS = {  # first matching word of the command → (exit code, output)
    "--version": (0, "2.5.0 (Claude Code)\n"),
    "auth": (0, json.dumps({"loggedIn": True, "email": "someone@example.test", "subscriptionType": "max"})),
    "ls": (0, "claude-rc|0|1760000000\nclaude-workspace|1|1760000500\n"),
    "capture-pane": (0, "▶ claude remote-control --name test\nhttps://claude.ai/code/session_x\nCapacity: 1/32\nConnected\n"),
    "/context": (0, fake_out("context.txt")),
    "/usage": (0, fake_out("usage.txt")),
    "mcp": (0, fake_out("mcp-list.txt")),
}


def fake_run(cmd, workdir=None, user="root"):
    for key, ans in ANSWERS.items():
        if key in cmd:
            return ans
    return 0, ""


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(STACK, ignore_errors=True)  # the dummy stack lives in /tmp – one per run


@pytest.fixture(scope="session")
def app_module():
    from app import dockerops
    dockerops.run = fake_run
    dockerops.container = lambda: None
    from app import main
    return main


RUNNING = {"exists": True, "running": True, "state": "running", "uptime": "2 hours", "created_ts": 0, "image": "local/tentaclai:latest",
           "image_age": "1 day", "health": None, "mounts": []}


@pytest.fixture(scope="session")
def logged_in(app_module):
    """one login for all page tests (bcrypt is slow on purpose)"""
    from fastapi.testclient import TestClient
    from app import auth
    if not auth.has_password():
        auth.set_password("testpass123")
    c = TestClient(app_module.app)  # no `with`: the background loops (notifications, usage) don't start
    r = c.post("/login", data={"password": "testpass123"}, follow_redirects=False)
    assert r.status_code in (200, 303), r.text
    return c


@pytest.fixture(params=["stopped", "running"])
def client(request, logged_in, monkeypatch):
    """logged-in TestClient; 'running' pretends the sandbox runs (overview, RC, usage cards render their full state)"""
    from app import dockerops
    if request.param == "running":
        monkeypatch.setattr(dockerops, "status", lambda: dict(RUNNING))
        monkeypatch.setattr(dockerops, "stats", lambda: {"cpu": 3.5, "mem_mb": 512, "mem_limit_mb": 8192, "mem_pct": 6.2})
    return logged_in
