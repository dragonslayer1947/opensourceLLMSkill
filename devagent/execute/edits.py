"""Search/replace edit blocks — a robust edit format for mid-size local models.

The model emits blocks like:

    path/to/file.py
    <<<<<<< SEARCH
    def old():
        ...
    =======
    def new():
        ...
    >>>>>>> REPLACE

We parse them and apply each by exact match (falling back to whitespace-normalized match).
An empty SEARCH block means "create the file with this content"."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_BLOCK = re.compile(
    r"^(?P<path>[^\n`]+?)\n"
    r"<{5,7} SEARCH\s*\n"
    r"(?P<search>.*?)\n?"
    r"={5,7}\s*\n"
    r"(?P<replace>.*?)\n?"
    r">{5,7} REPLACE\s*$",
    re.DOTALL | re.MULTILINE,
)


@dataclass
class Edit:
    path: str
    search: str
    replace: str


@dataclass
class ApplyResult:
    ok: bool
    path: str
    reason: str = ""


def parse_edits(text: str) -> list[Edit]:
    edits = []
    for m in _BLOCK.finditer(text):
        edits.append(Edit(
            path=m.group("path").strip().strip("`").strip(),
            search=m.group("search"),
            replace=m.group("replace"),
        ))
    return edits


def _normalize(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.strip().splitlines())


def apply_edit(root: Path, edit: Edit) -> tuple[ApplyResult, str | None, str | None]:
    """Returns (result, old_text, new_text). old/new are file contents for diff/snapshot."""
    target = (root / edit.path).resolve()
    # safety: stay within the repo root
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return ApplyResult(False, edit.path, "path escapes repo root"), None, None

    if edit.search.strip() == "":
        old = target.read_text(encoding="utf-8") if target.exists() else None
        new = edit.replace
        return ApplyResult(True, edit.path, "create/overwrite"), old, new

    if not target.exists():
        return ApplyResult(False, edit.path, "file not found for SEARCH"), None, None

    old = target.read_text(encoding="utf-8")
    if edit.search in old:
        new = old.replace(edit.search, edit.replace, 1)
        return ApplyResult(True, edit.path, "exact"), old, new

    # Fallback: whitespace-normalized match.
    norm_search = _normalize(edit.search)
    lines = old.splitlines()
    for i in range(len(lines)):
        for j in range(i + 1, len(lines) + 1):
            if _normalize("\n".join(lines[i:j])) == norm_search:
                new = "\n".join(lines[:i] + edit.replace.splitlines() + lines[j:])
                if old.endswith("\n"):
                    new += "\n"
                return ApplyResult(True, edit.path, "normalized"), old, new
    return ApplyResult(False, edit.path, "SEARCH block did not match"), None, None
