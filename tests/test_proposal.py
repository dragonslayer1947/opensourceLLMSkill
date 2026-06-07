import json
from dataclasses import dataclass

import yaml

from devagent.knowledge import adr as adr_mod
from devagent.longhorizon import proposal


@dataclass
class FakeResult:
    text: str
    model_name: str = "fake-planner"
    tier: str = "cli"
    tokens_in: int = 5
    tokens_out: int = 10
    cost_usd: float = 0.0


class FakeRouter:
    def __init__(self, text):
        self.text = text

    def complete(self, *a, **k):
        return FakeResult(self.text)


PROP_JSON = {
    "title": "Use an outbox table for order events",
    "context": "Dual-writes risk inconsistency.",
    "decision": "Write events to an outbox table in the same transaction.",
    "alternatives": [{"option": "direct publish", "why_not": "loses atomicity"}],
    "consequences": ["a relay process drains the outbox"],
    "constraints": [{"rule": "Order writes must enqueue to the outbox", "severity": "block"}],
}


def test_propose_persists_as_proposed(tmp_path):
    p = proposal.propose(tmp_path, "reliable order events", FakeRouter(json.dumps(PROP_JSON)))
    assert p.status == "proposed" and p.id == "P-0001"
    assert "outbox" in p.title.lower()
    loaded = proposal.load_proposal(tmp_path, "P-0001")
    assert loaded and loaded.decision.startswith("Write events")


def test_set_decision_gate(tmp_path):
    proposal.propose(tmp_path, "g", FakeRouter(json.dumps(PROP_JSON)))
    p = proposal.set_decision(tmp_path, "P-0001", "approved", reviewer="neeraj")
    assert p.status == "approved" and p.reviewer == "neeraj"


def test_approve_promotes_to_adr(tmp_path):
    proposal.propose(tmp_path, "g", FakeRouter(json.dumps(PROP_JSON)))
    p = proposal.set_decision(tmp_path, "P-0001", "approved")
    adr_path = proposal.promote_to_adr(tmp_path, p)
    assert adr_path.exists()
    data = yaml.safe_load(adr_path.read_text(encoding="utf-8"))
    assert data["status"] == "accepted" and data["source_proposal"] == "P-0001"
    assert data["generates_constraints"][0]["severity"] == "block"
    # the promoted ADR is now loadable + active via the knowledge layer
    adrs = adr_mod.load_adrs(tmp_path)
    assert len(adr_mod.active(adrs)) == 1


def test_garbage_proposal_still_saves(tmp_path):
    p = proposal.propose(tmp_path, "do thing", FakeRouter("not json at all"))
    assert p.status == "proposed" and p.title == "do thing"


def test_next_id_increments(tmp_path):
    proposal.propose(tmp_path, "a", FakeRouter(json.dumps(PROP_JSON)))
    assert proposal.next_id(tmp_path) == "P-0002"
