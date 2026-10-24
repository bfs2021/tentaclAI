"""Extension API v1: tentaclAI Community without extensions, discovery, enabling, the rules per origin, the example
extension (examples/extensions/tcx_hello) and the audit follower."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELLO = os.path.join(ROOT, "examples", "extensions", "tcx_hello")


def test_community_placeholders(client):
    html = client.get("/license").text
    assert "needs no license" in html and 'href="/central"' in html  # Enterprise = the central, licensed there
    assert 'id="instance-id"' in html or "has no ID" in html
    assert client.get("/reports").status_code == 404  # no Reports in Community – a module brings its own entry
    html = client.get("/modules").text
    assert "Compliance reports" not in html and "module-locked" not in html
    assert "No modules installed" in html
    nav = client.get("/settings").text
    assert 'title="Enterprise"' not in nav and 'href="/modules"' in nav  # Modules is open: built-in and own modules
    assert 'href="/reports"' not in nav


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


def test_discovery_reads_only_the_manifest(client, ext):
    ext.install("tcx_hello", src=HELLO)
    ext.install("tcx_old", {"__init__.py": "raise RuntimeError('imported!')"},
                manifest={"name": "tcx_old", "title": "Old", "requires_api": 0})
    ext.reload()
    assert "tcx_hello" not in sys.modules and ext.mod.LOADED == []  # not enabled: never imported
    html = client.get("/modules").text
    assert "Hello (example)" in html and "Third-party" in html and 'hx-post="/modules/tcx_hello/enable"' in html
    assert "needs Extension API 0" in html
    assert client.get("/x/tcx_hello/").status_code == 404


def test_enable_load_and_disable_the_example(client, ext, app_module):
    from app import notify
    ext.install("tcx_hello", src=HELLO)
    ext.reload()
    assert client.post("/modules/tcx_hello/enable").status_code == 200
    assert "restart" in client.get("/settings").text.lower() and ext.mod.restart_pending() == ["tcx_hello"]
    ext.reload()  # = tentacontrol's restart
    assert ext.mod.LOADED == ["tcx_hello"] and ext.mod.restart_pending() == []
    page = client.get("/x/tcx_hello/")
    assert page.status_code == 200 and "Hello" in page.text and "/x/tcx_hello/static/hello.css?v=" in page.text
    assert 'class="active"' in page.text.split('href="/x/tcx_hello/"')[1][:20]  # its sidebar entry is highlighted
    assert '<path d="M3 12c2-4' in page.text  # the extension's icon
    nav = client.get("/settings").text
    assert 'href="/x/tcx_hello/"' in nav and 'hx-get="/x/tcx_hello/count"' in nav and 'href="/x/tcx_hello/settings"' in nav
    assert client.get("/x/tcx_hello/settings").status_code == 200
    assert client.post("/x/tcx_hello/settings", data={"greeting": "Servus"}).status_code == 200
    assert "Servus" in client.get("/x/tcx_hello/").text
    assert 'id="x-tcx_hello-about"' in client.get("/help").text
    assert client.get("/x/tcx_hello/static/hello.css").status_code == 200
    assert "hello" in notify.EVENTS and "hello" in client.get("/notifications").text.lower()
    [task] = [t for t in ext.mod.tasks() if t["ext"] == "tcx_hello"]
    task["fn"]()
    assert client.get("/x/tcx_hello/count").text == "1"
    [(hook, _)] = ext.mod.audit_hooks()
    hook({"id": "x"})
    assert "<strong>1</strong> new audit entries" in client.get("/x/tcx_hello/").text
    assert "active" in client.get("/modules").text
    # disabled: gone at once, without a restart
    assert client.post("/modules/tcx_hello/disable").status_code == 200
    assert client.get("/x/tcx_hello/").status_code == 404 and client.get("/x/tcx_hello/static/hello.css").status_code == 404
    nav = client.get("/settings").text
    assert "/x/tcx_hello/" not in nav and ext.mod.audit_hooks() == [] and ext.mod.tabs("settings") == []
    assert 'id="x-tcx_hello-about"' not in client.get("/help").text


def test_loading_is_audited(client, ext):
    from app import auditlog
    ext.install("tcx_hello", src=HELLO)
    ext.mod.set_enabled("tcx_hello", True)
    ext.reload()
    entries, _ = auditlog.search("target:tcx_hello", rng="1h")
    assert any("extension loaded" in e["action"] for e in entries)


def test_backup_part_of_an_extension(client, ext):
    from app import backup
    ext.install("tcx_hello", src=HELLO)
    ext.mod.set_enabled("tcx_hello", True)
    ext.reload()
    assert "x_tcx_hello_settings" in backup.sections()
    doc, _ = backup.export(["x_tcx_hello_settings"])
    assert doc["x_tcx_hello_settings"] == {"greeting": "Hello"}
    doc["x_tcx_hello_settings"] = {"greeting": "Grüß Gott"}
    parsed = backup.parse(json.dumps(doc))
    rows = backup.preview(parsed)["x_tcx_hello_settings"]
    assert rows[0]["status"] == "changed" and not rows[0]["checked"]  # opt-in
    results, _ = backup.apply(parsed, {rows[0]["id"]})
    assert results[0]["ok"] and "Grüß Gott" in client.get("/x/tcx_hello/").text
    assert "Hello settings" in client.get("/backup").text
    # the part of an extension that isn't here: shown, not applied
    doc["x_other_thing"] = {"a": 1}
    rows = backup.preview(backup.parse(json.dumps(doc)))["x_other_thing"]
    assert rows[0]["status"] == "invalid" and "isn't installed" in rows[0]["error"]


ROUTE_PKG = """
    from fastapi import APIRouter
    from fastapi.responses import PlainTextResponse
    router = APIRouter()
    @router.get("{path}")
    async def page():
        return PlainTextResponse("from the extension")
    REQUIRES_API = 1
    def register(api):
        api.add_router(router)
        {more}
