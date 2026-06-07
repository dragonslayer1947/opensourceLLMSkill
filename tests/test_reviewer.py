from devagent.review.reviewer import Finding, has_blocking, parse_findings


def test_parse_findings():
    text = '[{"severity":"high","category":"security","message":"SQL injection in query"}]'
    f = parse_findings(text)
    assert len(f) == 1 and f[0].severity == "high" and f[0].category == "security"


def test_parse_findings_empty():
    assert parse_findings("[]") == []
    assert parse_findings("no json here") == []


def test_unknown_severity_defaults_low():
    f = parse_findings('[{"severity":"critical","message":"x"}]')
    assert f[0].severity == "low"


def test_skips_entries_without_message():
    f = parse_findings('[{"severity":"high"}, {"severity":"low","message":"ok"}]')
    assert len(f) == 1 and f[0].message == "ok"


def test_has_blocking():
    assert has_blocking([Finding("high", "bug", "x")])
    assert not has_blocking([Finding("medium", "x", "y"), Finding("low", "a", "b")])
    assert not has_blocking([])
