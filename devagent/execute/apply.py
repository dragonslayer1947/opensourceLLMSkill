"""Safe apply: snapshot -> diff preview -> confirm -> atomic write. Plus undo.

Snapshots are file-scoped copies (works in or out of git, deterministic, cross-platform). The
manifest records each touched file's prior state so undo restores edits and deletes creations."""
from __future__ import annotations

import difflib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

from .edits import Edit, apply_edit


@dataclass
class FileChange:
    path: str
    old: str | None
    new: str
    reason: str

    @property
    def is_create(self) -> bool:
        return self.old is None


@dataclass
class PreparedEdits:
    changes: list[FileChange] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    @property
    def ok(self) -> bool:
        return bool(self.changes) and not self.failures


def prepare(root: Path, edits: list[Edit]) -> PreparedEdits:
    prepared = PreparedEdits()
    for edit in edits:
        result, old, new = apply_edit(root, edit)
        if result.ok and new is not None:
            prepared.changes.append(FileChange(result.path, old, new, result.reason))
        else:
            prepared.failures.append((result.path, result.reason))
    return prepared


def unified_diff(changes: list[FileChange]) -> str:
    """Concatenated unified diff for a set of changes (for the reviewer / logging)."""
    parts = []
    for ch in changes:
        diff = "".join(difflib.unified_diff(
            (ch.old or "").splitlines(keepends=True), ch.new.splitlines(keepends=True),
            fromfile=f"a/{ch.path}", tofile=f"b/{ch.path}", n=3,
        ))
        if diff.strip():
            parts.append(diff)
    return "\n".join(parts)


def render_diff(changes: list[FileChange], console: Console) -> None:
    for ch in changes:
        old_lines = (ch.old or "").splitlines(keepends=True)
        new_lines = ch.new.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{ch.path}", tofile=f"b/{ch.path}", n=2,
        ))
        label = "[green]CREATE[/green]" if ch.is_create else "[yellow]EDIT[/yellow]"
        console.print(f"\n{label} {ch.path}  [dim]({ch.reason})[/dim]")
        if diff.strip():
            console.print(Syntax(diff, "diff", theme="ansi_dark", word_wrap=True))
        else:
            console.print("[dim](no textual diff)[/dim]")


def snapshot(root: Path, snap_dir: Path, changes: list[FileChange]) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    files_dir = snap_dir / "files"
    for ch in changes:
        entry = {"path": ch.path, "created": ch.is_create}
        if not ch.is_create:
            dest = files_dir / ch.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / ch.path, dest)
        manifest.append(entry)
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def write_changes(root: Path, changes: list[FileChange]) -> int:
    total = 0
    for ch in changes:
        target = root / ch.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ch.new, encoding="utf-8")
        total += 1
    return total


def undo_from_snapshot(root: Path, snap_dir: Path) -> list[str]:
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored = []
    files_dir = snap_dir / "files"
    for entry in manifest:
        path = entry["path"]
        target = root / path
        if entry.get("created"):
            if target.exists():
                target.unlink()
                restored.append(f"deleted {path}")
        else:
            src = files_dir / path
            if src.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                restored.append(f"restored {path}")
    return restored
