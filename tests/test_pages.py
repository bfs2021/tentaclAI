"""Every tentacontrol page renders (status 200, no template error) with the dummy stack – sandbox stopped and running."""
import pytest

# every GET route without parameters is a page to render – new pages are covered without touching this file
NOT_PAGES = ("/login", "/setup", "/logout", "/healthz", "/api/", "/internal/", "/logs/stream", "/jobs/", "/static", "/vendor",
             "/topology/ssh-status")  # (ssh-status runs real SSH checks)
# pages with parameters or variants, rendered with the dummy stack's objects
VARIANTS = ["/services/demoapi", "/services/demodb", "/services/demohost", "/services/demogit", "/systems/box", "/ssh/box/edit",
            "/ssh/box/remove", "/network?tab=allow", "/network?tab=block"]


def pages():
    import sys
    from starlette.routing import Route
    sys.modules.get("app.main") or __import__("conftest")  # the dummy stack exists before the app is imported
    from app import main
    plain = [r.path for r in main.app.routes if isinstance(r, Route) and "GET" in (r.methods or ()) and "{" not in r.path
             and not r.path.startswith(NOT_PAGES)]
    return sorted(set(plain)) + VARIANTS


@pytest.mark.parametrize("path", pages())
def test_page_renders(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:300]}"
    if "<html" in r.text:  # full page (dialogs and partials are fragments)
        assert "</html>" in r.text


def test_page_discovery_finds_the_known_pages():
    found = set(pages())
    assert {"/", "/services", "/settings", "/skills", "/models", "/usage", "/partials/overview", "/topology.json"} <= found


def test_login_required(app_module):
    from fastapi.testclient import TestClient
    r = TestClient(app_module.app).get("/skills", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] in ("/login", "/setup")


def test_skills_mode_post(client):
    r = client.post("/skills/mode", data={"name": "dataviz", "mode": "bogus"}, headers={"HX-Request": "true"})
    assert "Not saved" in r.headers.get("HX-Trigger", "")


def test_models_post_refuses_bad_values(client):
    r = client.post("/models", data={"main": "custom", "main_custom": "x y"}, headers={"HX-Request": "true"})
    assert "Not saved" in r.headers.get("HX-Trigger", "")


def test_lifespan_runs_every_startup_hook(app_module, monkeypatch):
    from fastapi.testclient import TestClient
    ran = []
    for name in ("prepare_activity", "start_notifications", "start_update_check", "announce_setup"):
        async def hook(name=name):
            ran.append(name)
        monkeypatch.setattr(app_module, name, hook)
    with TestClient(app_module.app):
        pass
    assert ran == ["prepare_activity", "start_notifications", "start_update_check", "announce_setup"]


def test_skills_page_offers_no_off_for_required(client):
    import re
    html = client.get("/skills").text
    assert "required by tentaclAI" in html
    row = re.search(r'aria-label="Mode of inventory".*?</select>', html, re.S).group(0)
    assert 'value="off"' not in row and 'value="user-invocable-only"' in row


def test_service_without_test_path_warns_and_suggests(client):
    html = client.get("/services/demoapi").text
    assert "no test path" in html and 'value="/items?limit=1"' in html  # suggestion from the template of the same name


def test_save_test_path(client, app_module):
    import json
    from app import services_store
    before = services_store.read_raw("demoapi")
    try:
        r = client.post("/services/demoapi/test-path", data={"path": "/items?limit=1"}, headers={"HX-Request": "true"})
        assert r.headers.get("HX-Refresh") == "true"
        assert json.loads(services_store.read_raw("demoapi"))["test_path"] == "/items?limit=1"
        assert "no test path" not in client.get("/services/demoapi").text
        r = client.post("/services/demoapi/test-path", data={"path": "items; rm"}, headers={"HX-Request": "true"})
        assert "Not saved" in r.headers.get("HX-Trigger", "")
    finally:
        services_store.save_raw("demoapi", before)


