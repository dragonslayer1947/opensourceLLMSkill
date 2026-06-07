from devagent.longhorizon import reservation as r


def test_reserve_then_active(tmp_path):
    res, conflict = r.reserve(tmp_path, "service:payments", "team-a", "s1")
    assert conflict is None and res.owner == "team-a"
    active = r.active(tmp_path)
    assert len(active) == 1 and active[0].resource == "service:payments"


def test_other_owner_conflicts(tmp_path):
    r.reserve(tmp_path, "service:payments", "team-a", "s1")
    res, conflict = r.reserve(tmp_path, "service:payments", "team-b", "s2")
    assert res is None and conflict is not None and conflict.owner == "team-a"


def test_same_owner_is_idempotent_refresh(tmp_path):
    r.reserve(tmp_path, "table:orders", "team-a", "s1", now=1000.0)
    res, conflict = r.reserve(tmp_path, "table:orders", "team-a", "s1", now=2000.0)
    assert conflict is None and res.acquired_at == 2000.0


def test_expired_reservation_is_reclaimable(tmp_path):
    r.reserve(tmp_path, "service:x", "team-a", "s1", ttl_seconds=10, now=1000.0)
    # long after expiry, team-b can take it
    res, conflict = r.reserve(tmp_path, "service:x", "team-b", "s2", now=5000.0)
    assert conflict is None and res.owner == "team-b"


def test_active_excludes_expired(tmp_path):
    r.reserve(tmp_path, "service:x", "team-a", "s1", ttl_seconds=10, now=1000.0)
    assert r.active(tmp_path, now=5000.0) == []


def test_release_only_by_owner(tmp_path):
    r.reserve(tmp_path, "service:x", "team-a", "s1")
    assert r.release(tmp_path, "service:x", "team-b") is False
    assert r.release(tmp_path, "service:x", "team-a") is True
    assert r.active(tmp_path) == []


def test_prune_removes_expired(tmp_path):
    r.reserve(tmp_path, "a", "t", "s", ttl_seconds=10, now=1000.0)
    r.reserve(tmp_path, "b", "t", "s", ttl_seconds=10000, now=1000.0)
    removed = r.prune(tmp_path, now=5000.0)
    assert removed == 1 and len(r.load_reservations(tmp_path)) == 1
