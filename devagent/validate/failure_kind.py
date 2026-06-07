"""Classify a gate/integration failure as a CONTEXT miss vs. a generation problem (gap #7).

A gate failure has two very different root causes:
  - the model lacked the right slice of the repo — it referenced a name it was never shown
    (undefined name, unresolved import, missing attribute, cross-file interface drift). The fix
    is to RE-RETRIEVE wider and retry locally ($0), not to escalate to the frontier.
  - the model produced wrong logic/syntax with the right context. That's a real escalation.

This module recognizes the first kind by the shape of the tool output (ruff F821, mypy
"is not defined", our own interface-drift message, import errors) and extracts the unresolved
identifier names so retrieval can pull in the files that define them."""
from __future__ import annotations

import re

# Substrings that mark a context/interface miss (lower-cased match).
_CONTEXT_SIGNS = (
    "undefined name", "f821",
    "is not defined", "cannot import name", "no name ",
    "has no attribute", "which does not define it",
    "unresolved import", "could not be resolved",
    "importerror", "modulenotfounderror", "attributeerror", "nameerror",
)

_NAME_PATTERNS = [
    re.compile(r"undefined name [`'\"]([A-Za-z_]\w*)"),
    re.compile(r"name [`'\"]([A-Za-z_]\w*)[`'\"] is not defined"),
    re.compile(r"cannot import name [`'\"]([A-Za-z_]\w*)"),
    re.compile(r"imports '([A-Za-z_]\w*)' from"),       # our interface-drift message
    re.compile(r"has no attribute [`'\"]([A-Za-z_]\w*)"),
    re.compile(r"\"([A-Za-z_]\w*)\" is not defined"),
]


def is_context_failure(text: str) -> bool:
    t = (text or "").lower()
    return any(sign in t for sign in _CONTEXT_SIGNS)


def missing_names(text: str) -> set[str]:
    """The unresolved identifiers named in the failure — used to locate their defining files."""
    out: set[str] = set()
    for pat in _NAME_PATTERNS:
        out.update(pat.findall(text or ""))
    return {n for n in out if n}
