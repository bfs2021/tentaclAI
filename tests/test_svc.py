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
