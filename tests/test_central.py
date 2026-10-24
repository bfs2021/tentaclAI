"""Central management, the agent side (control/app/central.py) against a minimal fake server that follows
docs/central.md: enrolling, signed requests, the signed profile (enforced / default items, local items), locks in the
stores and in svc, reverting, audit shipping with its cursor, the config snapshot, leaving and being revoked."""
import base64
import hashlib
import json
import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class FakeCentral:
    """the server side of docs/central.md, just enough to test the agent"""
    URL = "https://central.example.test"

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub = b64e(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.tokens, self.agents, self.audit, self.reports, self.configs = {"tok-1"}, {}, {}, [], []
        self.profile, self.rev, self.revoked, self.packages, self.nonces = None, 0, False, {}, set()

    def code(self, token="tok-1", key=None):
        from app import central
        return "tce1." + b64e(json.dumps({"url": self.URL, "token": token, "key": key or central.fingerprint(self.pub)}).encode())

    def set_profile(self, **p):
        self.rev += 1
        self.profile = dict(p, rev=self.rev)

    def __call__(self, method, url, body, headers):
        path = url[len(self.URL):]
        assert path.startswith("/api/agent/v1/")
        path = path[len("/api/agent/v1"):]
        if path == "/enroll":
            req = json.loads(body)
            if req["token"] not in self.tokens:
                return 403, b'{"error": "token unknown"}'
            self.tokens.discard(req["token"])
            self.agents["A-0001"] = req
            return 200, json.dumps({"agent_id": "A-0001", "central_key": self.pub, "central_name": "Test Central", "profile_rev": self.rev}).encode()
        aid = headers["X-TC-Agent"]
        msg = "\n".join([method, "/api/agent/v1" + path, headers["X-TC-Time"], headers["X-TC-Nonce"],
                         hashlib.sha256(body or b"").hexdigest()]).encode()
        Ed25519PublicKey.from_public_bytes(b64d(self.agents[aid]["public_key"])).verify(b64d(headers["X-TC-Signature"]), msg)
        assert headers["X-TC-Nonce"] not in self.nonces and abs(int(headers["X-TC-Time"]) - time.time()) < 300
        self.nonces.add(headers["X-TC-Nonce"])
        if self.revoked:
            return 410, b'{"error": "revoked"}'
        data = json.loads(body) if body else {}
        if path == "/heartbeat":
            self.status = data
            return 200, json.dumps({"profile_rev": self.rev, "want_config": False}).encode()
        if path == "/profile":
            prof = dict(self.profile, agent_id=aid)
            return 200, json.dumps({"profile": prof, "sig": b64e(self.key.sign(canonical(prof)))}).encode()
        if path == "/audit":
            for e in data["entries"]:
                self.audit[e["id"]] = e
            return 200, json.dumps({"stored": len(data["entries"])}).encode()
        if path == "/config":
            self.configs.append(data)
            return 200, b"{}"
        if path == "/report":
            self.reports.append(data)
            return 200, b"{}"
        if path == "/leave":
            self.agents.pop(aid)
            return 200, b"{}"
        if path.startswith("/packages/"):
            return 200, self.packages[path]
        return 404, b'{"error": "unknown"}'


@pytest.fixture
def fc(app_module, tmp_path, monkeypatch):
    """the agent with its state in tmp_path, talking to a FakeCentral"""
    from app import central, packages
    for name, val in (("DIR", tmp_path / "central"),):
        monkeypatch.setattr(central, name, str(val))
    for name in ("KEY_FILE", "STATE", "PROFILE", "LOCKS", "CA_FILE"):
        monkeypatch.setattr(central, name, str(tmp_path / "central" / os.path.basename(getattr(central, name))))
    monkeypatch.setattr(packages, "PACKAGES_DIR", str(tmp_path / "packages"))
    monkeypatch.setattr(packages, "PREV_DIR", str(tmp_path / "packages" / ".prev"))
    fake = FakeCentral()
    monkeypatch.setattr(central, "_http", fake)
    yield fake
    central.LOCKS and os.path.exists(central.LOCKS) and os.remove(central.LOCKS)


def test_enroll_and_leave(fc, client):
    from app import central
    with pytest.raises(central.CentralError, match="starts with tce1"):
        central.enroll("hello", "x", [])
    with pytest.raises(central.CentralError, match="doesn't match"):
        central.enroll(fc.code(key="AAAA BBBB CCCC DDDD"), "x", ["services"])
    fc.tokens.add("tok-2")
    st = central.enroll(fc.code("tok-2"), "dev box", ["services", "settings", "nothing"])
    assert st["agent_id"] == "A-0001" and st["areas"] == ["services", "settings"] and central.enrolled()
    assert oct(os.stat(central.KEY_FILE).st_mode & 0o777) == "0o600"
    with pytest.raises(central.CentralError, match="enrolled already"):
        central.enroll(fc.code("tok-1"), "x", [])
    html = client.get("/central").text
    assert "Test Central" in html and "A-0001" in html and "Leave" in html
    central.leave()
    assert not central.enrolled() and "A-0001" not in fc.agents
    assert "Enrollment code" in client.get("/central").text


def test_profile_enforced_default_and_locks(fc, client):
    from app import central, services_store
    from app.envfile import read_env, update_env
    update_env({"GIT_USER_NAME": None, "TZ": None})  # the fixture stack is shared by both client variants
    central.enroll(fc.code(), "dev", ["services", "settings", "network"])
    svc = {"description": "Wiki", "web": {"base_url": "https://wiki.example.test"}}
    fc.set_profile(name="Developers", sections={"services": {"cwiki": svc}, "settings": {"GIT_USER_NAME": "Central Bot", "TZ": "Europe/Vienna"},
                                                 "network": {"allow": ["central-allowed.example.test"]}},
                   modes={"services:cwiki": "enforced", "settings:GIT_USER_NAME": "enforced", "settings:TZ": "default",
                          "network:allow:central-allowed.example.test": "enforced"},
                   local_items={"services": False})
    report = central.sync()
    assert {r["id"] for r in report["results"] if r["result"] == "applied"} >= {"services:cwiki", "settings:GIT_USER_NAME", "settings:TZ"}
    assert json.loads(services_store.read_raw("cwiki"))["description"] == "Wiki"
    assert read_env()["GIT_USER_NAME"] == "Central Bot" and read_env()["TZ"] == "Europe/Vienna"
    assert central.locked("services", "cwiki") and central.locked("settings", "GIT_USER_NAME") and not central.locked("settings", "TZ")
    # local changes: an enforced item is refused, a default one may change
    with pytest.raises(ValueError, match="managed by Test Central"):
        services_store.save_raw("cwiki", json.dumps({"description": "mine"}))
    with pytest.raises(ValueError, match="managed by"):
        update_env({"GIT_USER_NAME": "me"})
    update_env({"TZ": "UTC"})
    html = client.get("/settings").text
    assert 'id="f-GIT_USER_NAME" type="text" value="Central Bot" disabled' in html and "central</span>" in html
    assert "central</span>" in client.get("/services").text
    # around tentacontrol (a file edited on the host): reverted at the next sync and reported
    with open(services_store.path_of("cwiki"), "w") as f:
        json.dump({"description": "edited on the host"}, f)
    report = central.sync()
    assert json.loads(services_store.read_raw("cwiki"))["description"] == "Wiki"
    assert {"id": "services:cwiki", "kind": "reverted"} in report["drift"]
    assert {"id": "settings:TZ", "kind": "overridden"} in report["drift"] and read_env()["TZ"] == "UTC"
    assert any(d["kind"] == "local item" for d in report["drift"])  # the fixture's own services, not allowed here
    assert fc.reports and fc.reports[-1]["drift"]
    assert fc.status["profile"]["rev"] == fc.rev and "services" in [s for s in fc.status["areas"]]
    # a new default replaces a value only while it is still the previous profile value
    fc.profile["sections"]["settings"]["TZ"] = "Europe/Berlin"
    fc.set_profile(**{k: v for k, v in fc.profile.items() if k != "rev"})
    central.sync()
    assert read_env()["TZ"] == "UTC"  # changed locally: stays
    from app import auditlog
    actions = [e["action"] for e in auditlog.search("central", rng="1h")[0]]
    assert any("local change reverted: services:cwiki" in a for a in actions) and any("profile applied" in a for a in actions)
    central.leave()
    services_store.save_raw("cwiki", json.dumps({"description": "free again"}))  # no locks after leaving
    from app import egress
    services_store.delete("cwiki")
    egress.remove("allow", "central-allowed.example.test")
    update_env({"GIT_USER_NAME": None, "TZ": None})


def test_refused_areas_and_bad_signature(fc):
    from app import central
    central.enroll(fc.code(), "dev", ["settings"])
    fc.set_profile(name="P", sections={"services": {"x": {"description": "x"}}}, modes={})
    report = central.sync()
    assert report["refused"] == ["services"] and not report["results"]
    fc.set_profile(name="P2", sections={}, modes={})
    real = fc.key
    fc.key = Ed25519PrivateKey.generate()  # a profile signed by someone else
    with pytest.raises(central.CentralError, match="signature"):
        central.sync()
    fc.key = real
    fc.revoked = True
    with pytest.raises(central.CentralError, match="revoked"):
        central.sync()
    assert central.state()["revoked"] and not central.enrolled()
    central.leave()  # works without the server


def test_audit_is_shipped_once(fc):
    from app import auditlog, central
    central.enroll(fc.code(), "dev", [])
    for i in range(3):
        auditlog.write("admin", "testsvc", f"central shipping {i}", actor="system")
    fc.set_profile(name="P", sections={}, modes={})
    central.sync()
    shipped = [e for e in fc.audit.values() if e["action"].startswith("central shipping")]
    assert len(shipped) == 3
    n = len(fc.audit)
    central.sync()
    assert len(fc.audit) - n <= 3  # only the agent's own new entries (profile received …), nothing twice
    assert central.retention_floor() >= 2
    assert fc.configs and "services" in fc.configs[0]["config"]
    before = len(fc.configs)
    central.sync()
    assert len(fc.configs) == before  # the configuration didn't change: not sent again


def test_svc_respects_the_locks(tmp_path):
    import tclib
    d = tmp_path / "control"
    (d / "central").mkdir(parents=True)
    (d / "central" / "locks.json").write_text(json.dumps({"central": "HQ", "enforced": ["services:wiki"]}))
    assert tclib.central_locked(str(d), "services", "wiki") == "HQ"
    assert tclib.central_locked(str(d), "services", "other") == ""
    assert tclib.central_locked(str(tmp_path / "none"), "services", "wiki") == ""


def test_module_packages_from_the_profile(fc, ext, monkeypatch):
    """Z4: a profile names a signed package – fetched from the server, checked, installed"""
    import importlib.util
    from app import central, packages
    spec = importlib.util.spec_from_file_location("tp", os.path.join(os.path.dirname(__file__), "test_packages.py"))
    tp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tp)
    priv, pub = tp.new_key()
    monkeypatch.setattr(packages, "PACKAGE_KEYS", [pub])
    data = tp.build("tcx_pkg", priv=priv)
    fc.packages["/packages/tcx_pkg/1.0.0"] = data
    central.enroll(fc.code(), "dev", ["modules"])
    fc.set_profile(name="P", sections={}, modes={},
                   modules={"tcx_pkg": {"mode": "enforced", "package": {"version": "1.0.0", "sha256": hashlib.sha256(data).hexdigest()}}})
    report = central.sync()
    assert any(r["id"] == "modules:tcx_pkg" and r["result"] == "applied" for r in report["results"])
    assert packages.installed_versions() == {"tcx_pkg": "1.0.0"}
    fc.packages["/packages/tcx_pkg/1.1.0"] = tp.build("tcx_pkg", "1.1.0")  # unsigned: refused
    fc.set_profile(name="P", sections={}, modes={}, modules={"tcx_pkg": {"package": {"version": "1.1.0"}}})
    report = central.sync()
    assert any(r["id"] == "modules:tcx_pkg" and r["result"] == "failed" and "not signed" in r["msg"] for r in report["results"])
    assert packages.installed_versions() == {"tcx_pkg": "1.0.0"}
