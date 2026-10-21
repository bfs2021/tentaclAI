"""Claude → Instructions / Memory / settings.json: managed parts stay, secrets stay masked, site rules stay readable."""
import json
import os
import stat

import pytest


@pytest.fixture
def cf(app_module):
    from app import claude_files
    return claude_files


CURRENT = {"model": "opus", "effortLevel": "high", "skillOverrides": {"x": "off"}, "theme": "dark",
           "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet", "DISABLE_TELEMETRY": "1", "MY_API_TOKEN": "s3cret", "LANG": "C"}}


def test_editable_hides_managed_and_masks_secrets(cf):
    e = cf.editable(CURRENT)
    assert e == {"theme": "dark", "env": {"MY_API_TOKEN": cf.MASK, "LANG": "C"}}
    assert "s3cret" not in json.dumps(e)


def test_merge_keeps_managed_parts_and_masked_values(cf):
    edited = json.dumps({"theme": "light", "language": "german", "env": {"MY_API_TOKEN": cf.MASK, "FOO": "1"}})
    out = cf.merge(CURRENT, edited)
    assert out["theme"] == "light" and out["language"] == "german"
    assert out["model"] == "opus" and out["effortLevel"] == "high" and out["skillOverrides"] == {"x": "off"}
    assert out["env"] == {"MY_API_TOKEN": "s3cret", "FOO": "1", "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet", "DISABLE_TELEMETRY": "1"}


def test_merge_drops_removed_user_entries(cf):
    out = cf.merge(CURRENT, "{}")
    assert "theme" not in out and out["env"] == {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet", "DISABLE_TELEMETRY": "1"}


@pytest.mark.parametrize("text,msg", [
    ("{", "invalid JSON"), ("[]", "JSON object"), ('{"env": []}', "object of name"),
    ('{"model": "haiku"}', "Models page"), ('{"skillOverrides": {}}', "Skills page"),
    ('{"env": {"DISABLE_TELEMETRY": "0"}}', "CLAUDE_TELEMETRY"), ('{"env": {"NEW_TOKEN": "[hidden – unchanged]"}}', "enter a value"),
])
def test_merge_rejects(cf, text, msg):
    with pytest.raises(ValueError, match=msg):
        cf.merge(CURRENT, text)


def test_save_local_backup_mode_and_toggle(cf, tmp_path, monkeypatch):
    p = tmp_path / "local" / "CLAUDE.md"
    monkeypatch.setattr(cf, "LOCAL", str(p))
    assert cf.save_local("# rules") is True  # appeared → the generated CLAUDE.md must import it
    assert p.read_text() == "# rules\n" and stat.S_IMODE(os.stat(p).st_mode) == 0o644
    assert cf.save_local("# rules\nmore\n") is False
    assert len(list(p.parent.glob("CLAUDE.md.bak-*"))) == 1
    assert cf.save_local("# rules\nmore\n") is False  # unchanged: no new backup
    assert cf.save_local("") is True  # emptied → the import goes


def test_save_local_too_large(cf, tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "LOCAL", str(tmp_path / "CLAUDE.md"))
    with pytest.raises(ValueError):
        cf.save_local("x" * (cf.MAX_SIZE + 1))


@pytest.mark.parametrize("project,name", [("../x", "a.md"), ("-workspace", "../a.md"), ("-workspace", "a.txt"),
                                          ("..", "a.md"), ("-workspace", "")])
def test_forget_validates_names(cf, project, name):
    with pytest.raises(ValueError, match="invalid"):
        cf.forget(project, name)
