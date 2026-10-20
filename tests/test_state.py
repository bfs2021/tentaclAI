"""Connector and skill state in tentacontrol: discovery, defaults, toggles and what is written into the sandbox."""
import json

import pytest

import tclib


@pytest.fixture
def applied(app_module, monkeypatch):
    """records what would be written into the sandbox instead of running docker exec"""
    from app import dockerops
    calls = []
    real = dockerops.run

    def run(cmd, workdir=None, user="root"):
        if cmd[:2] == ["python3", "-c"]:
            calls.append(("skills", json.loads(cmd[3]), user))
            return 0, ""
        return real(cmd, workdir, user)

    def sandbox_exec(cmd, stdin=None, timeout=60):
        calls.append(("managed", json.loads(stdin), cmd))
        return 0, ""
    monkeypatch.setattr(dockerops, "run", run)
    monkeypatch.setattr(dockerops, "sandbox_exec", sandbox_exec)
    return calls


def test_connectors_discover_denies_new_ones(applied, app_module):
    from app import connectors
    assert connectors.discover()
    names = {c["name"]: c for c in connectors.listing()}
    assert set(names) == {"claude.ai Google Drive", "claude.ai Claude Docs"}
    assert not any(c["enabled"] for c in names.values())
    kind, managed, cmd = applied[-1]
    assert kind == "managed" and {d["serverName"] for d in managed["deniedMcpServers"]} == set(names)
    assert "chmod 644" in cmd[-1]  # readable for Claude, written by root

    assert connectors.set_enabled("claude.ai Google Drive", True)
    assert [d["serverName"] for d in applied[-1][1]["deniedMcpServers"]] == ["claude.ai Claude Docs"]
    with pytest.raises(ValueError):
        connectors.set_enabled("claude.ai Nope", True)


def test_skills_discover_listing_and_modes(applied, app_module):
    from app import skills
    assert skills.discover()
    rows = {r["name"]: r for r in skills.listing()}
    assert rows["claude-api"]["tokens"] == 1200 and rows["claude-api"]["mode"] == "on"
    assert rows["dataviz"]["mode"] == "off" and rows["dataviz"]["description"] == "Use this skill for charts"
    assert set(tclib.SKILLS_OFF_DEFAULT) <= set(rows)  # defaults are listed although Claude Code doesn't show them

    assert skills.set_mode("claude-api", "user-invocable-only")
    kind, managed, user = applied[-1]
    assert kind == "skills" and user == "claude" and managed["claude-api"] == "user-invocable-only"
    assert skills.set_mode("dataviz", "on") and applied[-1][1]["dataviz"] == "on"
    with pytest.raises(ValueError):
        skills.set_mode("claude-api", "maybe")
    with pytest.raises(ValueError):
        skills.set_mode("never-seen", "off")
    order = [r["mode"] for r in skills.listing()]
    assert order == sorted(order, key=["on", "user-invocable-only", "off"].index)


def test_skills_discover_without_claude(app_module, monkeypatch):
    from app import dockerops, skills
    monkeypatch.setattr(dockerops, "run", lambda *a, **k: (1, "container is not running"))
    assert skills.discover() is False


def test_models_set_and_apply(app_module, monkeypatch):
    from app import dockerops, models
    calls = []
    monkeypatch.setattr(dockerops, "run", lambda cmd, workdir=None, user="root": (calls.append((json.loads(cmd[3]), user)), (0, ""))[1])
    assert models.current() == tclib.MODELS_DEFAULT  # fresh installation: sparing defaults
    assert models.set_values({"main": "custom", "main_custom": "Claude-Opus-5-5", "effort": "high", "subagents": "",
                              "commands": "haiku", "commands_effort": ""})
    applied, user = calls[-1]
    assert user == "claude"
    assert applied == {"main": "claude-opus-5-5", "effort": "high", "subagents": "", "commands": "haiku", "commands_effort": ""}
    assert models.current() == applied
    for bad in ({"main": "custom", "main_custom": "opus; rm -rf /"}, {"effort": "max"}, {"commands_effort": "ultra"}):
        with pytest.raises(ValueError):
            models.set_values(bad)
    assert models.current() == applied  # refused input changes nothing


