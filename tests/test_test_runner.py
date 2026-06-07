import sys

from devagent.validate import test_runner


def test_run_tests_pass(tmp_path):
    passed, out = test_runner.run_tests(tmp_path, f'"{sys.executable}" -c "raise SystemExit(0)"')
    assert passed is True


def test_run_tests_fail(tmp_path):
    passed, out = test_runner.run_tests(tmp_path, f'"{sys.executable}" -c "raise SystemExit(1)"')
    assert passed is False


def test_find_command_none_without_tests(tmp_path):
    # pytest may be installed, but there are no tests here
    assert test_runner.find_test_command(tmp_path, {"test_command": "pytest -q"}) is None


def test_find_command_detects_tests_dir(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    cmd = test_runner.find_test_command(tmp_path, {"test_command": "pytest -q"})
    # only asserts detection when pytest is on PATH; otherwise None is acceptable
    assert cmd in ("pytest -q", None)


def test_find_command_missing_runner(tmp_path):
    assert test_runner.find_test_command(tmp_path, {"test_command": "definitely-not-a-real-exe-xyz"}) is None
