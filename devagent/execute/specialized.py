"""Specialized agents by domain (V3). Detects a subtask's domain from its files/description and
adds domain-specific guidance to the executor prompt — so an infra, migration, frontend, or API
change follows the right conventions and hazards, without separate model infrastructure."""
from __future__ import annotations

DOMAIN_GUIDANCE = {
    "migration": "This is a DATABASE MIGRATION. Make it reversible (up and down); never edit an "
                 "already-applied migration; guard destructive operations; consider data backfill.",
    "infra": "This is INFRASTRUCTURE-AS-CODE. Keep changes declarative and idempotent; never "
             "hardcode secrets; mind blast radius on shared resources.",
    "frontend": "This is FRONTEND code. Follow existing component patterns; preserve prop types "
                "and accessibility; reuse the design system rather than inline styles.",
    "api": "This is an API change. Preserve backward compatibility; validate inputs; keep error "
           "shapes consistent with existing endpoints.",
    "backend": "",
}

_FRONTEND_SUFFIXES = (".tsx", ".jsx", ".css", ".scss", ".less", ".html", ".vue", ".svelte")
_INFRA_SUFFIXES = (".tf", ".tfvars")


def detect_domain(description: str, target_files: list[str]) -> str:
    files = [f.lower().replace("\\", "/") for f in (target_files or [])]
    text = (description or "").lower()

    def in_text(*subs: str) -> bool:
        return any(s in text for s in subs)

    if any("migration" in f or "alembic" in f for f in files) or \
            in_text("migration", "schema change", "alter table", "column to", "add column",
                    "add a column", "drop column", "drop a column", "new column"):
        return "migration"
    if any(f.endswith(_INFRA_SUFFIXES) for f in files) or \
            any("dockerfile" in f or "/helm/" in f or "/k8s/" in f or ".github/workflows" in f
                for f in files) or \
            in_text("terraform", "kubernetes", "helm", "dockerfile", "deployment manifest",
                    "infrastructure", "ci/cd", "ci pipeline"):
        return "infra"
    if any(f.endswith(_FRONTEND_SUFFIXES) for f in files) or \
            in_text("component", "react", "frontend", "ui ", "stylesheet", "css"):
        return "frontend"
    if in_text("endpoint", "route", "openapi", "rest api", "graphql"):
        return "api"
    return "backend"


def guidance_for(description: str, target_files: list[str]) -> tuple[str, str]:
    """Return (domain, guidance_text)."""
    domain = detect_domain(description, target_files)
    return domain, DOMAIN_GUIDANCE.get(domain, "")
