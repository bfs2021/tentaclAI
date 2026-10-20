"""Every tentacontrol page renders (status 200, no template error) with the dummy stack – sandbox stopped and running."""
import pytest

PAGES = ["/", "/partials/overview", "/services", "/services/demoapi", "/services/demodb", "/services/demohost", "/services/demogit",
         "/systems", "/systems/box", "/ssh", "/ssh/new", "/ssh/box/edit", "/ssh/box/remove", "/network", "/network?tab=allow",
         "/network?tab=block", "/topology", "/topology.json", "/activity", "/partials/approvals", "/partials/approval-count",
         "/terminal", "/help", "/settings", "/notifications", "/skills", "/models", "/usage", "/logs", "/updates", "/partials/versions"]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:300]}"
    if "<html" in r.text:  # full page (dialogs and partials are fragments)
        assert "</html>" in r.text


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
