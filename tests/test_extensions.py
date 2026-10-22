"""tentaclAI Community without extensions (Reports, Modules, License as placeholders) and how an extension hooks in."""
import sys
import textwrap


def test_community_placeholders(client):
    html = client.get("/license").text
    assert "Currently using tentaclAI Community" in html and "To use Enterprise modules and get support" in html
    assert 'id="instance-id"' in html or "has no ID" in html
    assert "Reports are only available with a valid Enterprise license" in client.get("/reports").text
    html = client.get("/modules").text
    assert "Compliance reports" in html and "SIEM export" in html and "Modules are only available with a valid Enterprise license" in html
    nav = client.get("/settings").text
    assert nav.count('title="Enterprise"') == 1 and 'href="/modules"' in nav  # Modules carries the lock
    assert 'href="/reports"' not in nav  # Reports only shows once a license unlocks it


def test_instance_id_comes_from_the_hardware(app_module, tmp_path, monkeypatch):
    from app import instance
    uuid = tmp_path / "product_uuid"
    monkeypatch.setattr(instance, "DMI_UUID", str(uuid))
    monkeypatch.setattr(instance, "_engine_id", lambda: "ENGINE-1")
    def fresh():
        monkeypatch.setattr(instance, "_instance", None)
        return instance.instance_id()
    uuid.write_text("CA19E780-8A0C-11EE-A162-34D2D6982C00\n")
    iid, kind = fresh()
    assert kind == "hardware" and iid.startswith("TCI-") and len(iid) == 23 and iid == fresh()[0]  # stable
    uuid.write_text("ca19e780-8a0c-11ee-a162-34d2d6982c01")
    assert fresh()[0] != iid  # other hardware, other ID
    uuid.write_text("03000200-0400-0500-0006-000700080009")  # a board's placeholder: Docker's engine ID instead
    assert fresh()[1] == "docker"
    uuid.unlink()
    monkeypatch.setattr(instance, "_engine_id", lambda: "")
    assert fresh() == ("", "")


def test_an_extension_hooks_in(app_module, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.templating import Jinja2Templates
    from jinja2 import DictLoader
    from app import extensions
    pkg = tmp_path / "tcx_demo"
    (pkg / "templates").mkdir(parents=True)
    (pkg / "templates" / "hello.html").write_text("ext page")
    (pkg / "__init__.py").write_text(textwrap.dedent('''
        import os
        from fastapi import APIRouter
        from fastapi.responses import PlainTextResponse
        router = APIRouter()
        @router.get("/license")
        async def lic():
            return PlainTextResponse("from the extension")
        def register(api):
            api.add_router(router)
            api.add_templates(os.path.join(os.path.dirname(__file__), "templates"))
            api.add_features(lambda: {"reports"})
            api.set_edition("enterprise")
    '''))
    (tmp_path / "tcx_broken.py").write_text("def register(api):\n    raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(extensions, "LOADED", [])
    monkeypatch.setattr(extensions, "_features", [])
    monkeypatch.setattr(extensions, "_edition", ["community"])
    monkeypatch.setenv("TC_EXTENSIONS", "tcx_missing, tcx_broken, tcx_demo")
    app, templates = FastAPI(), Jinja2Templates(directory=str(tmp_path))
    templates.env.loader = DictLoader({"hello.html": "own page"})
    extensions.load(app, templates)

    @app.get("/license")  # the app's own page, included after the extension
    async def own():
        return "own"
    assert extensions.LOADED == ["tcx_demo"]  # missing: skipped, broken: reported, not fatal
    assert TestClient(app).get("/license").text == "from the extension"
    assert templates.env.get_template("hello.html").render() == "ext page"
    assert extensions.features() == {"reports"} and extensions.edition() == "enterprise"
    sys.modules.pop("tcx_demo", None)
    sys.modules.pop("tcx_broken", None)
