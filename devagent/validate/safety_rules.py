"""Declarative safety-rules engine — evaluated on prepared changes BEFORE any write.

Rules live in `.devagent/rules.yaml`:

    rules:
      - id: auth-requires-flag
        when: { path_glob: "**/auth/**" }
        action: require_flag
        flag: security-review
        message: "Auth changes need --flag security-review"
      - id: block-secrets
        when: { content_regex: '(?i)(secret|api_key|password)\s*[:=]\s*\S+' }
        action: block

action ∈ {block, warn, require_flag}. A blocking violation prevents the change from being
written; require_flag blocks unless the named flag was passed (`run --flag <name>`)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RULES_FILE = ".devagent/rules.yaml"

SAMPLE_RULES = """\
# devagent safety rules — evaluated before any write.
# action: block | warn | require_flag (with `flag:`). Match on path_glob and/or content_regex.
rules:
  - id: auth-requires-review
    when: { path_glob: "**/auth/**" }
    action: require_flag
    flag: security-review
    message: "Auth changes need: run --flag security-review"

  - id: migrations-require-dba
    when: { path_glob: "**/migrations/**" }
    action: require_flag
    flag: dba-approved
    message: "DB migrations need: run --flag dba-approved"

  - id: block-hardcoded-secret
    when: { content_regex: '(?i)(secret|api[_-]?key|password|token)\s*[:=]\s*\S{6,}' }
    action: block
    message: "Possible hardcoded secret — use config/env instead."
"""


@dataclass
class Rule:
    id: str
    action: str                       # block | warn | require_flag
    path_glob: str | None = None
    content_regex: str | None = None
    flag: str | None = None
    message: str = ""


@dataclass
class Violation:
    rule_id: str
    severity: str                     # block | warn
    path: str
    message: str


def load_rules(root: Path) -> list[Rule]:
    p = root / RULES_FILE
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rules: list[Rule] = []
    for r in data.get("rules", []):
        when = r.get("when", {}) or {}
        rules.append(Rule(
            id=str(r.get("id", "?")),
            action=str(r.get("action", "warn")),
            path_glob=when.get("path_glob"),
            content_regex=when.get("content_regex"),
            flag=r.get("flag"),
            message=str(r.get("message", "")),
        ))
    return rules


def write_sample(root: Path) -> Path:
    p = root / RULES_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(SAMPLE_RULES, encoding="utf-8")
    return p


def _glob_to_re(glob: str) -> re.Pattern:
    """Convert a glob pattern to a compiled regex anchored at both ends (^ … $).

    Translation rules:
    - ``**``  matches any sequence of characters including path separators (``/``),
      so it can cross directory boundaries.  A trailing ``**/`` makes the leading
      separator optional, allowing ``**/x`` to match both ``a/b/x`` and bare ``x``.
    - ``*``   matches any sequence of characters *within* a single path segment;
      it will not cross a ``/``.
    - ``?``   matches exactly one character — any character, including a ``/`` separator
      (it translates to regex ``.``).
    - All other characters are regex-escaped so they are treated as literals.

    Backslashes in the input are normalised to forward slashes before processing.
    Returns a compiled :class:`re.Pattern`.
    """
    g = glob.replace("\\", "/")
    out = "^"
    j = 0
    while j < len(g):
        if g[j:j + 2] == "**":
            out += ".*"
            j += 2
            if g[j:j + 1] == "/":     # make the separator optional so **/x also matches x
                out += "/?"
                j += 1
        elif g[j] == "*":
            out += "[^/]*"
            j += 1
        elif g[j] == "?":
            out += "."
            j += 1
        else:
            out += re.escape(g[j])
            j += 1
    return re.compile(out + "$")


def _matches(rule: Rule, path: str, content: str) -> bool:
    if rule.path_glob is None and rule.content_regex is None:
        return False  # never apply a rule with no condition (avoid blanket blocks)
    if rule.path_glob is not None and not _glob_to_re(rule.path_glob).match(path.replace("\\", "/")):
        return False
    if rule.content_regex is not None and not re.search(rule.content_regex, content or ""):
        return False
    return True


def evaluate(changes, rules: list[Rule], flags: set[str]) -> list[Violation]:
    """changes: objects with .path and .new (the new file content)."""
    out: list[Violation] = []
    for ch in changes:
        content = getattr(ch, "new", "") or ""
        for rule in rules:
            if not _matches(rule, ch.path, content):
                continue
            if rule.action == "require_flag":
                if rule.flag not in flags:
                    out.append(Violation(rule.id, "block", ch.path,
                                         rule.message or f"requires --flag {rule.flag}"))
            elif rule.action == "block":
                out.append(Violation(rule.id, "block", ch.path, rule.message or "blocked by rule"))
            elif rule.action == "warn":
                out.append(Violation(rule.id, "warn", ch.path, rule.message or "warning"))
    return out
