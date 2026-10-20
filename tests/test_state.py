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
