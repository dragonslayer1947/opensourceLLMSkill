"""Cross-cutting change detection + coordination directive (Tier-1)."""
from devagent.planning import crosscut


def test_detects_rename_to():
    cc = crosscut.detect("rename get_user to fetch_user across the service")
    assert cc and cc.kind == "rename" and ("get_user", "fetch_user") in cc.renames


def test_detects_rename_arrow_and_backticks():
    cc = crosscut.detect("Rename `OldRepo` -> NewRepo everywhere")
    assert cc and ("OldRepo", "NewRepo") in cc.renames


def test_detects_replace_with():
    cc = crosscut.detect("replace getToken with readToken in all call sites")
    assert cc and ("getToken", "readToken") in cc.renames


def test_detects_signature_change():
    cc = crosscut.detect("change the signature of process_order to take an idempotency key")
    assert cc and cc.kind == "signature"


def test_detects_wide_phrasing_without_names():
    cc = crosscut.detect("update all usages of the legacy client throughout the codebase")
    assert cc and cc.kind == "wide"


def test_ignores_local_additive_task():
    assert crosscut.detect("add a /health endpoint returning build info") is None
    assert crosscut.detect("") is None


def test_directive_names_the_rename():
    cc = crosscut.detect("rename foo to bar everywhere")
    d = cc.directive()
    assert "foo" in d and "bar" in d and "Never leave a reference" in d
