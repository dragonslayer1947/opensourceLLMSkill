from dataclasses import dataclass

from devagent import report
from devagent.config import Pricing


@dataclass
class Call:
    model: str
    tier: str
    tin: int
    tout: int
    cost_usd: float = 0.0


PRICING = {"local": Pricing(0, 0), "sonnet": Pricing(3, 15), "cli": Pricing(0, 0)}


def test_no_budget_is_unlimited():
    calls = [Call("local", "local", 100000, 100000)]
    assert report.over_budget(calls, {}, PRICING) is None
    assert report.over_budget(calls, {"token_budget_session": 0}, PRICING) is None


def test_token_budget_trips():
    calls = [Call("local", "local", 6000, 6000)]  # 12000 tokens
    assert report.over_budget(calls, {"token_budget_session": 10000}, PRICING) is not None
    assert report.over_budget(calls, {"token_budget_session": 20000}, PRICING) is None


def test_cost_budget_trips_on_counterfactual():
    # local tokens priced at sonnet counterfactual: 1M in => $3
    calls = [Call("local", "local", 1_000_000, 0)]
    reason = report.over_budget(calls, {"cost_budget_usd": 1.0}, PRICING, local_ref="sonnet")
    assert reason and "budget" in reason


def test_cli_cost_counts_toward_budget():
    calls = [Call("cli", "cli", 10, 10, cost_usd=0.5)]
    assert report.over_budget(calls, {"cost_budget_usd": 0.25}, PRICING) is not None
    assert report.over_budget(calls, {"cost_budget_usd": 1.0}, PRICING) is None
