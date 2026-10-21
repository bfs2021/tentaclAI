"""svc (the sandbox's service gateway) – pure functions, loaded from build/svc without running it."""
import importlib.machinery
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("svc", os.path.join(ROOT, "build", "svc"))
_spec = importlib.util.spec_from_loader("svc", _loader)
svc = importlib.util.module_from_spec(_spec)
_loader.exec_module(svc)


@pytest.mark.parametrize("text,hidden,kept", [
    ('"Env": ["POSTGRES_PASSWORD=hunter2", "TZ=Europe/Vienna"]', "hunter2", "TZ=Europe/Vienna"),
    ('"Env": ["N8N_API_KEY=abc123", "DB_URL=postgres://u:pw1@db/x"]', "abc123", "postgres://u:"),
    ('"Env": ["DB_URL=postgres://u:pw1@db/x"]', "pw1", "@db/x"),
    ('"traefik.http.middlewares.auth.basicauth.users": "me:$2y$05$hash"', "$2y$05$hash", "basicauth.users"),
    ("    environment:\n      MYSQL_ROOT_PASSWORD: s3cret\n      TZ: UTC\n", "s3cret", "TZ: UTC"),
    ("      - SECRET_KEY_BASE=xyz\n", "xyz", "SECRET_KEY_BASE="),
    ("Environment=FOO_TOKEN=t0k LANG=C", "t0k", "LANG=C"),
    ("ExecStart=/usr/bin/agent --api-key=k3y --port=80", "k3y", "--port=80"),
])
def test_redact(text, hidden, kept):
    out = svc.redact(text)
    assert hidden not in out and kept in out and "[redacted by svc]" in out


def test_redact_leaves_json_structure_alone():
    text = '{"Config": {"Env": ["TZ=UTC"], "AuthConfig": {}, "Image": "x"}}'
    assert svc.redact(text) == text


@pytest.mark.parametrize("words,compose,expected", [
    (["inspect", "pg"], False, True),
    (["container", "inspect", "pg"], False, True),
    (["docker", "inspect", "--format", "x", "pg"], False, True),
    (["sudo", "docker", "inspect", "pg"], False, False),  # svc strips the host's sudo prefix before asking
    (["logs", "-f", "pg"], False, False),
    (["ps"], False, False),
    (["config"], True, True),
    (["ps"], True, False),
    (["systemctl", "show", "x"], False, True),
    (["systemctl", "status", "x"], False, False),
])
def test_sensitive_read(words, compose, expected):
    assert svc.sensitive_read(words, compose=compose) is expected


def test_env_values_stay_out_of_the_process_environment(tmp_path, monkeypatch):
    """child processes (ssh, docker, compose, git) must not inherit the service tokens from .env"""
    f = tmp_path / ".env"
    f.write_text("SVC_TEST_SECRET=s3cret\nTZ=Europe/Vienna\n")
    monkeypatch.setattr(svc, "ENV_FILE", str(f))
    monkeypatch.delenv("SVC_TEST_SECRET", raising=False)
    svc.load_env_file()
    import os
    assert "SVC_TEST_SECRET" not in os.environ
    assert svc.render({"t": "${SVC_TEST_SECRET}"}) == {"t": "s3cret"}
    assert svc.missing_vars({"t": "${SVC_TEST_SECRET} ${NOPE_X}"}) == ["NOPE_X"]


def test_docker_runs_over_ssh_only(monkeypatch):
    monkeypatch.setattr(svc, "ssh_sudo", lambda alias: "")
    services = {"old": {"name": "old", "docker": {"mode": "socket", "socket": "/var/run/docker.sock", "stacks_dir": "/srv"}},
                "box": {"name": "box", "docker": {"mode": "ssh", "ssh_host": "box", "stacks_dir": "/srv"}}}
    with pytest.raises(SystemExit, match="over SSH only"):
        svc.get_docker(services, "old")
    d = svc.get_docker(services, "box")
    assert svc.docker_argv("box", d, ["ps"]) == ["ssh", "box", "docker ps"]
    assert svc.docker_target(d) == "ssh box"


def test_origin_from_the_kept_variables(monkeypatch):
    """only the checked values of the four kept variables – never the session access token"""
    import subprocess
    monkeypatch.setattr(svc, "_origin", None)
    monkeypatch.setattr(svc, "via_sudo", lambda: True)
    monkeypatch.setattr(svc, "as_user", lambda *a, **k: {})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "369797fa-acf1-46dc-9752-e0a7fdab4d35")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ACCESS_TOKEN", "must-not-travel")
    monkeypatch.setenv("TMUX", "/tmp/tmux-10000/default,123,0")
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "claude-workspace\n", ""))
    o = svc.origin()
    assert o == {"session": "369797fa-acf1-46dc-9752-e0a7fdab4d35", "entry": "cli", "tmux": "claude-workspace"}
    assert "must-not-travel" not in str(o)


def test_origin_drops_bad_values(monkeypatch):
    monkeypatch.setattr(svc, "_origin", None)
    monkeypatch.setattr(svc, "via_sudo", lambda: True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "<script>")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli; rm -rf /")
    monkeypatch.setenv("TMUX_PANE", "%3; id")
    monkeypatch.delenv("TMUX", raising=False)
    assert svc.origin() == {}
    monkeypatch.setattr(svc, "_origin", None)
    monkeypatch.setattr(svc, "via_sudo", lambda: False)
    assert svc.origin() == {"by": "root"}