"""


def _pkg(path="/x/NAME/", more="pass"):
    return {"__init__.py": ROUTE_PKG.format(path=path, more=more)}


@pytest.mark.parametrize("name,files,why", [
    ("tcx_steal", _pkg("/license"), "routes must start with /x/tcx_steal/"),
    ("tcx_lic", _pkg("/x/tcx_lic/", "api.provide_license(lambda: {'reports'}, 'enterprise')"), "only an extension from TC_EXTENSIONS"),
    ("tcx_paid", _pkg("/x/tcx_paid/", "api.add_module('reports', 'R', 'r', licensed=True)"), "licensed modules come from the license provider"),
    ("tcx_nav", _pkg("/x/tcx_nav/", "api.add_module('n', 'N', 'n', nav={'href': '/approvals', 'label': 'A'})"), "nav href must start with /x/tcx_nav"),
    ("tcx_ev", _pkg("/x/tcx_ev/", "api.add_event('approval', 'Mine')"), "event approval exists already"),
    ("tcx_icon", _pkg("/x/tcx_icon/", "api.add_icon('lock', '<path/>')"), "icon lock exists already"),
    ("json", _pkg("/x/json/"), "taken by an installed Python module"),
    ("tcx_noapi", {"__init__.py": "def register(api):\n    pass\n"}, "declares no REQUIRES_API"),
])
def test_rules_for_builtin_and_local_extensions(client, ext, name, files, why):
    files = {k: v.replace("NAME", name) for k, v in files.items()}
    ext.install(name, files)
    ext.mod.set_enabled(name, True)
    ext.reload()
    assert name not in ext.mod.LOADED and why in ext.mod.FAILED[name]
    assert "from the extension" not in client.get("/license").text  # tentacontrol's own page stays
    assert ext.mod.edition() == "community" and ext.mod.modules() == []  # a failed register() leaves nothing behind
    assert "not loaded" in client.get("/modules").text


def test_local_templates_cannot_replace_tentacontrols(client, ext):
    files = _pkg("/x/tcx_tpl/", "api.add_templates(__import__('os').path.join(__import__('os').path.dirname(__file__), 'templates'))")
    files["templates/base.html"] = "hijacked"
    ext.install("tcx_tpl", files)
    ext.mod.set_enabled("tcx_tpl", True)
    ext.reload()
    assert "tcx_tpl" in ext.mod.LOADED
    assert "hijacked" not in client.get("/settings").text
    from app.web import templates
    assert templates.env.get_template("tcx_tpl/base.html").render() == "hijacked"  # its own, under its name


def test_a_local_extension_with_a_builtins_name_is_ignored(client, ext):
    ext.install("tcx_same", _pkg("/x/tcx_same/"), origin="builtin")
    ext.install("tcx_same", _pkg("/x/tcx_same/", "raise RuntimeError('local one')"), origin="local")
    ext.mod.set_enabled("tcx_same", True)
    ext.reload()
    assert ext.mod.LOADED == ["tcx_same"] and ext.mod.origin("tcx_same") == "builtin"
    assert "Built-in" in client.get("/modules").text


CONFIGURED = """
    from fastapi import APIRouter, Request
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from app import extapi
    REQUIRES_API = 1
    TITLE = "Test Enterprise"
    router = APIRouter()
    @router.get("/license")
    async def lic():
        return PlainTextResponse("from the extension")
    @router.get("/reports", response_class=HTMLResponse)
    @extapi.requires("reports")
    async def reports(request: Request):
        return extapi.page(request, "modules.html", nav="reports", exts=[], modules=[], community_modules=[], enterprise_modules=[])
    @router.get("/siem", response_class=HTMLResponse)
    @extapi.requires("siem")
    async def siem(request: Request):
        return PlainTextResponse("siem")
    def register(api):
        api.add_router(router)
        api.provide_license(lambda: {"reports"}, "enterprise")
        api.add_module("reports", "Compliance reports", "r", nav={"href": "/reports", "label": "Reports", "icon": "chart"}, licensed=True)
        api.add_module("siem", "SIEM export", "s", nav={"href": "/siem", "label": "SIEM"}, licensed=True)
        api.add_module("sso", "Single sign-on", "p", licensed=True, planned=True)
        api.add_banner(lambda request: {"text": "The license ends in 3 days", "kind": "warn", "href": "/license", "link": "Renew"})
        api.add_banner(lambda request: 1 / 0)  # a broken banner is left out
        api.add_banner(lambda request: None)
