from dataclasses import dataclass, field

from devagent.context.index import build_index
from devagent.longhorizon import conflict


@dataclass
class T:
    id: str
    target_files: list = field(default_factory=list)


def _coupled_repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    (pkg / "user.py").write_text("from pkg.core import base\n\ndef u():\n    return base()\n",
                                 encoding="utf-8")
    return tmp_path


def test_direct_file_conflict_is_blocking():
    conflicts = conflict.detect([T("a", ["x.py"]), T("b", ["x.py"])])
    assert len(conflicts) == 1
    assert conflicts[0].kind == "direct" and conflicts[0].severity == "block"
    assert conflict.has_blocking(conflicts)


def test_no_conflict_for_disjoint_files():
    assert conflict.detect([T("a", ["x.py"]), T("b", ["y.py"])]) == []


def test_coupling_conflict_via_import_graph(tmp_path):
    repo = _coupled_repo(tmp_path)
    idx = build_index(repo)
    # task a edits core (which user imports), task b edits user → coupling
    conflicts = conflict.detect([T("a", ["pkg/core.py"]), T("b", ["pkg/user.py"])], index=idx)
    kinds = {c.kind for c in conflicts}
    assert "coupling" in kinds


def test_reservation_conflict_flags_held_file():
    res = [{"resource": "file:billing.py", "owner": "team-pay", "session_id": "other"}]
    conflicts = conflict.detect([T("a", ["billing.py"])], reservations=res, self_session="me")
    assert any(c.kind == "reservation" and c.severity == "block" for c in conflicts)


def test_reservation_held_by_self_is_ignored():
    res = [{"resource": "file:billing.py", "owner": "me", "session_id": "me"}]
    conflicts = conflict.detect([T("a", ["billing.py"])], reservations=res, self_session="me")
    assert not any(c.kind == "reservation" for c in conflicts)
