import os

import pytest

import tclib


def test_read_env(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nA=1\nB='two words'\n  C = \"x=y\"\nnot a line\nEMPTY=\n")
    assert tclib.read_env(str(p)) == {"A": "1", "B": "two words", "C": "x=y", "EMPTY": ""}
    assert tclib.read_env(str(tmp_path / "missing")) == {}


def test_variables_render_missing():
    obj = {"url": "https://${HOST}/api", "auth": {"token": "${TOKEN}"}, "_meta": {"x": "${IGNORED}"}, "list": ["${A}", 3]}
    assert tclib.variables(obj) == {"HOST", "TOKEN", "A"}
    env = {"HOST": "h", "A": "a"}
    r = tclib.render(obj, env)
    assert r == {"url": "https://h/api", "auth": {"token": ""}, "list": ["a", 3]}
    assert tclib.render("${TOKEN}", env, keep_unknown=True) == "${TOKEN}"
    assert tclib.missing(obj, env) == ["TOKEN"]


def test_status():
    svc = {"api": {"base_url": "https://x", "auth": {"token": "${T}"}}}
    assert tclib.status(svc, {}) == ("missing", ["T"])
    assert tclib.status_text(svc, {}) == "missing: T"
    assert tclib.status(svc, {"T": "1"}) == ("active", [])
    assert tclib.status(dict(svc, enabled=False), {}) == ("disabled", [])


def test_load_services_inherits_system_ssh_host(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "systems").mkdir()
    (tmp_path / "systems" / "box.json").write_text('{"ssh_host": "box"}')
    (tmp_path / "services" / "d.json").write_text('{"system": "box", "docker": {"mode": "ssh"}, "files": {"root": "/"}}')
    (tmp_path / "services" / "_tpl.json").write_text("{}")
    (tmp_path / "services" / "bad.json").write_text("{nope")
    errors = {}
    svcs = tclib.load_services(str(tmp_path / "services"), tclib.load_systems(str(tmp_path)), errors)
    assert list(svcs) == ["d"] and "bad" in errors
    assert svcs["d"]["docker"]["ssh_host"] == "box" and svcs["d"]["files"]["ssh_host"] == "box"


def test_reach_hosts():
    services = {
        "a": {"api": {"base_url": "https://api.test:8443/x"}, "mcp": {"url": "http://mcp.test"}},
        "g": {"git": {"url": "git@git.test:me/r.git"}},
        "d": {"db": {"engine": "mariadb", "host": "DB.test"}},
        "off": {"enabled": False, "api": {"base_url": "https://off.test"}},
        "miss": {"api": {"base_url": "https://${NOPE}.test"}},
    }
    r = tclib.reach_hosts(services, {}, [{"alias": "box", "hostname": "box.test", "port": "2222"}])
    assert r == {("api.test", 8443): "a", ("mcp.test", 80): "a", ("git.test", 22): "g", ("db.test", 3306): "d",
                 ("box.test", 2222): "ssh box"}


@pytest.mark.parametrize("pattern,host,port,ips,ok", [
    ("example.com", "example.com", 443, (), True),
    ("example.com", "EXAMPLE.com.", 443, (), True),
    ("example.com:443", "example.com", 80, (), False),
    ("*.example.com", "a.example.com", 443, (), True),
    ("*.example.com", "example.com", 443, (), False),
    ("10.0.0.0/8", "10.1.2.3", 22, (), True),
    ("10.0.0.0/8", "name.test", 22, ("10.9.9.9",), True),
    ("10.0.0.0/8", "name.test", 22, ("192.168.1.1",), False),
    ("192.168.1.5", "192.168.1.5", 443, (), True),
    ("bad/cidr", "x", 1, (), False),
])
def test_egress_match(pattern, host, port, ips, ok):
    assert tclib.egress_match(pattern, host, port, ips) is ok


def test_egress_patterns():
    assert tclib.egress_patterns("# c\nA.test  # why\n\n*.b.test\n") == ["a.test", "*.b.test"]


def test_ssh_roundtrip(tmp_path):
    conf = str(tmp_path)
    fields = {"alias": "box", "hostname": "box.test", "user": "root", "port": "2222", "sudo": "sudo -n", "access": "rw"}
    tclib.ssh_add(conf, fields)
    assert oct(os.stat(tmp_path / "box.conf").st_mode & 0o777) == "0o600"
    h = tclib.ssh_host(conf, "box")
    assert (h["hostname"], h["port"], h["sudo"], h["access"]) == ("box.test", "2222", "sudo -n", "rw")
    assert tclib.ssh_access(conf, "box") == "rw" and tclib.ssh_access(conf, "nope") == "confirm"
    with pytest.raises(ValueError):
        tclib.ssh_add(conf, fields)  # exists
    tclib.ssh_set_sudo(conf, "box", "")
    assert tclib.ssh_sudo(conf, "box") == ""
    assert tclib.ssh_known_name({"hostname": "box.test", "port": "2222"}) == "[box.test]:2222"


@pytest.mark.parametrize("bad", [
    {"alias": "a b"}, {"hostname": "x;rm"}, {"user": "r oot"}, {"port": "70000"}, {"sudo": "sudo; id"},
    {"access": "all"}, {"proxyjump": "box"},
])
def test_ssh_validate_rejects(bad):
    fields = dict({"alias": "box", "hostname": "box.test", "user": "root"}, **bad)
    with pytest.raises(ValueError):
        tclib.ssh_validate(fields)


def test_skill_overrides_defaults_and_modes():
    s = {"known": {"loop": {}, "dataviz": {}}, "modes": {"loop": "off", "dataviz": "on", "x": "bogus"}}
    m = tclib.skill_overrides(s)
    assert m["loop"] == "off" and m["dataviz"] == "on" and m["x"] == "on"  # invalid mode → default
    assert all(m[k] == "off" for k in tclib.SKILLS_OFF_DEFAULT if k != "dataviz")


