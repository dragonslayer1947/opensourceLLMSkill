"""Auto-updated continuity memory (gap #6)."""
from devagent.knowledge import continuity


def test_record_and_recall_relevant_entry(tmp_path):
    continuity.record(tmp_path, task="add the store singleton", files=["app/store.py"],
                      provides=["store: from app.store import store"], session_id="s1")
    continuity.record(tmp_path, task="add bookings api", files=["app/api.py"],
                      provides=[], session_id="s2")
    ctx = continuity.recent_context(tmp_path, ["app/store.py"])
    assert "store singleton" in ctx
    assert "from app.store import store" in ctx     # the interface is recalled for dependents
    assert len(continuity.load(tmp_path)) == 2


def test_recent_context_falls_back_to_recent_when_no_overlap(tmp_path):
    continuity.record(tmp_path, task="unrelated change", files=["x.py"], provides=[], session_id="s")
    ctx = continuity.recent_context(tmp_path, ["totally/other.py"])
    assert "unrelated change" in ctx   # no file overlap -> still surfaces recent history


def test_entries_are_capped(tmp_path):
    for i in range(continuity.MAX_ENTRIES + 15):
        continuity.record(tmp_path, task=f"t{i}", files=["x.py"], provides=[], session_id=str(i))
    assert len(continuity.load(tmp_path)) == continuity.MAX_ENTRIES


def test_empty_is_safe(tmp_path):
    assert continuity.recent_context(tmp_path) == ""
    assert continuity.load(tmp_path) == []
