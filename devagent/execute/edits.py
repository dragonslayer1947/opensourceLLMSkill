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

import os
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


def _clean_path(raw: str) -> str:
    """Strip the decorations local models wrap paths in: backticks, angle brackets, quotes,
    and leading markdown markers. `<calc.py>`, `` `calc.py` ``, "src/x.py" → the bare path."""
    p = raw.strip().strip("`").strip()
    for lo, hi in (("<", ">"), ('"', '"'), ("'", "'")):
        if len(p) >= 2 and p.startswith(lo) and p.endswith(hi):
            p = p[1:-1].strip()
    return p.lstrip("#-* ").strip()


def parse_edits(text: str) -> list[Edit]:
    edits = []
    for m in _BLOCK.finditer(text):
        edits.append(Edit(
            path=_clean_path(m.group("path")),
            search=m.group("search"),
            replace=m.group("replace"),
        ))
    return edits


def _normalize(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.strip().splitlines())


def _repo_files(root: Path, cap: int = 20000):
    """Real files in the repo, junk dirs pruned (for tolerant path resolution)."""
    from ..context.index import _skip_dir
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if "pyvenv.cfg" in filenames:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        base = Path(dirpath)
        for fn in filenames:
            yield (base / fn).relative_to(root).as_posix()
            count += 1
            if count >= cap:
                return


def resolve_target_path(root: Path, edit_path: str) -> str:
    """Map a model-emitted path to a real repo file. Local models often copy the prompt's
    `path/to/...` placeholder or guess a directory; if the literal path doesn't exist we match by
    basename (preferring a file whose path matches the emitted tail). If nothing matches, the path
    is returned unchanged (a genuine new-file creation)."""
    if (root / edit_path).exists():
        return edit_path
    rel = edit_path.replace("\\", "/").strip("/")
    name = rel.rsplit("/", 1)[-1]
    matches = [r for r in _repo_files(root) if r.rsplit("/", 1)[-1] == name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        for r in matches:
            if r.endswith(rel) or rel.endswith(r):
                return r
        return matches[0]
    return edit_path


def apply_edit(root: Path, edit: Edit) -> tuple[ApplyResult, str | None, str | None]:
    """Returns (result, old_text, new_text). old/new are file contents for diff/snapshot."""
    # Tolerant path resolution for a non-create edit (a SEARCH must hit an existing file); a
    # create keeps the emitted path so new files land where intended.
    rel = resolve_target_path(root, edit.path) if edit.search.strip() else edit.path
    target = (root / rel).resolve()
    # safety: stay within the repo root
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return ApplyResult(False, rel, "path escapes repo root"), None, None

    if edit.search.strip() == "":
        old = target.read_text(encoding="utf-8") if target.exists() else None
        new = edit.replace
        return ApplyResult(True, rel, "create/overwrite"), old, new

    if not target.exists():
        return ApplyResult(False, rel, "file not found for SEARCH"), None, None

    old = target.read_text(encoding="utf-8")
    if edit.search in old:
        new = old.replace(edit.search, edit.replace, 1)
        return ApplyResult(True, rel, "exact"), old, new

    # Fallback: whitespace-normalized match.
    norm_search = _normalize(edit.search)
    lines = old.splitlines()
    for i in range(len(lines)):
        for j in range(i + 1, len(lines) + 1):
            if _normalize("\n".join(lines[i:j])) == norm_search:
                new = "\n".join(lines[:i] + edit.replace.splitlines() + lines[j:])
                if old.endswith("\n"):
                    new += "\n"
                return ApplyResult(True, rel, "normalized"), old, new
    return ApplyResult(False, rel, "SEARCH block did not match"), None, None