def test_apply_skill_overrides_keeps_foreign_entries():
    settings = {"skillOverrides": {"mine": "off", "run": "off"}, "theme": "dark"}
    tclib.apply_skill_overrides(settings, {"run": "on", "loop": "user-invocable-only"})
    assert settings == {"skillOverrides": {"mine": "off", "loop": "user-invocable-only"}, "theme": "dark"}
    s2 = {"skillOverrides": {"run": "off"}}
    assert tclib.apply_skill_overrides(s2, {"run": "on"}) == {}


def test_models_defaults_and_validation():
    assert tclib.models({}) == tclib.MODELS_DEFAULT
    m = tclib.models({"main": "opus", "effort": "xhigh", "subagents": "", "commands": "opus; rm -rf /", "commands_effort": "max",
                      "unknown": "x"})
    assert m == {"main": "opus", "effort": "xhigh", "subagents": "", "commands": "sonnet", "commands_effort": ""}
    assert tclib.valid_model("claude-sonnet-5-5") and tclib.valid_model("opus[1m]") and not tclib.valid_model("Opus X")


def test_apply_models():
    s = {"env": {"DISABLE_TELEMETRY": "1"}, "theme": "dark"}
    tclib.apply_models(s, {"main": "opus", "effort": "high", "subagents": "haiku"})
    assert s == {"env": {"DISABLE_TELEMETRY": "1", "CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}, "theme": "dark", "model": "opus",
                 "effortLevel": "high"}
    tclib.apply_models(s, {"main": "", "effort": "", "subagents": ""})
    assert s == {"env": {"DISABLE_TELEMETRY": "1"}, "theme": "dark"}
    assert tclib.apply_models({"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "x"}}, {}) == {}


def test_command_frontmatter():
    text = "---\ndescription: Do it\nargument-hint: <x>\n---\n\nBody\n---\nnot frontmatter\n"
    a = tclib.command_frontmatter(text, "sonnet", "low")
    assert a == "---\ndescription: Do it\nargument-hint: <x>\nmodel: sonnet\neffort: low\n---\n\nBody\n---\nnot frontmatter\n"
    assert tclib.command_frontmatter(a, "opus", "") == "---\ndescription: Do it\nargument-hint: <x>\nmodel: opus\n---\n\nBody\n---\nnot frontmatter\n"
    assert tclib.command_frontmatter(a) == text
    assert tclib.command_frontmatter("Just a body\n", "haiku") == "---\nmodel: haiku\n---\nJust a body\n"


def test_write_model_commands(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(), dst.mkdir()
    (src / "inventory.md").write_text("---\ndescription: inv\n---\nBody\n")
    tclib.write_model_commands({"commands": "sonnet", "commands_effort": "medium"}, str(src), str(dst))
    assert (dst / "inventory.md").read_text() == "---\ndescription: inv\nmodel: sonnet\neffort: medium\n---\nBody\n"
    assert not (dst / "onboard.md").exists()  # missing source → skipped


def test_required_skills_never_off():
    s = {"modes": {"inventory": "off", "onboard": "user-invocable-only"}}
    m = tclib.skill_overrides(s)
    assert m["inventory"] == "on" and m["onboard"] == "user-invocable-only"
    assert tclib.skill_overrides({})["inventory"] == "on"  # listed even before discovery


def test_secret_variables():
    svc = {"api": {"base_url": "https://${HOST}/api", "auth": {"type": "basic", "user": "${API_USER}", "password": "${API_PASS}"},
                   "headers": {"X-Access": "${ACCESS_THING}"}},
           "mcp": {"command": "x", "env": {"PLAIN": "${MCP_THING}"}},
           "db": {"host": "${DB_HOST}", "user": "${DB_USER}", "password": "${DB_SECRET_X}"},
           "web": {"auth": {"type": "header", "header": "${HEADER_NAME}", "value": "${WEB_VALUE}"}},
           "_meta": {"x": "${IGNORED}"}}
    assert tclib.secret_variables(svc) == {"API_PASS", "ACCESS_THING", "MCP_THING", "DB_SECRET_X", "WEB_VALUE"}


def test_is_secret_names_and_positions():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))
    from app.config import is_secret
    assert is_secret("NETBIRD_PAT") and is_secret("WIKI_PASS") and is_secret("X_TOKEN")
    assert not is_secret("PATCHMON_URL") and not is_secret("HOST_PATH") and not is_secret("TZ")
    assert is_secret("ODD_NAME", credentials={"ODD_NAME"})


def test_egress_rule_order():
    r = lambda host, port=443, **kw: tclib.egress_rule(host, port, **{"default": ["api.anthropic.com"], **kw})
    assert r("api.anthropic.com", block=["api.anthropic.com"]) == ("default", "built-in")  # never blockable
    assert r("x.test", block=["x.test"], allow=["x.test"], tentacles={("x.test", 443): "svc"})[0] == "blocked"
    assert r("x.test", allow=["*.test"], tentacles={("x.test", 443): "svc"}) == ("allow", "own allowlist")
    assert r("x.test", tentacles={("x.test", None): "box"}) == ("tentacle", "box")
    assert r("x.test", grants={"x.test:443": 2e9}, now=1e9)[0] == "grant"
    assert r("x.test", grants={"x.test:443": 1e9}, now=2e9) == ("unknown", "")
    assert r("y.test", ips=["10.1.2.3"], block=["10.0.0.0/8"])[0] == "blocked"  # resolved address in a blocked range
