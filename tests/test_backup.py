"""Settings → Export / Import and service templates: no secrets leave .env, preview writes nothing, apply keeps backups."""
import glob
import json
import os

import pytest

from conftest import STACK


@pytest.fixture
def bk(app_module):
    from app import backup
    return backup


@pytest.fixture
def scratch():
    """files a test creates in the shared dummy stack – removed afterwards (with their backups)"""
    paths = []
    yield paths
    for p in paths:
        for f in glob.glob(p) + glob.glob(p + ".bak-*"):
            os.remove(f)


def write_service(name, data, scratch):
    p = os.path.join(STACK, "services", f"{name}.json")
    with open(p, "w") as f:
        json.dump(data, f)
    scratch.append(p)


def test_export_has_no_secret_values(bk):
    doc, _ = bk.export()
    text = json.dumps(doc)
    for value in ("secret-123", "ntfy://x/y", "visible-if-broken-123"):
        assert value not in text
    assert {"API_TOKEN", "DB_PW", "ODD_ACCESS"} <= set(doc["variables"])
    assert doc["settings"]["TC_HOSTNAME"] == "testhost"
    assert "TC_DIR" not in doc["settings"] and "RC_ENABLED" in doc["settings"]
    assert doc["ssh_hosts"][0]["alias"] == "box" and "file" not in doc["ssh_hosts"][0]
    assert "name" not in doc["services"]["demoapi"]


def test_export_blanks_plain_credentials(bk, scratch):
    write_service("plainsvc", {"api": {"base_url": "https://p.example.test", "auth": {"type": "bearer", "token": "hardcoded-1"},
                                       "headers": {"Accept": "application/json", "X-Api-Key": "hardcoded-2"}}}, scratch)
    doc, left = bk.export(["services"])
    assert "hardcoded" not in json.dumps(doc)
    assert doc["services"]["plainsvc"]["api"]["headers"]["Accept"] == "application/json"
    assert ("plainsvc", "api.auth.token") in left and ("plainsvc", "api.headers.X-Api-Key") in left


def test_own_export_previews_as_unchanged(bk):
    doc, _ = bk.export()
    rows = bk.preview(bk.parse(json.dumps(doc)))
    assert {r["status"] for rs in rows.values() for r in rs} == {"same"}


@pytest.mark.parametrize("doc,msg", [
    ({"format": "other"}, "not a tentaclAI export"),
    ({"format": "tentaclai-export", "version": 9}, "version 9"),
    ({"format": "tentaclai-export", "version": 1, "secrets": {}}, "unknown parts"),
    ({"format": "tentaclai-export", "version": 1, "services": []}, "wrong type"),
])
def test_parse_refuses(bk, doc, msg):
    with pytest.raises(ValueError, match=msg):
        bk.parse(json.dumps(doc))


def test_preview_flags_invalid_entries(bk):
    doc = {"format": "tentaclai-export", "version": 1,
           "ssh_hosts": [{"alias": "evil", "hostname": "e.example.test", "user": "root", "proxycommand": "nc x 22"}],
           "services": {"Bad Name": {}}, "network": {"allow": ["not a host!"]},
           "settings": {"API_TOKEN": "x", "TC_DIR": "/x", "TZ": "Europe/Vienna"},
           "jobs": {"j1": {"name": "j", "prompt": "check @nosuch", "schedule": {"kind": "manual"}}}}
    rows = bk.preview(doc)
    status = {r["id"]: r["status"] for rs in rows.values() for r in rs}
    assert status["ssh_hosts:evil"] == "invalid"
    assert status["services:Bad Name"] == "invalid"
    assert status["network:allow:not a host!"] == "invalid"
    assert status["settings:API_TOKEN"] == status["settings:TC_DIR"] == "invalid"
    assert status["settings:TZ"] == "new"
    assert status["jobs:j1"] == "invalid"
    tz = next(r for r in rows["settings"] if r["key"] == "TZ")
    assert not tz["checked"]  # settings and site rules are opt-in


def test_apply_writes_picked_with_backups(bk, scratch):
    from app import egress, notify, scheduled
    write_service("tmpsvc", {"description": "old", "api": {"base_url": "https://t.example.test"}}, scratch)
    scratch += [os.path.join(STACK, "services", "newsvc.json"), os.path.join(STACK, "ssh", "config.d", "newhost.conf"),
                scheduled.JOBS, egress.RULES, notify.CONFIG]
    doc = {"format": "tentaclai-export", "version": 1,
           "services": {"tmpsvc": {"description": "new", "api": {"base_url": "https://t.example.test"}},
                        "newsvc": {"api": {"base_url": "https://n.example.test", "auth": {"type": "bearer", "token": "${NEWSVC_TOKEN}"}}}},
           "ssh_hosts": [{"alias": "newhost", "hostname": "nh.example.test", "user": "admin", "port": "2222", "hostkeyalias": "nh",
                          "sudo": "", "access": "read"}],
           "jobs": {"nightly": {"name": "Nightly", "prompt": "look at @newsvc", "schedule": {"kind": "daily", "time": "03:00"}, "enabled": True}},
           "network": {"allow": ["updates.example.test"]},
           "notifications": {"channels": [{"id": "pager", "name": "Pager", "kind": "ntfy", "enabled": True}]}}
    rows = bk.preview(doc)
    picked = {r["id"] for rs in rows.values() for r in rs if r["checked"]}
    assert "jobs:nightly" in picked  # @newsvc comes with the same import
    results, missing = bk.apply(doc, picked)
    assert all(r["ok"] for r in results), results
    assert json.load(open(os.path.join(STACK, "services", "tmpsvc.json")))["description"] == "new"
    assert glob.glob(os.path.join(STACK, "services", "tmpsvc.json.bak-*-import"))
    conf = open(os.path.join(STACK, "ssh", "config.d", "newhost.conf")).read()
    assert "Port 2222" in conf and "HostKeyAlias nh" in conf and "access=read" in conf
    assert scheduled.get("nightly")["enabled"] is False
    assert "updates.example.test" in egress.rules()["allow"]
    assert next(c for c in notify.load()["channels"] if c["id"] == "pager")["enabled"] is False
    assert {"NEWSVC_TOKEN", "NOTIFY_URL_PAGER"} <= set(missing)
    # clean up what lives inside shared files
    jobs = scheduled.load()
    jobs.pop("nightly")
    from app import jsonstore
    jsonstore.save(scheduled.JOBS, jobs)
    egress.remove("allow", "updates.example.test")
    c = notify.load()
    c["channels"] = [x for x in c["channels"] if x["id"] != "pager"]
    for ev in c["routes"]:
        c["routes"][ev] = [x for x in c["routes"][ev] if x != "pager"]
    notify.save(c)
    scratch[:] = [p for p in scratch if p not in (scheduled.JOBS, egress.RULES, notify.CONFIG)] + \
        [scheduled.JOBS + ".bak-*", egress.RULES + ".bak-*", notify.CONFIG + ".bak-*"]


