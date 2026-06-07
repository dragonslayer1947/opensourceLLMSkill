from dataclasses import dataclass

from devagent.knowledge import compliance
from devagent.validate.safety_rules import evaluate


@dataclass
class Change:
    path: str
    new: str = ""


def test_available_profiles():
    assert {"pci-dss", "soc2", "hipaa"} <= set(compliance.available())


def test_expand_unknown_is_empty():
    assert compliance.expand(["nope"]) == []


def test_pci_payment_requires_flag():
    rules = compliance.expand(["pci-dss"])
    v = evaluate([Change("svc/payment/charge.py", "x=1")], rules, flags=set())
    assert any(viol.rule_id == "pci-payment-review" and viol.severity == "block" for viol in v)
    # with the flag granted, that rule no longer blocks
    v2 = evaluate([Change("svc/payment/charge.py", "x=1")], rules, flags={"security-review"})
    assert not any(viol.rule_id == "pci-payment-review" for viol in v2)


def test_pci_blocks_secret():
    rules = compliance.expand(["pci-dss"])
    v = evaluate([Change("c.py", "api_key = 'abcdef123'")], rules, flags=set())
    assert any(viol.rule_id == "pci-no-secret" for viol in v)


def test_soc2_auth_requires_access_review():
    rules = compliance.expand(["soc2"])
    v = evaluate([Change("app/auth/login.py", "x=1")], rules, flags=set())
    assert any(viol.rule_id == "soc2-auth-review" and viol.severity == "block" for viol in v)


def test_profiles_compose():
    rules = compliance.expand(["pci-dss", "soc2"])
    ids = {r.id for r in rules}
    assert "pci-payment-review" in ids and "soc2-auth-review" in ids
