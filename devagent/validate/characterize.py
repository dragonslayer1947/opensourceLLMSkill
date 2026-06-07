"""Characterization-test gate (Tier-1).

The gate's correctness floor is only as high as the tests that already exist. On a large repo most
code has none — so the local model can produce something that lints, type-checks, and is
LOGICALLY WRONG on an untested path, and nothing catches it.

This pins the CURRENT behavior of untested code before we touch it: generate tests, run them
against the unchanged code, and KEEP only the ones that pass (that's what makes them
characterization tests — they describe what the code does today, right or wrong). Those pinned
tests then ride the normal impact/test gate, so any subtask that changes the observed behavior is
caught and rolled back instead of silently shipping.

Model + execution are injected (`generate`, `run_test`) so the policy is testable offline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath


def char_test_path(src_rel: str) -> str:
    """A distinct path so we never clobber a hand-written test for the same module."""
    return f"tests/test_{PurePosixPath(src_rel).stem}_characterization.py"


def find_untested(index, target_files: list[str]) -> list[str]:
    """Existing Python target files that NO test file imports — the coverage blind spots a change
    here could silently break. New files (not yet in the index) are skipped: nothing to pin."""
    targets = {t.replace("\\", "/") for t in target_files if t.endswith(".py")}
    if not targets:
        return []
    by_rel = {f.rel: f for f in index.files}

    def _is_test(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        return name.startswith("test_") or name.endswith("_test.py") or "tests/" in rel

    # module-name tokens each target is importable as
    from ..planning.blast_radius import _module_keys
    tested: set[str] = set()
    for f in index.files:
        if not _is_test(f.rel):
            continue
        imported = set(getattr(f, "imports", []) or [])
        imported |= {i.split(".")[-1] for i in imported}
        for tgt in targets:
            if tgt not in tested and set(_module_keys(tgt)) & imported:
                tested.add(tgt)
    return sorted(t for t in targets if t in by_rel and t not in tested)


@dataclass
class PinResult:
    src_rel: str
    test_path: str
    pinned: bool
    detail: str = ""


def pin(root: Path, src_rel: str, generate, run_test) -> PinResult:
    """Generate a characterization test for `src_rel`, run it against the CURRENT code, and keep it
    only if it passes (pins real behavior). `generate(src_rel, code) -> test_code`;
    `run_test(test_path) -> (passed, output)`. A non-pinning test is removed, never committed."""
    test_path = char_test_path(src_rel)
    src = root / src_rel
    try:
        code = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return PinResult(src_rel, test_path, False, "source unreadable")

    test_code = generate(src_rel, code)
    if not test_code or not test_code.strip():
        return PinResult(src_rel, test_path, False, "no test generated")

    dest = root / test_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(test_code, encoding="utf-8")

    passed, out = run_test(test_path)
    if not passed:
        # Can't pin behavior (test doesn't reflect current code) — don't leave a red test behind.
        dest.unlink(missing_ok=True)
        return PinResult(src_rel, test_path, False, "generated test did not pass on current code")
    return PinResult(src_rel, test_path, True, "pinned current behavior")


def pin_all(root: Path, index, target_files: list[str], generate, run_test) -> list[PinResult]:
    """Pin every untested existing target file. Returns one PinResult per attempt."""
    return [pin(root, rel, generate, run_test) for rel in find_untested(index, target_files)]
