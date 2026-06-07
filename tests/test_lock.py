import time

from devagent.execute import lock


def test_acquire_and_release(tmp_path):
    acquired, conflicts = lock.acquire(tmp_path, ["a.py", "b.py"], "s1")
    assert set(acquired) == {"a.py", "b.py"} and conflicts == []
    released = lock.release(tmp_path, acquired, "s1")
    assert released == 2


def test_conflict_with_other_session(tmp_path):
    lock.acquire(tmp_path, ["a.py"], "s1")
    acquired, conflicts = lock.acquire(tmp_path, ["a.py"], "s2")
    assert acquired == [] and len(conflicts) == 1
    assert conflicts[0][0] == "a.py" and conflicts[0][1]["session_id"] == "s1"


def test_reentrant_same_session(tmp_path):
    lock.acquire(tmp_path, ["a.py"], "s1")
    acquired, conflicts = lock.acquire(tmp_path, ["a.py"], "s1")
    assert conflicts == []  # same session may re-acquire


def test_stale_lock_is_reclaimed(tmp_path):
    lock.acquire(tmp_path, ["a.py"], "old")
    # acquire with a 0s staleness window => the existing lock is immediately stale
    time.sleep(0.01)
    acquired, conflicts = lock.acquire(tmp_path, ["a.py"], "new", stale_seconds=0)
    assert conflicts == [] and acquired == ["a.py"]


def test_atomic_partial_conflict_acquires_nothing(tmp_path):
    lock.acquire(tmp_path, ["b.py"], "s1")
    acquired, conflicts = lock.acquire(tmp_path, ["a.py", "b.py"], "s2")
    assert acquired == [] and len(conflicts) == 1
    # a.py must NOT have been locked by the failed attempt
    a2, c2 = lock.acquire(tmp_path, ["a.py"], "s3")
    assert a2 == ["a.py"] and c2 == []


def test_release_does_not_touch_other_session(tmp_path):
    lock.acquire(tmp_path, ["a.py"], "s1")
    assert lock.release(tmp_path, ["a.py"], "s2") == 0  # not ours
