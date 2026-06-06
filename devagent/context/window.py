"""Large-file windowing — attacks the file-scale half of the parity problem.

Instead of feeding a 2000-line file to the local model, feed: the full text of the focus
region (the target symbol + a margin) plus a compact *skeleton* (signatures only) of
everything else. The model edits a small window with a map of the rest."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileView:
    rel: str
    content: str          # what to show the model
    windowed: bool        # True if this is a skeleton+focus view, not the whole file
    focus_lines: tuple[int, int] | None = None


def _skeleton(text: str) -> str:
    """Top-level signatures only — the file's shape without its bodies."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    lines = text.splitlines()
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            sig_line = lines[node.lineno - 1].rstrip() if node.lineno - 1 < len(lines) else ""
            out.append(sig_line.rstrip(":") + ":  ...")
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        s = lines[sub.lineno - 1].rstrip() if sub.lineno - 1 < len(lines) else ""
                        out.append("    " + s.strip().rstrip(":") + ":  ...")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(lines[node.lineno - 1].rstrip())
    return "\n".join(out)


def _find_focus(text: str, focus_symbol: str | None) -> tuple[int, int] | None:
    if not focus_symbol:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    target = focus_symbol.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == target:
                return (node.lineno, getattr(node, "end_lineno", node.lineno))
    return None


def view_file(
    path: Path,
    rel: str,
    *,
    max_file_lines: int,
    focus_symbol: str | None = None,
) -> FileView:
    text = path.read_text(encoding="utf-8", errors="replace")
    n_lines = text.count("\n") + 1

    if n_lines <= max_file_lines or path.suffix != ".py":
        return FileView(rel=rel, content=text, windowed=(n_lines > max_file_lines and path.suffix != ".py"))

    # Large Python file: skeleton + focused region.
    focus = _find_focus(text, focus_symbol)
    lines = text.splitlines()
    margin = 15
    if focus is None:
        # No specific symbol — show the head of the file as the focus region.
        start, end = 1, min(max_file_lines, n_lines)
    else:
        start = max(1, focus[0] - margin)
        end = min(n_lines, focus[1] + margin)

    focus_block = "\n".join(lines[start - 1:end])
    skeleton = _skeleton(text)
    content = (
        f"# ── FILE SKELETON (signatures only) for {rel} ──\n"
        f"{skeleton}\n\n"
        f"# ── FOCUS REGION (lines {start}-{end} of {n_lines}) — edit here ──\n"
        f"{focus_block}\n"
    )
    return FileView(rel=rel, content=content, windowed=True, focus_lines=(start, end))
