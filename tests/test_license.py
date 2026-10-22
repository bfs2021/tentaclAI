"""Enterprise license keys: issued by scripts/license-key.py, checked by tentacontrol (control/app/license.py)."""
import base64
import importlib.machinery
import importlib.util
import json
import os
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("license_key", os.path.join(ROOT, "scripts", "license-key.py"))
_spec = importlib.util.spec_from_loader("license_key", _loader)
gen = importlib.util.module_from_spec(_spec)
_loader.exec_module(gen)


@pytest.fixture
def lic(app_module, tmp_path, monkeypatch):
    """the license module with a throwaway key pair; returns (module, issue(**payload) → key)"""
    from app import license
    pem, pub = gen.new_pair()
    monkeypatch.setattr(license, "PUBLIC_KEY", pub)
    monkeypatch.setattr(license, "LICENSE_FILE", str(tmp_path / "license.json"))
    def issue(**kw):
        p = {"id": "L-TEST-1", "licensee": "Example GmbH", "email": "it@example.com", "edition": "enterprise",
             "features": ["reports", "siem"], "seats": 10, "issued": date.today().isoformat(),
             "expires": (date.today() + timedelta(days=365)).isoformat()}
        p.update(kw)
        return gen.issue(pem, p)
    return license, issue


def test_valid_expired_and_perpetual_keys(lic):
    license, issue = lic
    st = license.check(issue())
    assert st["state"] == "valid" and st["license"]["licensee"] == "Example GmbH" and st["days_left"] == 365
    assert license.check(issue(expires=(date.today() - timedelta(days=1)).isoformat()))["state"] == "expired"
    st = license.check(issue(expires=None))
    assert st["state"] == "valid" and st["days_left"] is None
    key = issue()
    assert license.check(key[:40] + "\n  " + key[40:])["state"] == "valid"  # a pasted key may wrap


def test_forged_or_broken_keys_are_refused(lic):
    license, issue = lic
    key = issue()
    prefix, payload, sig = key.split(".")
    raw = json.loads(gen.b64d(payload))
    raw["seats"] = 1000  # changed payload, old signature
    forged = f"{prefix}.{gen.b64e(json.dumps(raw).encode())}.{sig}"
    other_pem, _ = gen.new_pair()
    foreign = gen.issue(other_pem, raw)  # signed with a key that isn't tentaclAI's
    for bad in (forged, foreign, "TCL1.abc.def", "nonsense", "", f"TCL2.{payload}.{sig}", key + "x"):
        st = license.check(bad)
        assert st["state"] == "invalid" and st["error"], bad
    pem2, pub2 = gen.new_pair()  # correctly signed, but without a licensee
    assert license.check(gen.issue(pem2, {"id": "x"}), public_key=pub2)["state"] == "invalid"


def test_install_has_and_remove(lic):
    license, issue = lic
    assert license.status()["state"] == "none" and not license.has("reports")
    with pytest.raises(ValueError, match="ended"):
        license.install(issue(expires=(date.today() - timedelta(days=3)).isoformat()))
    with pytest.raises(ValueError):
        license.install("TCL1.x.y")
    license.install(issue(features=["reports"]))
    assert license.status()["state"] == "valid" and license.has("reports") and not license.has("siem")
    assert oct(os.stat(license.LICENSE_FILE).st_mode & 0o777) == "0o600"
    license.remove()
    assert license.status()["state"] == "none" and not license.has("reports")


def test_license_pages(client, lic):
    license, issue = lic
    html = client.get("/license").text
    assert "Currently using tentaclAI Community" in html and "get support, you need a valid Enterprise license" in html and "Activate license" in html
    assert "Reports are only available with a valid Enterprise license" in client.get("/reports").text
    r = client.post("/license", data={"key": "TCL1.broken.key"})
    assert "License not installed" in r.headers.get("hx-trigger", "") and license.status()["state"] == "none"
    r = client.post("/license", data={"key": issue()})
    assert r.headers.get("hx-refresh") == "true"
    html = client.get("/license").text
    assert "Example GmbH" in html and "L-TEST-1" in html and "365 days left" in html and "Compliance reports" in html
    assert "Change license" in html and "Remove license" in html
    assert "coming in a later version" in client.get("/reports").text
    assert 'title="Enterprise"' not in client.get("/settings").text  # the lock in the sidebar is gone
    client.post("/license/remove")
    assert license.status()["state"] == "none"


def test_expired_license_page(client, lic):
    license, issue = lic
    key = issue(expires=(date.today() - timedelta(days=2)).isoformat())
    license.jsonstore.save(license.LICENSE_FILE, {"key": key, "installed": 0})  # it ran out after it was installed
    html = client.get("/license").text
    assert "expired" in html and "Enterprise features are locked" in html
    assert not license.has("reports") and "only available with a valid Enterprise license" in client.get("/reports").text


def test_shipped_public_key_is_well_formed(app_module):
    from app import license
    if license.PUBLIC_KEY:  # 32 raw bytes, base64url
        assert len(base64.urlsafe_b64decode(license.PUBLIC_KEY + "=" * (-len(license.PUBLIC_KEY) % 4))) == 32


def test_instance_id_comes_from_the_hardware(app_module, tmp_path, monkeypatch):
    from app import license
    uuid = tmp_path / "product_uuid"
    monkeypatch.setattr(license, "DMI_UUID", str(uuid))
    monkeypatch.setattr(license, "_engine_id", lambda: "ENGINE-1")
    def fresh():
        monkeypatch.setattr(license, "_instance", None)
        return license.instance_id()
    uuid.write_text("CA19E780-8A0C-11EE-A162-34D2D6982C00\n")
    iid, kind = fresh()
    assert kind == "hardware" and iid.startswith("TCI-") and len(iid) == 23 and iid == fresh()[0]  # stable
    uuid.write_text("ca19e780-8a0c-11ee-a162-34d2d6982c01")
    assert fresh()[0] != iid  # other hardware, other ID
    uuid.write_text("03000200-0400-0500-0006-000700080009")  # a board's placeholder: Docker's engine ID instead
    assert fresh()[1] == "docker"
    uuid.unlink()
    monkeypatch.setattr(license, "_engine_id", lambda: "")
    assert fresh() == ("", "")


def test_key_bound_to_another_installation(client, lic, monkeypatch):
    license, issue = lic
    monkeypatch.setattr(license, "instance_id", lambda: ("TCI-AAAA-BBBB-CCCC-DDDD", "hardware"))
    assert license.check(issue(instance="TCI-AAAA-BBBB-CCCC-DDDD"))["state"] == "valid"
    st = license.check(issue(instance="TCI-1111-2222-3333-4444"))
    assert st["state"] == "invalid" and "TCI-1111-2222-3333-4444" in st["error"] and "TCI-AAAA-BBBB-CCCC-DDDD" in st["error"]
    with pytest.raises(ValueError, match="another|not for this one"):
        license.install(issue(instance="TCI-1111-2222-3333-4444"))
    assert license.check(issue())["state"] == "valid"  # without a binding: any installation
    html = client.get("/license").text
    assert "TCI-AAAA-BBBB-CCCC-DDDD" in html and "hardware" in html
    license.install(issue(instance="TCI-AAAA-BBBB-CCCC-DDDD"))
    assert "(this one)" in client.get("/license").text
