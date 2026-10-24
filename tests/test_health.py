"""The built-in module Service-Health (control/extensions/tentaclai_health): parsing svc test, rounds, history,
notifications, the page, settings and Export / Import – with the fixture stack's services and a fake svc."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTH = os.path.join(ROOT, "control", "extensions", "tentaclai_health")
NAME = "tentaclai_health"


@pytest.fixture
def health(client, ext, monkeypatch):
    """Service-Health enabled and loaded; svc test answers from `answers`, notifications land in `sent`"""
    from app import extapi
    ext.install(NAME, src=HEALTH, origin="builtin")
    ext.mod.set_enabled(NAME, True)
    ext.reload()
    assert ext.mod.LOADED == [NAME], ext.mod.FAILED
    answers = {"demoapi": "✓ api: GET / → HTTP 200\n", "demohost": "✓ docker: Docker 27\n✗ files: permission denied\n",
               "credsvc": "✗ api: GET / → HTTP 000 timeout\n"}
    sent = []
    monkeypatch.setattr(extapi, "svc", lambda *a: (0, answers.get(a[1], "")))
    monkeypatch.setattr(extapi, "notify", lambda event, title, body="", key=None, link="": sent.append((event, title)))

    class H:
        checks = sys.modules[f"{NAME}.checks"]
    H.answers, H.sent = answers, sent
    return H


def test_parse_svc_test(health):
    checks = health.checks
    assert checks.parse("✓ api: ok\n✓ mcp: ok\n") == ("ok", "✓ api: ok · ✓ mcp: ok")
    assert checks.parse("✓ docker: ok\n✗ files: denied\n") == ("partial", "✗ files: denied")
    assert checks.parse("✗ api: HTTP 500\n") == ("fail", "✗ api: HTTP 500")
    assert checks.parse("container is not running") is None
    assert checks.parse("svc: unknown service x\n") == ("fail", "svc: unknown service x")


def test_rounds_states_and_notifications(client, health):
    c = health.checks
    page = client.get("/x/tentaclai_health/")
    assert page.status_code == 200 and "not checked yet" in page.text
    assert "demodb · token missing" in page.text and "demogit · disabled" in page.text  # not testable
    t = time.time() - 3600
    assert c.run_round(force=True, now=t)
    st = c.state()["services"]
    assert set(st) == {"demoapi", "demohost", "credsvc"}
    assert st["demoapi"]["state"] == "ok" and st["demohost"]["state"] == "partial" and st["credsvc"]["state"] == "fail"
    assert st["demohost"]["detail"] == "✗ files: permission denied"
    assert health.sent == [] and c.down_count() == 0  # one failed round is no outage yet (default: 2)
    assert not c.run_round(now=t + 60)  # not due yet
    assert c.run_round(now=t + 300)
    assert sorted(health.sent) == [("health_down", "credsvc is down"), ("health_down", "demohost: a part fails")]
    assert c.down_count() == 2 and ">2</span>" in client.get("/x/tentaclai_health/count").text
    c.run_round(now=t + 600)
    assert len(health.sent) == 2  # notified once per outage
    health.answers["credsvc"] = "✓ api: GET / → HTTP 200\n"
    c.run_round(now=t + 900)
    assert health.sent[-1] == ("health_up", "credsvc is up again") and c.down_count() == 1
    rows = c.history(t - 1)
    assert len(rows) == 4 and c.availability(rows, "credsvc") == 25.0 and c.availability(rows, "demoapi") == 100.0
    bar = c.bar(rows, "credsvc", t + 900)
    assert len(bar) == c.SLOTS and bar.count("") >= c.SLOTS - 2 and "fail" in bar
    html = client.get("/x/tentaclai_health/").text
    assert ">partly<" in html and ">up<" in html and "75.0 %" not in html.split("credsvc")[0]
    assert '<i class="fail">' in html or '<i class="ok">' in html
    assert ">box<" in html and "Standalone" in html  # per system


def test_sandbox_down_is_no_outage(client, health, monkeypatch):
    from app import extapi
    monkeypatch.setattr(extapi, "svc", lambda *a: (1, "container is not running"))
    assert not health.checks.run_round(force=True)
    assert health.checks.history(0) == [] and health.checks.state()["services"] == {}


def test_settings_and_backup(client, health):
    c = health.checks
    page = client.get("/x/tentaclai_health/settings")
    assert page.status_code == 200 and 'value="credsvc" checked' in page.text and "demodb (token missing)" in page.text
    assert 'href="/x/tentaclai_health/settings"' in client.get("/settings").text  # the Settings tab
    r = client.post("/x/tentaclai_health/settings", data={"interval": "15", "fails": "3", "watch": ["demoapi", "demohost"]})
    assert r.status_code == 200
    s = c.settings()
    assert s == {"interval": 15, "fails": 3, "skip": ["credsvc"]}
    assert [x["name"] for x in c.monitored()[0]] == ["demoapi", "demohost"]
    assert "credsvc · not monitored" in client.get("/x/tentaclai_health/").text
    client.post("/x/tentaclai_health/settings", data={"interval": "7", "fails": "9"})  # not offered → defaults
    assert c.settings()["interval"] == 5 and c.settings()["fails"] == 2
    from app import extensions
    part = extensions.backup_parts()[f"x_{NAME}_settings"]
    part["apply"]({"interval": 30, "fails": 1, "skip": ["demoapi"]})
    assert part["export"]() == {"interval": 30, "fails": 1, "skip": ["demoapi"]}
    part["apply"]("broken")
    assert c.settings() == {"interval": 5, "fails": 2, "skip": []}


def test_history_is_pruned(client, health):
    from app import extapi
    c = health.checks
    old = os.path.join(extapi.data_dir(NAME), "history-2000-01-01.jsonl")
    open(old, "w").write('{"t": 946684800, "r": {"demoapi": "fail"}}\n')
    c.run_round(force=True)
    assert not os.path.exists(old) and len(c.history(0)) == 1


def test_modules_page_and_run_now(client, health):
    html = client.get("/modules").text
    assert "Service-Health" in html and "Built-in" in html and 'href="/x/tentaclai_health/"' in html
    r = client.post("/x/tentaclai_health/run")
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    assert 'id="x-tentaclai_health-about"' in client.get("/help").text
    from app import notify
    assert notify.EVENTS["health_down"][:2] == ("Service down", "high") and "health_up" in notify.EVENTS