def test_rename_route(client, app_module):
    import json
    from app import services_store
    before = services_store.read_raw("demodb")
    try:
        r = client.post("/services/demodb/rename", data={"new": "demodb2"}, headers={"HX-Request": "true"})
        assert r.headers.get("HX-Redirect") == "/services/demodb2"
        assert json.loads(services_store.read_raw("demodb2"))["name"] == "demodb2"
        assert client.get("/services/demodb2").status_code == 200
    finally:
        try:
            services_store.delete("demodb2")
        except FileNotFoundError:
            pass
        services_store.save_raw("demodb", before)


def test_rename_git_service_needs_running_sandbox(client, app_module):
    from app import dockerops, services_store
    if dockerops.status().get("running"):
        return  # the "running" variant would move files in a sandbox – only the refusal is tested here
    r = client.post("/services/demogit/rename", data={"new": "demogit2"}, headers={"HX-Request": "true"})
    assert "start tentaclAI first" in r.headers.get("HX-Trigger", "")
    services_store.read_raw("demogit")  # still there


def test_template_placeholder_domain(client, app_module):
    import json
    from app import services_store
    try:
        services_store.create("fromtpl", "n8n")
        assert json.loads(services_store.read_raw("fromtpl"))["api"]["base_url"] == "https://n8n.example.com"  # not the site's domain
        assert "placeholder address" in client.get("/services/fromtpl").text
        assert "placeholder address" not in client.get("/services/demoapi").text
    finally:
        services_store.delete("fromtpl")


def test_shared_variable_is_shown(client, app_module):
    from app import services_store as s
    try:
        s.create("tokone", "tokapi")
        s.create("tokrepo2", "tokgit")
        html = client.get("/services/tokone").text
        assert "Also used by" in html and 'href="/services/tokrepo2"' in html
    finally:
        for n in ("tokone", "tokrepo2"):
            s.delete(n)


def _as(monkeypatch, who):
    """make the TestClient's address ("testclient") look like the sandbox or the gateway"""
    from app import netguard
    from app.config import CLAUDE_CONTAINER
    name = {"sandbox": CLAUDE_CONTAINER, "egress": netguard.EGRESS_NAME}.get(who)
    monkeypatch.setattr(netguard, "_resolve", lambda n: {"testclient"} if n == name else set())


def test_sandbox_reaches_only_the_svc_endpoints(logged_in, monkeypatch):
    _as(monkeypatch, "sandbox")
    for path in ("/", "/login", "/settings", "/skills", "/internal/egress/policy"):
        assert logged_in.get(path, follow_redirects=False).status_code == 403, path  # even with a valid session
    assert logged_in.post("/internal/egress/ask", json={"host": "evil.test", "port": 443}).status_code == 403
    r = logged_in.get("/internal/approvals")  # reaches the endpoint – which still wants the svc token
    assert r.status_code != 403 or "not from here" not in r.text


def test_gateway_reaches_only_egress_endpoints(logged_in, monkeypatch):
    _as(monkeypatch, "egress")
    assert logged_in.get("/internal/egress/policy").status_code == 200
    assert logged_in.get("/settings", follow_redirects=False).status_code == 403
    assert logged_in.get("/internal/approvals").status_code == 403


def test_others_cant_use_egress_endpoints(logged_in, monkeypatch):
    _as(monkeypatch, "nobody")
    assert logged_in.get("/internal/egress/policy").status_code == 403
    assert logged_in.post("/internal/egress/ask", json={"host": "x.test", "port": 443}).status_code == 403
    assert logged_in.get("/settings").status_code == 200


@pytest.mark.parametrize("version,expect", [(None, "not running"), ("0.0.1", "still runs 0.0.1"), ("same", "applying it in the sandbox failed")])
def test_not_applied_message(logged_in, app_module, monkeypatch, version, expect):
    from app import dockerops, models
    monkeypatch.setattr(dockerops, "sandbox_version", lambda: app_module.TC_VERSION if version == "same" else version)
    monkeypatch.setattr(models, "set_values", lambda form: False)
    r = logged_in.post("/models", data={}, headers={"HX-Request": "true"})
    assert r.headers.get("HX-Refresh") == "true"
    assert expect in logged_in.get("/models").text  # flash message after the refresh


def test_credential_values_never_reach_the_browser(client):
    """ODD_ACCESS has no secret-looking name, but credsvc uses it as a header token"""
    for path in ("/settings", "/services/credsvc"):
        assert "visible-if-broken-123" not in client.get(path).text, path
