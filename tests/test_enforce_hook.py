from devagent.hooks.enforce_local import enforcement_active, should_block


def _repo(tmp_path, enforce: bool):
    (tmp_path / ".git").mkdir()
    src = tmp_path / "src"
    src.mkdir()
    app = src / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    if enforce:
        (tmp_path / ".devagent").mkdir()
        (tmp_path / ".devagent" / "ENFORCE").write_text("on\n", encoding="utf-8")
    return app


def test_blocks_source_edit_when_enforced(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.delenv("DEVAGENT_ENFORCE", raising=False)
    app = _repo(tmp_path, enforce=True)
    assert should_block("Edit", str(app), str(tmp_path)) is True
    assert should_block("Write", str(app), str(tmp_path)) is True


def test_allows_when_no_sentinel(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.delenv("DEVAGENT_ENFORCE", raising=False)
    app = _repo(tmp_path, enforce=False)
    assert should_block("Edit", str(app), str(tmp_path)) is False


def test_allows_non_source_files(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.delenv("DEVAGENT_ENFORCE", raising=False)
    _repo(tmp_path, enforce=True)
    readme = tmp_path / "README.md"
    readme.write_text("# x\n", encoding="utf-8")
    assert should_block("Write", str(readme), str(tmp_path)) is False


def test_allows_devagent_and_plan_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.delenv("DEVAGENT_ENFORCE", raising=False)
    _repo(tmp_path, enforce=True)
    assert should_block("Write", str(tmp_path / ".devagent" / "plans" / "p.yaml"),
                        str(tmp_path)) is False
    assert should_block("Write", str(tmp_path / "plan.json"), str(tmp_path)) is False


def test_ignores_non_edit_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.delenv("DEVAGENT_ENFORCE", raising=False)
    app = _repo(tmp_path, enforce=True)
    assert should_block("Read", str(app), str(tmp_path)) is False
    assert should_block("Bash", "", str(tmp_path)) is False


def test_bypass_env_overrides(tmp_path, monkeypatch):
    app = _repo(tmp_path, enforce=True)
    monkeypatch.setenv("DEVAGENT_BYPASS", "1")
    assert should_block("Edit", str(app), str(tmp_path)) is False


def test_enforce_env_forces_on(tmp_path, monkeypatch):
    app = _repo(tmp_path, enforce=False)  # no sentinel
    monkeypatch.delenv("DEVAGENT_BYPASS", raising=False)
    monkeypatch.setenv("DEVAGENT_ENFORCE", "1")
    assert enforcement_active(str(app), str(tmp_path)) is True
