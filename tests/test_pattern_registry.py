from datetime import datetime, timedelta, timezone

from devagent.knowledge import pattern_registry as pr


def test_add_load_roundtrip(tmp_path):
    pr.add_pattern(tmp_path, "Cursor pagination", "use cursors", tags=["pagination", "api"])
    patterns = pr.load_patterns(tmp_path)
    assert len(patterns) == 1
    assert patterns[0].id == "cursor-pagination" and "pagination" in patterns[0].tags


def test_id_uniqueness(tmp_path):
    pr.add_pattern(tmp_path, "Repo Method")
    pr.add_pattern(tmp_path, "Repo Method")
    ids = {p.id for p in pr.load_patterns(tmp_path)}
    assert ids == {"repo-method", "repo-method-2"}


def test_confidence_decays_with_age(tmp_path):
    p = pr.add_pattern(tmp_path, "Old Pattern", confidence=0.8)
    now = datetime.now(timezone.utc)
    fresh = pr.effective_confidence(p, now)
    aged = pr.effective_confidence(p, now + timedelta(days=pr.HALF_LIFE_DAYS))
    assert abs(fresh - 0.8) < 0.05
    assert abs(aged - 0.4) < 0.05  # one half-life => halved


def test_deprecate_excludes_from_active(tmp_path):
    pr.add_pattern(tmp_path, "Temp Pattern", tags=["x"])
    assert len(pr.active_patterns(pr.load_patterns(tmp_path))) == 1
    pr.deprecate(tmp_path, "temp-pattern")
    assert pr.active_patterns(pr.load_patterns(tmp_path)) == []


def test_decayed_below_min_excluded(tmp_path):
    pr.add_pattern(tmp_path, "Faint", confidence=0.6)
    patterns = pr.load_patterns(tmp_path)
    way_future = datetime.now(timezone.utc) + timedelta(days=pr.HALF_LIFE_DAYS * 4)  # 0.6/16≈0.0375
    assert pr.active_patterns(patterns, now=way_future) == []


def test_relevant_matches_tags(tmp_path):
    pr.add_pattern(tmp_path, "Cursor pagination", "use cursors", tags=["pagination"])
    pr.add_pattern(tmp_path, "Retry policy", "exponential backoff", tags=["retry"])
    rel = pr.relevant(pr.load_patterns(tmp_path), "add pagination to the list endpoint")
    assert [p.id for p in rel] == ["cursor-pagination"]


def test_patterns_context_text(tmp_path):
    pr.add_pattern(tmp_path, "Cursor pagination", "use cursors", tags=["pagination"])
    ctx = pr.patterns_context(pr.load_patterns(tmp_path), "pagination please")
    assert "Cursor pagination" in ctx and "use cursors" in ctx


# --- write-time enforcement (V4) ---
from dataclasses import dataclass  # noqa: E402


@dataclass
class _Change:
    path: str
    new: str = ""


def test_enforce_flags_missing_pattern(tmp_path):
    pr.add_pattern(tmp_path, "Routes use cursor", tags=["api"],
                   enforce_glob="**/routes/*.py", enforce_regex="cursor",
                   enforce_severity="block")
    patterns = pr.load_patterns(tmp_path)
    v = pr.enforce_violations(patterns, [_Change("app/routes/products.py", "def list(): return []")])
    assert len(v) == 1 and v[0].severity == "block" and "cursor" in v[0].message


def test_enforce_satisfied(tmp_path):
    pr.add_pattern(tmp_path, "Routes use cursor", enforce_glob="**/routes/*.py",
                   enforce_regex="cursor")
    patterns = pr.load_patterns(tmp_path)
    v = pr.enforce_violations(patterns, [_Change("app/routes/p.py", "def list(cursor=0): ...")])
    assert v == []


def test_enforce_ignores_nonmatching_path(tmp_path):
    pr.add_pattern(tmp_path, "Routes use cursor", enforce_glob="**/routes/*.py",
                   enforce_regex="cursor")
    patterns = pr.load_patterns(tmp_path)
    assert pr.enforce_violations(patterns, [_Change("app/models.py", "x=1")]) == []


def test_enforce_skips_deprecated(tmp_path):
    pr.add_pattern(tmp_path, "Routes use cursor", enforce_glob="**/routes/*.py",
                   enforce_regex="cursor", enforce_severity="block")
    pr.deprecate(tmp_path, "routes-use-cursor")
    patterns = pr.load_patterns(tmp_path)
    assert pr.enforce_violations(patterns, [_Change("app/routes/p.py", "no match")]) == []
