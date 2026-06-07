"""Whole-changeset-vs-intent review (gap #8)."""
from dataclasses import dataclass

from devagent.review import reviewer


class _Router:
    def __init__(self, text):
        self.text = text

    def complete(self, *a, **k):
        @dataclass
        class R:
            text: str
            model_name: str = "reviewer"
            tier: str = "cli"
            tokens_in: int = 5
            tokens_out: int = 5
            cost_usd: float = 0.0
        return R(self.text)


def test_changeset_review_flags_goal_not_met():
    r = _Router('[{"severity":"high","category":"completeness",'
                '"message":"the POST /orders route is never registered on the app"}]')
    findings, meta = reviewer.review_changeset(
        "add an orders endpoint", "=== api.py ===\n<code>", ["s1: add OrderRepo"], r)
    assert reviewer.has_blocking(findings)
    assert "registered" in findings[0].message and meta["model"] == "reviewer"


def test_changeset_review_clean_passes():
    findings, _ = reviewer.review_changeset("x", "=== a.py ===\ncode", ["s1: x"], _Router("[]"))
    assert findings == [] and not reviewer.has_blocking(findings)


def test_changeset_review_empty_changeset_skips():
    findings, meta = reviewer.review_changeset("x", "   ", ["s1"], _Router("[]"))
    assert findings == [] and meta == {}
