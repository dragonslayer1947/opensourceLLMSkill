from devagent.validate.gate import run_gate


def test_gate_syntax_pass(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("def a():\n    return 1\n", encoding="utf-8")
    report = run_gate(tmp_path, ["ok.py"], {"run_types": False, "run_lint": False,
                                            "run_security": False, "run_tests": False})
    assert report.passed
    assert report.to_dict()["syntax"] == "pass"


def test_gate_syntax_fail(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def a(:\n    return\n", encoding="utf-8")
    report = run_gate(tmp_path, ["bad.py"], {"run_types": False, "run_lint": False,
                                             "run_security": False, "run_tests": False})
    assert not report.passed
    assert any(c.name == "syntax" and c.status == "fail" for c in report.checks)


def test_gate_missing_tool_is_skipped_not_fail(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    # request a tool that almost certainly isn't a real exe name
    report = run_gate(tmp_path, ["ok.py"], {"run_types": True, "run_lint": False,
                                            "run_security": False, "run_tests": False})
    types = [c for c in report.checks if c.name == "types"]
    assert types and types[0].status in ("pass", "skipped")  # never 'fail' merely for absence
    assert report.passed  # syntax ok + types pass/skipped
