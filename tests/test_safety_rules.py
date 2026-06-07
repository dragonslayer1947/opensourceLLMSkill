from dataclasses import dataclass

from devagent.validate import safety_rules
from devagent.validate.safety_rules import Rule, _glob_to_re, evaluate, load_rules, write_sample


@dataclass
class Change:
    path: str
    new: str = ""


def test_glob_matches_nested_and_top_level():
    rx = _glob_to_re("**/auth/**")
    assert rx.match("src/auth/login.py")
    assert rx.match("auth/login.py")          # leading separator optional
    assert not rx.match("src/authx/login.py")  # needs a real auth/ dir
    assert not rx.match("auth.py")


def test_star_does_not_cross_slash():
    rx = _glob_to_re("src/*.py")
    assert rx.match("src/a.py")
    assert not rx.match("src/sub/a.py")


def test_require_flag_blocks_without_flag():
    rules = [Rule(id="auth", action="require_flag", path_glob="**/auth/**", flag="sec")]
    v = evaluate([Change("app/auth/x.py", "code")], rules, flags=set())
    assert len(v) == 1 and v[0].severity == "block"


def test_require_flag_passes_with_flag():
    rules = [Rule(id="auth", action="require_flag", path_glob="**/auth/**", flag="sec")]
    v = evaluate([Change("app/auth/x.py", "code")], rules, flags={"sec"})
    assert v == []


def test_content_regex_block():
    rules = [Rule(id="secret", action="block",
                  content_regex=r"(?i)api_key\s*=\s*['\"]")]
    v = evaluate([Change("c.py", "API_KEY = 'abc123'")], rules, flags=set())
    assert len(v) == 1 and v[0].severity == "block"


def test_warn_is_not_block():
    rules = [Rule(id="todo", action="warn", content_regex="TODO")]
    v = evaluate([Change("c.py", "# TODO later")], rules, flags=set())
    assert len(v) == 1 and v[0].severity == "warn"


def test_rule_with_no_condition_never_matches():
    rules = [Rule(id="empty", action="block")]
    assert evaluate([Change("anything.py", "x")], rules, flags=set()) == []


def test_load_and_sample_roundtrip(tmp_path):
    p = write_sample(tmp_path)
    assert p.exists()
    rules = load_rules(tmp_path)
    ids = {r.id for r in rules}
    assert "auth-requires-review" in ids and "block-hardcoded-secret" in ids
    # the sample auth rule blocks an auth write without its flag
    v = evaluate([Change("svc/auth/login.py", "x=1")], rules, flags=set())
    assert any(viol.rule_id == "auth-requires-review" and viol.severity == "block" for viol in v)


def test_load_missing_returns_empty(tmp_path):
    assert load_rules(tmp_path) == []
