"""DB migration safety gate (V4). For changes to migration files, require reversibility and
gate destructive operations behind an explicit flag — evaluated before any write, alongside the
safety-rules engine. Returns the same Violation type so the pipeline's block logic applies."""
from __future__ import annotations

import re

from .safety_rules import Violation

APPROVE_FLAG = "migration-approved"

_MIGRATION_PATH = re.compile(r"(^|/)(migrations?|alembic|versions)(/|$)", re.IGNORECASE)
_DESTRUCTIVE = re.compile(
    r"(?i)(drop\s+table|drop\s+column|truncate\s+table|truncate\s+\w+|"
    r"delete\s+from\s+\w+\s*(;|$)|alter\s+table\s+\w+\s+drop)")


def is_migration_path(path: str) -> bool:
    return bool(_MIGRATION_PATH.search(path.replace("\\", "/")))


def check(changes, flags: set[str]) -> list[Violation]:
    """changes: objects with .path and .new (new content)."""
    out: list[Violation] = []
    for ch in changes:
        if not is_migration_path(ch.path):
            continue
        content = getattr(ch, "new", "") or ""

        # destructive operations require an explicit approval flag
        if _DESTRUCTIVE.search(content) and APPROVE_FLAG not in flags:
            out.append(Violation("migration-destructive", "block", ch.path,
                                 f"destructive migration needs --flag {APPROVE_FLAG}"))

        # alembic-style migrations must be reversible
        if "def upgrade" in content and "def downgrade" not in content:
            out.append(Violation("migration-irreversible", "block", ch.path,
                                 "migration defines upgrade() but no downgrade() — must be reversible"))
    return out
