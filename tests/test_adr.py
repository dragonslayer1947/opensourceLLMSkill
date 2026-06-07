from devagent.knowledge import adr as adr_mod
from devagent.knowledge.adr import _parse_violations


def _write(root, name, body):
    d = adr_mod.adr_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def test_sample_roundtrip(tmp_path):
    adr_mod.write_sample(tmp_path)
    adrs = adr_mod.load_adrs(tmp_path)
    assert len(adrs) == 1
    a = adrs[0]
    assert a.id == "0001" and a.is_active and a.title.startswith("Use cursor")
    assert a.constraints and a.constraints[0].severity == "block"


def test_active_filter(tmp_path):
    _write(tmp_path, "a.yaml", 'id: "1"\ntitle: A\nstatus: accepted\n')
    _write(tmp_path, "b.yaml", 'id: "2"\ntitle: B\nstatus: draft\n')
    adrs = adr_mod.load_adrs(tmp_path)
    assert len(adrs) == 2 and len(adr_mod.active(adrs)) == 1


def test_constraints_context_includes_only_accepted(tmp_path):
    _write(tmp_path, "a.yaml", 'id: "1"\ntitle: Cursor pagination\nstatus: accepted\n'
                               'decision: Use cursors.\n')
    _write(tmp_path, "b.yaml", 'id: "2"\ntitle: Draft thing\nstatus: draft\ndecision: nope.\n')
    ctx = adr_mod.constraints_context(adr_mod.load_adrs(tmp_path))
    assert "Cursor pagination" in ctx and "Draft thing" not in ctx


def test_parse_violations_array():
    text = 'sure: [{"adr_id":"0001","reason":"uses offset pagination"}]'
    v = _parse_violations(text)
    assert v == [{"adr_id": "0001", "reason": "uses offset pagination"}]


def test_parse_violations_empty():
    assert _parse_violations("[]") == []
    assert _parse_violations("no json") == []


def test_check_violations_no_active_returns_empty(tmp_path):
    # no accepted ADRs => no model call, empty result
    _write(tmp_path, "b.yaml", 'id: "2"\ntitle: B\nstatus: draft\n')
    adrs = adr_mod.load_adrs(tmp_path)

    class BoomRouter:
        def complete(self, *a, **k):
            raise AssertionError("must not call model when no active ADRs")

    assert adr_mod.check_violations(adrs, "some diff", BoomRouter()) == []


def test_set_status_transitions(tmp_path):
    adr_mod.write_sample(tmp_path)  # id 0001, status accepted
    assert adr_mod.set_status(tmp_path, "0001", "deprecated") is True
    adrs = adr_mod.load_adrs(tmp_path)
    assert adrs[0].status == "deprecated"
    assert adr_mod.active(adrs) == []  # deprecated no longer active


def test_set_status_invalid_raises(tmp_path):
    adr_mod.write_sample(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        adr_mod.set_status(tmp_path, "0001", "bogus")


def test_set_status_missing_returns_false(tmp_path):
    adr_mod.write_sample(tmp_path)
    assert adr_mod.set_status(tmp_path, "9999", "accepted") is False
