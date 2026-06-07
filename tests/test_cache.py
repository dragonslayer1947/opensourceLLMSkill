import time
from pathlib import Path

from devagent.context.cache import CACHE_FILE, build_index_cached, fingerprint


def _repo(tmp_path):
    (tmp_path / "a.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    return tmp_path


def test_cache_written_and_reused(tmp_path):
    _repo(tmp_path)
    idx1 = build_index_cached(tmp_path)
    assert (tmp_path / CACHE_FILE).exists()
    idx2 = build_index_cached(tmp_path)
    names1 = {s.name for f in idx1.files for s in f.symbols}
    names2 = {s.name for f in idx2.files for s in f.symbols}
    assert names1 == names2 == {"one"}


def test_fingerprint_changes_on_edit(tmp_path):
    _repo(tmp_path)
    fp1 = fingerprint(tmp_path)
    time.sleep(0.01)
    (tmp_path / "a.py").write_text("def one():\n    return 2\n# extra\n", encoding="utf-8")
    assert fingerprint(tmp_path) != fp1


def test_cache_invalidates_on_new_symbol(tmp_path):
    _repo(tmp_path)
    build_index_cached(tmp_path)  # warm cache
    (tmp_path / "b.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    idx = build_index_cached(tmp_path)  # fingerprint differs -> rebuild
    names = {s.name for f in idx.files for s in f.symbols}
    assert names == {"one", "two"}


def test_corrupt_cache_falls_back(tmp_path):
    _repo(tmp_path)
    cache = tmp_path / CACHE_FILE
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{ not json", encoding="utf-8")
    idx = build_index_cached(tmp_path)  # should rebuild, not raise
    assert {s.name for f in idx.files for s in f.symbols} == {"one"}
