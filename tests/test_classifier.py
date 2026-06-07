from devagent.orchestration.classifier import classify


def _c(task, **kw):
    base = dict(in_envelope=True, est_tokens=500, max_context_tokens=12000, has_pattern=True)
    base.update(kw)
    return classify(task, **base)


def test_small_known_task_is_direct():
    d = _c("rename get_user to fetch_user", has_pattern=True)
    assert d.route == "direct" and d.score == 0.0 and d.confidence > 0.9


def test_no_pattern_alone_stays_direct():
    d = _c("add a helper to format dates", has_pattern=False)  # only +3.0
    assert d.route == "direct" and d.signals.get("no_existing_pattern")


def test_security_plus_cross_service_plus_nopattern_routes_plan():
    d = _c("refactor authentication across all services", has_pattern=False)
    # no_pattern 3.0 + cross_service 2.5 + security 2.0 = 7.5 >= 6
    assert d.route == "plan_execute"
    assert d.signals.get("security_surface") and d.signals.get("cross_service")


def test_out_of_envelope_forces_plan():
    d = _c("tiny tweak", in_envelope=False, has_pattern=True)
    assert d.route == "plan_execute"
    assert "out of parity envelope" in d.reasons


def test_ambiguity_signal():
    d = _c("improve it", has_pattern=True)   # vague verb + 'it' => <2 content terms
    assert d.signals.get("ambiguity")


def test_large_context_signal():
    d = _c("do the thing in module", est_tokens=10000, max_context_tokens=12000, has_pattern=True)
    assert d.signals.get("large_context")
