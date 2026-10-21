"""Claude → Jobs: schedules, the claude command line per profile, reading the answer, when to notify."""
import json
import time

import pytest


@pytest.fixture
def sj(app_module):
    from app import scheduled
    return scheduled


def lt(ts):
    return time.localtime(ts)


def at(y, mo, d, h=0, mi=0):
    return int(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))


def test_daily_and_weekly(sj):
    t = sj.next_run({"kind": "daily", "time": "07:00"}, at(2026, 10, 21, 8, 0))
    assert (lt(t).tm_mday, lt(t).tm_hour, lt(t).tm_min) == (22, 7, 0)
    t = sj.next_run({"kind": "daily", "time": "07:00"}, at(2026, 10, 21, 6, 59))
    assert (lt(t).tm_mday, lt(t).tm_hour) == (21, 7)
    t = sj.next_run({"kind": "weekly", "day": 0, "time": "07:00"}, at(2026, 10, 21, 8, 0))  # 21 Oct 2026 is a Wednesday
    assert lt(t).tm_wday == 0 and lt(t).tm_mday == 26


def test_hourly_and_every(sj):
    t = sj.next_run({"kind": "hourly", "minute": 15}, at(2026, 10, 21, 8, 20))
    assert (lt(t).tm_hour, lt(t).tm_min) == (9, 15)
    now = at(2026, 10, 21, 8, 0)
    assert sj.next_run({"kind": "every", "hours": 6}, now, now) == now + 6 * 3600
    assert sj.next_run({"kind": "every", "hours": 6}, now) == now + 60  # never ran: right away


@pytest.mark.parametrize("expr,after,expect", [
    ("0 7 * * 1-5", at(2026, 10, 23, 8), (26, 7, 0)),      # Fri after 07:00 → Mon
    ("*/15 * * * *", at(2026, 10, 21, 8, 1), (21, 8, 15)),
    ("30 2 1 * *", at(2026, 10, 21), (1, 2, 30)),          # first of next month
    ("0 9 * * 0", at(2026, 10, 21), (25, 9, 0)),           # Sunday
    ("0 9 * * 7", at(2026, 10, 21), (25, 9, 0)),           # 7 = Sunday too
])
def test_cron(sj, expr, after, expect):
    t = sj.next_run({"kind": "cron", "expr": expr}, after)
    assert (lt(t).tm_mday, lt(t).tm_hour, lt(t).tm_min) == expect


@pytest.mark.parametrize("form,msg", [
    ({"kind": "cron", "expr": "0 7 * *"}, "five fields"), ({"kind": "cron", "expr": "61 7 * * *"}, "out of range"),
    ({"kind": "daily", "time": "25:00"}, "HH:MM"), ({"kind": "every", "hours": "0"}, "1–168"), ({"kind": "x"}, "unknown"),
])
def test_bad_schedules(sj, form, msg):
    with pytest.raises(ValueError, match=msg):
        sj.clean_schedule(form)


def test_describe(sj):
    assert sj.describe({"kind": "weekly", "day": 0, "time": "07:00"}) == "every Mon at 07:00"
    assert sj.describe({"kind": "every", "hours": 6}) == "every 6 h"


def test_read_only_profile_is_locked_down(sj):
    argv = sj.claude_argv({"name": "x", "prompt": "check disks", "profile": "read-only"}, "run1", "sid")
    assert argv[:3] == ["claude", "-p", "check disks"]  # prompt before the multi-value tool options
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Bash(svc:*)" in allowed and "Edit" not in allowed and "Write" not in allowed
    assert "Edit" in argv[argv.index("--disallowedTools") + 1]
    assert json.loads(argv[argv.index("--json-schema") + 1])["properties"]["status"]["enum"] == ["ok", "warn", "alert"]


def test_standard_profile(sj):
    argv = sj.claude_argv({"name": "x", "prompt": "p", "profile": "standard", "model": "haiku"}, "run1", "sid")
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions" and "--allowedTools" not in argv
    assert argv[argv.index("--model") + 1] == "haiku"


def test_parse_output(sj):
    ok = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "num_turns": 3,
                     "permission_denials": [{}], "structured_output": {"status": "warn", "title": "t", "summary": "s"}})
    st, f = sj.parse_output("noise\n" + ok)
    assert st == "done" and f["result"]["status"] == "warn" and f["denials"] == 1 and f["cost"] == 0.01
    err = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "too long"})
    assert sj.parse_output(err)[0] == "failed"
    assert sj.parse_output("Error: not logged in")[0] == "failed"
    plain = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "just text"})
    st, f = sj.parse_output(plain)
    assert st == "done" and f["result"]["status"] == "warn"  # no traffic light → better look at it


@pytest.mark.parametrize("rule,status,light,expect", [
    ("findings", "done", "ok", False), ("findings", "done", "warn", True), ("findings", "done", "alert", True),
    ("always", "done", "ok", True), ("failure", "done", "alert", False), ("failure", "failed", None, True),
    ("findings", "timeout", None, True), ("always", "canceled", None, False),
])
def test_should_notify(sj, rule, status, light, expect):
    assert sj.should_notify({"notify": rule}, {"status": status, "result": {"status": light}}) is expect


