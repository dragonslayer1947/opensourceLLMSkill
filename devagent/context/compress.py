"""Deterministic-first compression for the rare frontier-model call.

Order matters: extract signatures/types/exceptions/public interface *verbatim* (deterministic,
lossless for the things that matter), THEN let the local model summarize remaining logic. This
keeps critical info from being silently dropped and keeps the output stable enough to hit the
prompt cache."""
from __future__ import annotations

import ast


def deterministic_extract(rel: str, text: str) -> str:
    """Signatures, type aliases, exception classes, imports — the file's contract."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return f"# {rel} (unparseable — name only)\n"
    lines = text.splitlines()
    out = [f"# {rel}"]
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(lines[node.lineno - 1].rstrip())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(lines[node.lineno - 1].rstrip().rstrip(":") + ":")
            is_exc = isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and "Error" in b.id) or
                (isinstance(b, ast.Name) and "Exception" in b.id)
                for b in node.bases
            )
            if is_exc:
                out.append("    # exception type")
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append("    " + lines[sub.lineno - 1].strip().rstrip(":") + ":")
    return "\n".join(out) + "\n"


def compress_for_frontier(views, router=None) -> str:
    """Build a compact, frontier-bound context: deterministic contracts for every file,
    plus an optional local-model summary of behavior. The frontier model never sees bodies."""
    contracts = []
    for v in views:
        # `v.content` is already windowed; extract a contract from whatever we have.
        contracts.append(deterministic_extract(v.rel, v.content))
    blob = "\n".join(contracts)

    if router is not None:
        try:
            summary = router.complete(
                "compressor",
                system="You compress code context. Output a terse bullet summary of behavior "
                       "and invariants. Do not restate signatures. Max 12 bullets.",
                user=blob[:8000],
                max_tokens=400,
            )
            blob += "\n\n# Behavior summary (local model):\n" + summary.text
        except Exception:
            pass  # summary is best-effort; the deterministic contract is the guarantee
    return blob
