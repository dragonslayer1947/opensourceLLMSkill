from devagent.validate import test_gen as tg


def test_derives_test_path():
    assert tg.test_path_for("svc/pricing.py") == "tests/test_pricing.py"
    assert tg.test_path_for("a/b/calc.py") == "tests/test_calc.py"


def test_extract_code_block_fenced():
    text = "Sure:\n```python\ndef test_x():\n    assert True\n```\nDone"
    code = tg.extract_code_block(text)
    assert code.startswith("def test_x():") and code.endswith("\n")


def test_extract_code_block_plain():
    code = tg.extract_code_block("def test_y():\n    assert 1\n")
    assert "def test_y()" in code
