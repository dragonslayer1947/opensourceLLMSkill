import pytest

from devagent.hooks import enforce_local
from devagent.hooks.enforce_local import enforcement_active, should_block


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.delenv("DEVAGENT_ENFORCE", raising=False)
    # isolate from the real machine's global flag: point it at a nonexistent path → default ON.
    monkeypatch.setattr(enforce_local, "global_state_path", lambda: tmp_path / "no-global-flag")


def _repo(tmp_path, *, disable=False, force=False):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    app = tmp_path / "src" / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    dev = tmp_path / ".devagent"
    if disable or force:
        dev.mkdir()
        if disable:
            (dev / "DISABLE").write_text("off\n", encoding="utf-8")
        if force:
            (dev / "ENFORCE").write_text("on\n", encoding="utf-8")
    return app


def test_blocks_source_edit_by_default(tmp_path):
    """Default ON: a source edit is blocked even with no sentinel."""
    app = _repo(tmp_path)
    assert should_block("Edit", str(app), str(tmp_path)) is True
    assert should_block("Write", str(app), str(tmp_path)) is True


def test_repo_disable_opts_out(tmp_path):
    app = _repo(tmp_path, disable=True)
    assert should_block("Edit", str(app), str(tmp_path)) is False


def test_repo_enforce_forces_on_even_if_global_off(tmp_path, monkeypatch):
    monkeypatch.setattr(enforce_local, "is_globally_enabled", lambda: False)
    app = _repo(tmp_path, force=True)
    assert should_block("Edit", str(app), str(tmp_path)) is True


def test_global_off_allows_when_no_repo_override(tmp_path, monkeypatch):
    monkeypatch.setattr(enforce_local, "is_globally_enabled", lambda: False)
    app = _repo(tmp_path)
    assert should_block("Edit", str(app), str(tmp_path)) is False


def test_allows_non_source_files(tmp_path):
    _repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# x\n", encoding="utf-8")
    assert should_block("Write", str(readme), str(tmp_path)) is False


def test_allows_devagent_and_plan_paths(tmp_path):
    _repo(tmp_path)
    assert should_block("Write", str(tmp_path / ".devagent" / "plans" / "p.yaml"), str(tmp_path)) is False
    assert should_block("Write", str(tmp_path / "plan.json"), str(tmp_path)) is False


def test_ignores_non_edit_tools(tmp_path):
    app = _repo(tmp_path)
    assert should_block("Read", str(app), str(tmp_path)) is False
    assert should_block("Bash", "", str(tmp_path)) is False


def test_bypass_env_overrides(tmp_path, monkeypatch):
    app = _repo(tmp_path)
    monkeypatch.setenv("DEVAGENT_BYPASS", "1")
    assert should_block("Edit", str(app), str(tmp_path)) is False


def test_env_enforce_zero_disables(tmp_path, monkeypatch):
    app = _repo(tmp_path)
    monkeypatch.setenv("DEVAGENT_ENFORCE", "0")
    assert enforcement_active(str(app), str(tmp_path)) is False


def test_global_toggle_roundtrip(tmp_path, monkeypatch):
    """set_global_enabled writes/removes the global flag; default (no file) means enabled."""
    flag = tmp_path / "enforce-disabled"
    monkeypatch.setattr(enforce_local, "global_state_path", lambda: flag)
    assert enforce_local.is_globally_enabled() is True       # default: no file → ON
    enforce_local.set_global_enabled(False)
    assert flag.exists() and enforce_local.is_globally_enabled() is False
    enforce_local.set_global_enabled(True)
    assert not flag.exists() and enforce_local.is_globally_enabled() is True