def test_template_from_service(app_module, scratch):
    from app import services_store
    write_service("tokdemo", {"description": "Tok demo", "system": "box", "enabled": False,
                              "api": {"base_url": "https://tok.site.test", "auth": {"type": "bearer", "token": "${TOKDEMO_KEY}"}, "insecure_tls": True},
                              "examples": ["svc tokdemo GET /x"], "notes": ["Setup: create a token", "uses 10.1.2.3"]}, scratch)
    tpl, warnings = services_store.to_template("tokdemo", category="Apps")
    assert tpl["_meta"] == {"title": "Tok demo", "category": "Apps", "setup": "create a token"}
    assert {"system", "enabled", "name"}.isdisjoint(tpl) and "insecure_tls" not in tpl["api"]
    assert tpl["api"]["base_url"] == "https://tok.{{DOMAIN}}"
    assert tpl["api"]["auth"]["token"] == "${EXAMPLE_KEY}"
    assert tpl["examples"] == ["svc NAME GET /x"] and tpl["notes"] == ["uses 10.1.2.3"]
    assert any("10.1.2.3" in w for w in warnings)


def test_template_import(app_module, scratch, client):
    from app import services_store
    scratch.append(os.path.join(services_store.OWN_TEMPLATES_DIR, "mytpl.json"))
    good = {"_meta": {"title": "Mine", "category": "Apps"}, "api": {"base_url": "https://m.{{DOMAIN}}", "auth": {"type": "bearer", "token": "${EXAMPLE_TOKEN}"}}}
    r = client.post("/services/template-import", files={"file": ("mytpl.json", json.dumps(good))}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Not imported" not in r.headers.get("HX-Trigger", "")
    own = next(t for t in services_store.list_templates() if t["id"] == "mytpl")
    assert own["own"] and own["title"] == "Mine"
    with pytest.raises(ValueError, match="built-in"):
        services_store.save_template("n8n", json.dumps(good))
    leaked = dict(good, api={"base_url": "https://x", "auth": {"type": "bearer", "token": "abc"}})
    with pytest.raises(ValueError, match="credential"):
        services_store.save_template("leaky", json.dumps(leaked))


def test_import_pages(bk, client):
    doc, _ = bk.export(["services", "settings"])
    doc["services"]["fromfile"] = {"api": {"base_url": "https://f.example.test"}}
    r = client.post("/backup/import/preview", files={"file": ("x.json", json.dumps(doc))})
    assert r.status_code == 200 and "fromfile" in r.text and "Import picked" in r.text
    r = client.post("/backup/import/preview", files={"file": ("x.json", "nope")})
    assert "not a JSON file" in r.text
    r = client.get("/backup/export?part=systems")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert set(json.loads(r.text)) >= {"format", "systems"} and "services" not in json.loads(r.text)


def test_site_skills_export_preview_and_import(bk):
    import shutil
    from app import skills
    base = os.path.join(STACK, "local", "skills")
    text = "---\nname: renew-cert\ndescription: Renew the wildcard certificate\n---\n\n1. check the expiry\n"
    try:
        skills.save_site("renew-cert", text)
        doc, _ = bk.export(["skills"])
        assert doc["skills"] == {"renew-cert": text}
        doc["skills"]["renew-cert"] = text + "2. reload the proxy\n"
        doc["skills"]["new-one"] = text.replace("renew-cert", "new-one")
        doc["skills"]["Bad"] = text
        rows = {r["key"]: r for r in bk.preview(bk.parse(json.dumps(doc)))["skills"]}
        assert rows["renew-cert"]["status"] == "changed" and "+2. reload the proxy" in rows["renew-cert"]["diff"]
        assert rows["new-one"]["status"] == "new" and not rows["new-one"]["checked"]  # opt-in: steps Claude follows
        assert rows["Bad"]["status"] == "invalid"
        results, _ = bk.apply(doc, {"skills:renew-cert", "skills:new-one", "skills:Bad"})
        assert sorted(r["label"] for r in results if r["ok"]) == ["new-one", "renew-cert"]
        assert "reload the proxy" in skills.site_text("renew-cert") and glob.glob(os.path.join(base, "renew-cert", "SKILL.md.bak-*"))
        assert oct(os.stat(os.path.join(base, "new-one", "SKILL.md")).st_mode)[-3:] == "644"
    finally:
        shutil.rmtree(base, ignore_errors=True)
