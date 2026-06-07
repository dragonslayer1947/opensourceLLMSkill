"""Pattern registry — learned conventions, with confidence decay and deprecation (gap #11).

Patterns live in `.devagent/patterns.yaml` (per repo). Each carries a confidence that DECAYS
with age since last use (half-life in days), and a status — so a pattern that is no longer
exercised, or was superseded, naturally stops influencing generation. Relevant active patterns
are injected into the executor prompt so the local model follows house conventions.

Capture is explicit (`devagent pattern add`) — a frontier-generated fix is NOT auto-promoted to
an authoritative pattern without human approval."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..validate.safety_rules import Violation, _glob_to_re

PATTERNS_FILE = ".devagent/patterns.yaml"
HALF_LIFE_DAYS = 90.0
MIN_EFFECTIVE = 0.3

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Pattern:
    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    snippet: str = ""
    confidence: float = 0.6
    created_at: str = ""
    last_used: str = ""
    uses: int = 0
    status: str = "active"  # active | deprecated
    # optional write-time enforcement: files matching enforce_glob must contain enforce_regex
    enforce_glob: str = ""
    enforce_regex: str = ""
    enforce_severity: str = "warn"  # warn | block


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "pattern"


def patterns_file(root: Path) -> Path:
    return root / PATTERNS_FILE


def load_patterns(root: Path) -> list[Pattern]:
    p = patterns_file(root)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for d in data.get("patterns", []) or []:
        out.append(Pattern(
            id=str(d.get("id") or _slug(d.get("name", "pattern"))),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            tags=[str(t) for t in d.get("tags", []) or []],
            snippet=str(d.get("snippet", "")),
            confidence=float(d.get("confidence", 0.6)),
            created_at=str(d.get("created_at", "")),
            last_used=str(d.get("last_used", "")),
            uses=int(d.get("uses", 0)),
            status=str(d.get("status", "active")),
            enforce_glob=str(d.get("enforce_glob", "")),
            enforce_regex=str(d.get("enforce_regex", "")),
            enforce_severity=str(d.get("enforce_severity", "warn")),
        ))
    return out


def save_patterns(root: Path, patterns: list[Pattern]) -> None:
    p = patterns_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"patterns": [vars(pat) for pat in patterns]}
    p.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def add_pattern(root: Path, name: str, description: str = "", tags: list[str] | None = None,
                snippet: str = "", confidence: float = 0.6, enforce_glob: str = "",
                enforce_regex: str = "", enforce_severity: str = "warn") -> Pattern:
    patterns = load_patterns(root)
    pid = _slug(name)
    existing = {p.id for p in patterns}
    if pid in existing:
        i = 2
        while f"{pid}-{i}" in existing:
            i += 1
        pid = f"{pid}-{i}"
    pat = Pattern(id=pid, name=name, description=description, tags=tags or [], snippet=snippet,
                  confidence=confidence, created_at=_now(), last_used=_now(), uses=0,
                  enforce_glob=enforce_glob, enforce_regex=enforce_regex,
                  enforce_severity=enforce_severity)
    patterns.append(pat)
    save_patterns(root, patterns)
    return pat


def enforce_violations(patterns: list[Pattern], changes, now: datetime | None = None) -> list[Violation]:
    """Write-time enforcement: a changed file matching a pattern's enforce_glob must contain its
    enforce_regex, else a Violation (warn/block) is raised. Only active patterns enforce."""
    out: list[Violation] = []
    for p in active_patterns(patterns, now):
        if not p.enforce_glob or not p.enforce_regex:
            continue
        rx = _glob_to_re(p.enforce_glob)
        try:
            creg = re.compile(p.enforce_regex)
        except re.error:
            continue
        for ch in changes:
            path = ch.path.replace("\\", "/")
            if rx.match(path) and not creg.search(getattr(ch, "new", "") or ""):
                sev = p.enforce_severity if p.enforce_severity in ("block", "warn") else "warn"
                out.append(Violation(f"pattern:{p.id}", sev, ch.path,
                                     f"pattern '{p.name}' requires /{p.enforce_regex}/"))
    return out


def deprecate(root: Path, pattern_id: str) -> bool:
    patterns = load_patterns(root)
    found = False
    for p in patterns:
        if p.id == pattern_id:
            p.status = "deprecated"
            found = True
    if found:
        save_patterns(root, patterns)
    return found


def _age_days(iso: str, now: datetime) -> float:
    if not iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def effective_confidence(p: Pattern, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    age = _age_days(p.last_used or p.created_at, now)
    return p.confidence * (0.5 ** (age / HALF_LIFE_DAYS))


def active_patterns(patterns: list[Pattern], now: datetime | None = None,
                    min_effective: float = MIN_EFFECTIVE) -> list[Pattern]:
    return [p for p in patterns
            if p.status == "active" and effective_confidence(p, now) >= min_effective]


def relevant(patterns: list[Pattern], task: str, now: datetime | None = None,
             limit: int = 3) -> list[Pattern]:
    terms = {w.lower() for w in _WORD.findall(task) if len(w) > 2}
    scored = []
    for p in active_patterns(patterns, now):
        hay = {t.lower() for t in p.tags} | {w.lower() for w in _WORD.findall(p.name)}
        overlap = len(terms & hay)
        if overlap:
            scored.append((overlap * effective_confidence(p, now), p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


def patterns_context(patterns: list[Pattern], task: str, now: datetime | None = None) -> str:
    rel = relevant(patterns, task, now)
    lines = []
    for p in rel:
        lines.append(f"- {p.name}: {p.description}")
        if p.snippet:
            lines.append(f"    example: {p.snippet[:200]}")
    return "\n".join(lines)
