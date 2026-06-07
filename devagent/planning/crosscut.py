"""Cross-cutting change coordination (Tier-1).

Decompose-execute shines on additive/local work. It's weakest on a change that is ONE intent
spanning many files — rename a symbol used in 40 files, change a function's signature everywhere.
Split into independent file-subtasks, each intermediate state breaks compilation, and the pieces
can disagree on the new name. This is the canonical "wide change that breaks a big codebase."

Full coordination (an atomic multi-file changeset) is a larger effort; this is the bounded first
step that already helps a lot:
  - detect cross-cutting intent (rename / signature change / replace-across) and extract the
    old→new mapping when stated;
  - emit a COORDINATION DIRECTIVE injected into EVERY subtask's prompt, so all pieces apply the
    exact same rename/signature instead of guessing;
  - the green-tree invariant (#6) then backstops it: any piece that leaves a dangling reference to
    the old name fails the integration check and is rolled back, never silently shipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# "rename X to Y", "rename X -> Y", "replace X with Y", "change F's signature", "deprecate X"
_RENAME = [
    re.compile(r"\brename\s+(?:the\s+)?[`'\"]?([A-Za-z_]\w*)[`'\"]?\s+(?:to|into|->|→)\s+[`'\"]?([A-Za-z_]\w*)", re.I),
    re.compile(r"\breplace\s+[`'\"]?([A-Za-z_]\w*)[`'\"]?\s+with\s+[`'\"]?([A-Za-z_]\w*)", re.I),
]
_SIGNATURE = re.compile(r"\b(?:change|update|modify|alter)\b.*\bsignature\b", re.I)
_WIDE = re.compile(r"\b(rename|across (?:the|all)|everywhere|throughout|all (?:call\s?sites|usages|references)|"
                   r"signature|deprecat\w+|migrate all)\b", re.I)


@dataclass
class Crosscut:
    kind: str                                  # "rename" | "signature" | "wide"
    renames: list[tuple[str, str]] = field(default_factory=list)  # (old, new)

    def directive(self) -> str:
        lines = ["COORDINATED CROSS-CUTTING CHANGE — every subtask MUST apply this consistently:"]
        for old, new in self.renames:
            lines.append(f"- rename `{old}` → `{new}` EXACTLY; update every reference, import, and "
                         f"call site you touch. Never leave a reference to `{old}`.")
        if self.kind == "signature":
            lines.append("- the function signature changes: update the definition AND every call "
                         "site you touch to match the new signature exactly.")
        lines.append("- if a file you edit references the old form elsewhere, update those too so "
                     "the file stays internally consistent.")
        return "\n".join(lines)


def detect(task: str) -> Crosscut | None:
    """Return a Crosscut if the task reads like a wide, coordinated change, else None."""
    if not task:
        return None
    renames: list[tuple[str, str]] = []
    for pat in _RENAME:
        for old, new in pat.findall(task):
            if old != new:
                renames.append((old, new))
    if renames:
        return Crosscut("rename", renames)
    if _SIGNATURE.search(task):
        return Crosscut("signature")
    if _WIDE.search(task):
        return Crosscut("wide")
    return None
