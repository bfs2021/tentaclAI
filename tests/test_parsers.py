"""Parsers of Claude Code output: /context (skills), /usage (plan limits), claude mcp list (connectors), session files."""
from conftest import fake_out


def test_parse_context_skills_table(app_module):
    from app import skills
    rows = skills.parse_context(fake_out("context.txt"))
    assert rows == {"inventory": {"source": "User", "tokens": 50}, "update-config": {"source": "Built-in", "tokens": 240},
                    "claude-api": {"source": "Built-in", "tokens": 1200}, "anthropic-skills:pdf": {"source": "Plugin", "tokens": 150}}
    assert "mcp__demo__search" not in rows  # other tables of /context are ignored
    assert skills.parse_context("") == {} and skills.parse_context("error: not signed in") == {}


def test_skill_descriptions_from_sessions(app_module):
    from app import skills
    d = skills._descriptions()
    assert d["dataviz"] == "Use this skill for charts"
    assert d["anthropic-skills:pdf"].startswith("Use this skill whenever")  # names with a colon


def test_parse_plan(app_module):
    from app import usage
    p = usage.parse_plan(fake_out("usage.txt"))
    assert [(l["window"], l["scope"], l["percent"]) for l in p["limits"]] == [("session", "", 12), ("week", "all models", 2), ("week", "Opus", 81)]
    assert p["limits"][0]["resets"] == "Oct 21, 2:40am"
    assert p["billing"] == "You are currently using your subscription to power your Claude Code usage"  # not the warning line
    assert (p["period"], p["requests"], p["sessions"]) == ("7d", 116, 11)
    assert p["contrib"][0].startswith("50% of your usage")
    assert usage.parse_plan("")["limits"] == []


def test_connector_lines(app_module):
    from app import connectors
    lines = [connectors.LINE.match(l.strip()) for l in fake_out("mcp-list.txt").splitlines()]
    found = {m.group(1): (m.group(2), m.group(3)) for m in lines if m}
    assert set(found) == {"claude.ai Google Drive", "claude.ai Claude Docs"}  # svc MCP servers are not connectors
    assert found["claude.ai Claude Docs"][1] == "! Needs authentication"


def test_session_usage_counts_each_reply_once(app_module):
    """Claude Code writes streamed replies more than once (same message id) – counted once"""
    from app import usage
    usage.refresh_local()
    usage.refresh_local()  # incremental: nothing read twice
    s = usage.summary()
    assert s["today"]["replies"] == 1 and s["today"]["cache_read"] == 24000
    assert s["sessions"][0]["title"] == "Demo session" and s["sessions"][0]["kind"] == "Terminal"


def test_sessions_without_replies_are_not_listed(app_module):
    import json, os
    from app import usage
    p = os.path.join(usage.PROJECTS, "-workspace", "poll.jsonl")
    with open(p, "w") as f:
        f.write(json.dumps({"type": "user", "sessionId": "poll", "message": {"role": "user", "content": "<command-name>/usage</command-name>"}}) + "\n")
    try:
        usage.refresh_local()
        assert "poll" not in [x["id"] for x in usage.summary()["sessions"]]
    finally:
        os.remove(p)


def test_session_files_skip_symlinks(app_module, tmp_path):
    import os
    from app import usage
    secret = tmp_path / "secret.jsonl"
    secret.write_text('{"aiTitle": "leaked"}\n')
    link_file = os.path.join(usage.PROJECTS, "-workspace", "link.jsonl")
    link_dir = os.path.join(usage.PROJECTS, "-linked")
    os.symlink(secret, link_file)
    os.symlink(tmp_path, link_dir)
    try:
        files = usage.session_files()
        assert files and not any("link" in f for f in files)
    finally:
        os.remove(link_file)
        os.remove(link_dir)