def test_save_due_and_catch_up(sj, monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "JOBS", str(tmp_path / "jobs.json"))
    jid = sj.save({"name": "Disk space", "prompt": "df", "kind": "daily", "time": "07:00"})
    assert jid == "disk-space" and sj.get(jid)["profile"] == "read-only" and sj.get(jid)["notify"] == "findings"
    assert sj.save({"name": "Disk space", "prompt": "df", "kind": "daily", "time": "07:00"}) == "disk-space-2"
    jobs = sj.load()
    jobs[jid]["next"] = time.time() - 60
    jobs["disk-space-2"]["next"] = time.time() - 7 * 3600  # missed while tentaclAI was down for too long
    from app import jsonstore
    jsonstore.save(sj.JOBS, jobs)
    got = dict(sj.due())
    assert got[jid] == "schedule" and got["disk-space-2"].startswith("skipped")
    assert sj.due() == []  # moved on
    with pytest.raises(ValueError, match="profile"):
        sj.save({"name": "x", "prompt": "p", "kind": "daily", "time": "07:00", "profile": "root"})


def test_new_events_follow_approval_routing(app_module, tmp_path, monkeypatch):
    from app import notify
    cfg = tmp_path / "notify.json"
    cfg.write_text(json.dumps({"channels": [{"id": "phone"}], "routes": {"approval": ["phone"], "update": []}}))
    monkeypatch.setattr(notify, "CONFIG", str(cfg))
    r = notify.load()["routes"]
    assert r["job_result"] == ["phone"] and r["job_failed"] == ["phone"] and r["update"] == []  # a set choice stays


def test_clone_starts_paused(sj, monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "JOBS", str(tmp_path / "jobs.json"))
    jid = sj.save({"name": "Disk space", "prompt": "df", "kind": "daily", "time": "07:00", "profile": "standard"})
    c = sj.clone(jid)
    assert c == "disk-space-copy" and sj.clone(jid) == "disk-space-copy-2"
    j = sj.get(c)
    assert j["name"] == "Disk space (copy)" and j["enabled"] is False and j["profile"] == "standard" and j["prompt"] == "df"
    assert sj.get(jid)["enabled"] is True


KNOWN = {"mailrelay": {"kind": "service", "parts": {"smtp": "rw"}, "enabled": True, "description": "relay"},
         "imap_x": {"kind": "service", "parts": {"imap": "ro"}, "enabled": True, "description": ""},
         "slowmail": {"kind": "service", "parts": {"smtp": "confirm"}, "enabled": True, "description": ""},
         "n8n": {"kind": "service", "parts": {"api": "rw", "mcp": "tools"}, "enabled": False, "description": ""},
         "nas": {"kind": "ssh", "parts": {"ssh": "rw"}, "enabled": True, "description": "SSH host nas"}}


def test_mentions(sj):
    assert sj.mentions("send via @mailrelay, check @imap_x; mail me@example.com and @mailrelay again") == ["mailrelay", "imap_x"]


def test_check_mentions(sj):
    err, warn = sj.check_mentions({"prompt": "use @nope and @n8n", "profile": "standard", "scope": True}, KNOWN)
    assert err == ["@nope is no service or SSH host"]
    assert any("@n8n is disabled" in w for w in warn) and any("@n8n: MCP tools are off" in w for w in warn)
    _, warn = sj.check_mentions({"prompt": "send with @mailrelay", "profile": "read-only"}, KNOWN)
    assert warn == ["@mailrelay can't send mail in a read-only job"]
    _, warn = sj.check_mentions({"prompt": "send with @slowmail", "profile": "standard"}, KNOWN)
    assert "asks for approval (smtp)" in warn[0]
    err, _ = sj.check_mentions({"prompt": "no names", "profile": "standard", "scope": True}, KNOWN)
    assert "needs at least one @name" in err[0]


def test_context_scope_and_strict_mcp(sj, monkeypatch):
    monkeypatch.setattr(sj, "tentacles", lambda: KNOWN)
    job = {"name": "mail test", "prompt": "send via @mailrelay, check @imap_x", "profile": "standard", "scope": True}
    argv = sj.claude_argv(job, "run1", "sid")
    system = argv[argv.index("--append-system-prompt") + 1]
    assert "@mailrelay = service mailrelay (smtp rw) – relay" in system and "svc refuses every other one" in system
    assert "--strict-mcp-config" in argv
    assert "--strict-mcp-config" not in sj.claude_argv(dict(job, scope=False), "run1", "sid")
    assert "--strict-mcp-config" in sj.claude_argv(dict(job, scope=False, profile="read-only"), "run1", "sid")


def test_registry_carries_the_allow_list(sj, monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "ACTIVE", str(tmp_path / "runs.json"))
    sj._register("r1", {"name": "a", "profile": "standard", "scope": True, "prompt": "@mailrelay @nas"})
    sj._register("r2", {"name": "b", "profile": "standard", "prompt": "@mailrelay"})
    act = json.load(open(tmp_path / "runs.json"))
    assert act["r1"]["allow"] == ["mailrelay", "nas"] and "allow" not in act["r2"]


def test_state_token_changes(sj, monkeypatch, tmp_path):
    from app import jsonstore
    monkeypatch.setattr(sj, "HISTORY", str(tmp_path / "h.json"))
    monkeypatch.setattr(sj, "JOBS", str(tmp_path / "j.json"))
    jsonstore.save(sj.HISTORY, [{"id": "a", "status": "running"}])
    t1 = sj.state_token()
    jsonstore.save(sj.HISTORY, [{"id": "a", "status": "done"}])
    assert sj.state_token() != t1