"""


def test_the_configured_license_provider(client, ext):
    ext.install("tcx_ent", {"__init__.py": CONFIGURED}, origin="configured")
    ext.install("tcx_ent2", {"__init__.py": CONFIGURED.replace("tcx_ent", "tcx_ent2").replace('@router.get("/license")', '@router.get("/license2")')},
                origin="configured")
    ext.reload("tcx_missing, tcx_ent, tcx_ent2")
    assert ext.mod.LOADED == ["tcx_ent"] and "provides the license already" in ext.mod.FAILED["tcx_ent2"]
    assert "tcx_missing" not in ext.mod.FAILED  # not installed: skipped quietly
    assert ext.mod.features() == {"reports"} and ext.mod.edition() == "enterprise"
    assert client.get("/license").text == "from the extension"  # configured: may replace a page
    nav = client.get("/settings").text
    assert 'href="/reports"' in nav and 'href="/siem"' not in nav  # licensed entry only while unlocked
    assert "The license ends in 3 days" in nav and ">Renew</a>" in nav and nav.count("ends in 3 days") == 1
    r = client.get("/siem")
    assert r.status_code == 403 and "needs a license that includes it" in r.text and "SIEM export" in r.text
    assert client.get("/reports").status_code == 200
    html = client.get("/modules").text
    assert "Enterprise modules are only available" not in html and "Open Reports" in html
    assert html.count(">licensed<") == 2 and "Enterprise · licensed" in html and "planned" in html  # module card + extension card and "set in TC_EXTENSIONS" not in html and "Test Enterprise" in html
    with pytest.raises(ValueError):
        ext.mod.set_enabled("tcx_ent", False)


def test_enterprise_without_a_license(client, ext):
    """the package is loaded, but no license unlocks anything – the Modules page says so instead of a green Enterprise"""
    ext.install("tcx_ent", {"__init__.py": CONFIGURED.replace("lambda: {\"reports\"}", "lambda: set()")}, origin="configured")
    ext.reload("tcx_ent")
    assert ext.mod.edition() == "enterprise" and ext.mod.features() == set()
    html = client.get("/modules").text
    assert "Enterprise · no license" in html and ">no license<" in html and "Enterprise · licensed" not in html


def test_audit_follower_across_a_rotation(tmp_path, monkeypatch, app_module):
    import tclib
    from app import auditlog
    def write(i):
        tclib.audit_write(str(tmp_path), tclib.audit_entry("system", "t", f"entry {i}", "ok", actor="system"))
    write(0)
    f = auditlog.Follower(str(tmp_path))
    assert f.poll() == []  # starts at the end
    write(1)
    write(2)
    assert [e["action"] for e in f.poll()] == ["entry 1", "entry 2"]
    monkeypatch.setattr(tclib, "AUDIT_ROTATE", 1)  # the next write moves the file first
    write(3)
    path = tmp_path / tclib.AUDIT_FILE
    with open(path, "a") as fh:  # a line not complete yet stays for the next poll
        fh.write('{"action": "half')
    assert [e["action"] for e in f.poll()] == ["entry 3"]
    monkeypatch.setattr(tclib, "AUDIT_ROTATE", 10**9)
    with open(path, "a") as fh:
        fh.write('"}\n')
    write(4)
    assert [e["action"] for e in f.poll()] == ["half", "entry 4"]
    # rotation between two polls: the rest of the moved file first
    write(5)
    monkeypatch.setattr(tclib, "AUDIT_ROTATE", 1)
    import time as _t
    _t.sleep(1.1)  # archives are named by the second
    write(6)
    assert [e["action"] for e in f.poll()] == ["entry 5", "entry 6"]


def test_extapi_data_files(app_module):
    from app import extapi
    extapi.save_json("tcx_data", "state.json", {"n": 1})
    assert extapi.load_json("tcx_data", "state.json") == {"n": 1}
    assert oct(os.stat(extapi.data_dir("tcx_data")).st_mode & 0o777) == "0o700"
    for bad in ("../x.json", ".hidden", ""):
        with pytest.raises(ValueError):
            extapi.save_json("tcx_data", bad, {})
    with pytest.raises(ValueError):
        extapi.data_dir("../etc")
