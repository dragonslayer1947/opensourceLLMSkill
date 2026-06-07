"""Impact-scoped verification — the integration gate (gap #1).

The per-subtask gate proves each change compiles / lints / type-checks. It CANNOT prove the change
didn't break a distant caller — interface drift, a missing field, a broken contract. (The live AC
build proved this: ruff passed, yet `main.py` imported the wrong thing, a test asserted membership
wrong, and a `Slot` was built without a required field.)

After a run applies, this selects the tests that COVER the blast radius — the changed files plus
everything that transitively imports them — and runs just those as a final integration gate.
Scoped, so it stays fast on a large repo; if nothing specific is found it falls back to the whole
suite (when tests exist). Run via `python -m pytest` semantics with the repo root on PYTHONPATH so
`import yourpkg` resolves regardless of how the suite is invoked."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..context.index import RepoIndex
from ..planning import blast_radius


@dataclass
class ImpactResult:
    passed: bool
    scope: str                       # impacted | suite | skipped
    output: str = ""
    ran: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.scope == "impacted":
            return f"impact gate: ran {len(self.ran)} test file(s) covering the change"
        if self.scope == "suite":
            return "impact gate: no targeted tests found — ran the whole suite"
        return "impact gate: skipped (no runnable tests)"


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (name.startswith("test_") or name.endswith("_test.py")
            or "/tests/" in "/" + rel or rel.startswith("tests/"))


def impacted_modules(index: RepoIndex, changed: list[str]) -> tuple[set[str], set[str]]:
    """(impact_files, module_name_tokens) for changed files + their transitive dependents."""
    br = blast_radius.analyze(index, changed, warn=10**9, block=10**9)
    files = {c.replace("\\", "/") for c in changed} | set(br.affected)
    mods: set[str] = set()
    for f in files:
        mods.update(blast_radius._module_keys(f))
    return files, mods


def select_impacted_tests(index: RepoIndex, changed: list[str]) -> list[str]:
    """Test files that either changed themselves or import any module in the blast radius."""
    impact_files, mods = impacted_modules(index, changed)
    selected: set[str] = set()
    for fe in index.files:
        if not _is_test_file(fe.rel):
            continue
        if fe.rel in impact_files:
            selected.add(fe.rel)
            continue
        for imp in getattr(fe, "imports", []) or []:
            if imp in mods or imp.split(".")[-1] in mods:
                selected.add(fe.rel)
                break
    return sorted(selected)


def _run(root: Path, argv: list[str], timeout: int) -> tuple[bool, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                           timeout=timeout, env=env)
    except FileNotFoundError:
        return True, "test runner not found (skipped)"
    except subprocess.TimeoutExpired:
        return False, "tests timed out"
    return p.returncode == 0, (p.stdout + p.stderr)[-3000:]


def verify_impact(root: Path, changed: list[str], index: RepoIndex, gate_cfg: dict,
                  timeout: int = 600) -> ImpactResult:
    """Final integration gate: run the tests covering the blast radius (or the whole suite if no
    targeted tests are found). Uses pytest via the current interpreter so imports resolve."""
    cmd = gate_cfg.get("test_command", "pytest -q")
    parts = shlex.split(cmd) if cmd else []
    exe = parts[0] if parts else ""
    if not exe or (exe != "pytest" and shutil.which(exe) is None):
        return ImpactResult(True, "skipped", "no test runner configured")

    is_pytest = exe == "pytest" or (parts[:2] == [Path(sys.executable).name, "-m"] and "pytest" in parts)
    base = [sys.executable, "-m", "pytest", *parts[1:]] if exe == "pytest" else parts

    selected = select_impacted_tests(index, changed) if is_pytest else []
    if selected:
        passed, out = _run(root, [*base, *selected], timeout)
        return ImpactResult(passed, "impacted", out, selected)

    from .test_runner import _has_python_tests
    if is_pytest and _has_python_tests(root):
        passed, out = _run(root, base, timeout)
        return ImpactResult(passed, "suite", out)
    return ImpactResult(True, "skipped", "no impacted tests / no suite")
