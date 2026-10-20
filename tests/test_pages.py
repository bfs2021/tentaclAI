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
