"""Atomic cross-cutting rename (Tier-1, deep fix for #3).

The coordination directive (planning/crosscut.py) helps the model apply a wide rename
consistently, but it's still file-by-file and model-dependent. For the most common cross-cutting
change — renaming a symbol used across many files — a DETERMINISTIC, all-or-nothing transaction is
both safer and free: rewrite the identifier everywhere via the tokenizer (so strings/comments are
untouched and formatting is preserved), gate the WHOLE set, and roll back ALL files if anything
fails. No partial, half-renamed tree can ever exist.

Python uses `tokenize` (identifier-accurate). Other languages fall back to a word-boundary regex
(best-effort). New atomic changesets for signature changes are a follow-up; rename is the 80%."""
from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from ..context.index import build_index
from ..execute import apply as ap
from ..validate import interface as interface_mod
from ..validate.gate import run_gate


def _rewrite_python(text: str, old: str, new: str) -> tuple[str, int]:
    """Replace whole-identifier `old` with `new`, leaving strings/comments and formatting intact."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text, 0
    spots = [(t.start, t.end) for t in toks if t.type == tokenize.NAME and t.string == old]
    if not spots:
        return text, 0
    lines = text.splitlines(keepends=True)
    for (srow, scol), (_erow, ecol) in sorted(spots, reverse=True):
        line = lines[srow - 1]
        lines[srow - 1] = line[:scol] + new + line[ecol:]
    return "".join(lines), len(spots)


def _rewrite_regex(text: str, old: str, new: str) -> tuple[str, int]:
    pat = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w])")
    new_text, n = pat.subn(new, text)
    return new_text, n


def rewrite(text: str, old: str, new: str, is_python: bool) -> tuple[str, int]:
    return _rewrite_python(text, old, new) if is_python else _rewrite_regex(text, old, new)


@dataclass
class RenameResult:
    old: str
    new: str
    changed: list[str] = field(default_factory=list)
    occurrences: int = 0
    ok: bool = True
    output: str = ""
    rolled_back: bool = False


def plan_rename(root: Path, old: str, new: str, index=None) -> list[ap.FileChange]:
    """Compute the edits the rename would make across the repo (no writes)."""
    index = index or build_index(root)
    changes: list[ap.FileChange] = []
    for f in index.files:
        if f.lang not in ("py", "js"):
            continue
        try:
            text = f.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new_text, n = rewrite(text, old, new, is_python=(f.lang == "py"))
        if n:
            changes.append(ap.FileChange(f.rel, text, new_text, f"rename {old}->{new} ({n}x)"))
    return changes


def apply_rename(root: Path, old: str, new: str, gate_cfg: dict, snap_dir: Path,
                 index=None) -> RenameResult:
    """Apply the rename atomically: snapshot → write all → gate the whole set (syntax/lint +
    cross-file interface resolution). Any failure rolls back EVERY file (all-or-nothing)."""
    if not (old.isidentifier() and new.isidentifier()):
        return RenameResult(old, new, ok=False, output="old and new must be identifiers")
    changes = plan_rename(root, old, new, index)
    if not changes:
        return RenameResult(old, new, ok=True, output="no occurrences found", occurrences=0)

    rels = [c.path for c in changes]
    occ = sum(int(re.search(r"\((\d+)x\)", c.reason).group(1)) for c in changes)
    ap.snapshot(root, snap_dir, changes)
    ap.write_changes(root, changes)

    gate = run_gate(root, rels, gate_cfg)
    drift = interface_mod.issues_touching(root, rels)
    if gate.passed and not drift:
        return RenameResult(old, new, changed=rels, occurrences=occ, ok=True,
                            output="rename applied + gate passed")
    ap.undo_from_snapshot(root, snap_dir)
    detail = gate.render() + ("\n" + "\n".join(drift) if drift else "")
    return RenameResult(old, new, changed=rels, occurrences=occ, ok=False,
                        output=detail, rolled_back=True)