def test_polling_leaves_no_session_files(app_module, monkeypatch):
    """/usage and /context are asked every few minutes – they must not pile up as sessions in Claude's history"""
    from app import dockerops, skills, usage
    from conftest import fake_run
    cmds = []
    monkeypatch.setattr(dockerops, "run", lambda cmd, workdir=None, user="root": (cmds.append(cmd), fake_run(cmd))[1])
    usage.refresh_plan()
    skills.discover()
    claude = [c for c in cmds if "claude" in c and "-p" in c]
    assert len(claude) == 2 and all("--no-session-persistence" in c for c in claude)


def test_discover_claude_runs_connectors_and_skills(app_module, monkeypatch):
    import asyncio
    from app import connectors, skills
    ran = []
    monkeypatch.setattr(connectors, "discover", lambda: ran.append("connectors"))
    monkeypatch.setattr(skills, "discover", lambda: ran.append("skills"))
    asyncio.run(app_module.discover_claude())
    assert ran == ["connectors", "skills"]


def test_required_skills_refuse_off(applied, app_module):
    from app import skills
    with pytest.raises(ValueError, match="required"):
        skills.set_mode("inventory", "off")
    assert skills.set_mode("onboard", "user-invocable-only")
    rows = {r["name"]: r for r in skills.listing()}
    assert rows["inventory"]["required"] and rows["onboard"]["mode"] == "user-invocable-only"
    assert skills.set_mode("onboard", "on")


def test_test_path_helpers(app_module):
    from app import services_store
    assert services_store.template_test_path("demoapi") == "/items?limit=1"
    assert services_store.template_test_path("nope") == "" and services_store.template_test_path("../x") == ""
    for bad in ("", "items", "/a b", "/x'", '/x"'):
        assert not services_store.valid_test_path(bad)
    assert services_store.valid_test_path("/environments")


def test_service_rename(app_module):
    from app import services_store as s
    s.save_raw("renme", json.dumps({"api": {"base_url": "https://x"}, "examples": ["svc renme GET /a", "svc db renme tables", "svc renmex GET /"],
                                    "notes": ["use svc renme carefully"]}))
    try:
        data = s.rename("renme", "renamed")
        assert data["examples"] == ["svc renamed GET /a", "svc db renamed tables", "svc renmex GET /"]  # other names untouched
        assert data["notes"] == ["use svc renamed carefully"]
        assert json.loads(s.read_raw("renamed"))["name"] == "renamed"
        with pytest.raises(FileNotFoundError):
            s.read_raw("renme")
        for bad in ("Renamed", "a b", "../x", "", "renamed", "demoapi"):
            with pytest.raises(ValueError):
                s.rename("renamed", bad)
    finally:
        for n in ("renme", "renamed"):
            try:
                s.delete(n)
            except (FileNotFoundError, ValueError):
                pass


def test_second_service_from_a_template_gets_own_variables(app_module):
    from app import services_store as s
    names = ("tok1", "tok2", "tok-three", "tokrepo")
    try:
        s.create("tok1", "tokapi")
        s.create("tok2", "tokapi")
        s.create("tok-three", "tokapi")
        s.create("tokrepo", "tokgit")
        auth = lambda n: json.loads(s.read_raw(n))["api"]["auth"]
        assert auth("tok1") == {"type": "basic", "user": "${TOKAPI_KEY}", "password": "${TOKAPI_SECRET}"}  # first keeps the names
        assert auth("tok2") == {"type": "basic", "user": "${TOK2_KEY}", "password": "${TOK2_SECRET}"}
        assert auth("tok-three")["user"] == "${TOK_THREE_KEY}"
        assert json.loads(s.read_raw("tokrepo"))["git"]["auth"]["token"] == "${TOKAPI_KEY}"  # other kind: shared on purpose
        assert s.variable_users()["TOKAPI_KEY"] == ["tok1", "tokrepo"]
    finally:
        for n in names:
            try:
                s.delete(n)
            except FileNotFoundError:
                pass
