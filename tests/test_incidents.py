from devagent.knowledge import incidents as inc


def test_sample_roundtrip(tmp_path):
    inc.write_sample(tmp_path)
    items = inc.load_incidents(tmp_path)
    assert len(items) == 1 and items[0].id == "INC-001"
    assert "svc/checkout/charge.py" in items[0].files and items[0].lesson


def test_for_files_matches_full_path(tmp_path):
    inc.write_sample(tmp_path)
    items = inc.load_incidents(tmp_path)
    assert inc.for_files(items, ["svc/checkout/charge.py"]) == items


def test_for_files_matches_basename(tmp_path):
    inc.write_sample(tmp_path)
    items = inc.load_incidents(tmp_path)
    # different dir, same filename still matches
    assert inc.for_files(items, ["other/charge.py"])


def test_for_files_no_match(tmp_path):
    inc.write_sample(tmp_path)
    items = inc.load_incidents(tmp_path)
    assert inc.for_files(items, ["svc/unrelated.py"]) == []


def test_load_missing_empty(tmp_path):
    assert inc.load_incidents(tmp_path) == []
