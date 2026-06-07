"""Routing classifier — decides DIRECT vs PLAN_EXECUTE before execution.

A deterministic weighted decision matrix over cheap signals (free, no model call):

    signal                     weight   meaning
    no_existing_pattern         3.0     no learned pattern matches the task
    cross_service               2.5     the change spans services / contracts
    security_surface            2.0     auth / payments / PII involved
    ambiguity                   1.5     task is vague / under-specified
    large_context               1.0     retrieved context near the envelope ceiling

Score ≥ threshold (default 6) → PLAN_EXECUTE (a frontier model decomposes first). A task that
already falls outside the parity envelope always routes to PLAN_EXECUTE."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

SECURITY = {"auth", "authentication", "authorization", "login", "password", "token", "oauth",
            "jwt", "session", "payment", "billing", "card", "checkout", "pii", "ssn",
            "encrypt", "encryption", "credential", "secret", "permission"}
CROSS_SERVICE = {"service", "services", "microservice", "microservices", "contract",
                 "endpoint", "grpc", "kafka", "queue", "webhook", "integration"}
VAGUE = {"improve", "optimize", "better", "faster", "clean", "cleanup", "refactor",
         "enhance", "fix", "handle", "update", "tidy", "modernize"}
STOP = {"the", "a", "an", "to", "of", "in", "and", "or", "for", "with", "on", "this",
        "that", "it", "is", "be", "add", "make", "all", "into", "from"}

THRESHOLD = 6.0


@dataclass
class Decision:
    route: str                      # "direct" | "plan_execute"
    score: float
    confidence: float
    signals: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _terms(task: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(task)]


def _content_terms(task: str) -> list[str]:
    return [t for t in _terms(task) if t not in STOP and t not in VAGUE and len(t) > 2]


def classify(task: str, *, in_envelope: bool, est_tokens: int, max_context_tokens: int,
             has_pattern: bool, threshold: float = THRESHOLD) -> Decision:
    terms = set(_terms(task))
    signals: dict[str, bool] = {}
    reasons: list[str] = []
    score = 0.0

    if not has_pattern:
        signals["no_existing_pattern"] = True
        score += 3.0
        reasons.append("no matching pattern")
    if terms & CROSS_SERVICE:
        signals["cross_service"] = True
        score += 2.5
        reasons.append("cross-service surface")
    if terms & SECURITY:
        signals["security_surface"] = True
        score += 2.0
        reasons.append("security surface")
    if len(_content_terms(task)) < 2:
        signals["ambiguity"] = True
        score += 1.5
        reasons.append("ambiguous / under-specified")
    if est_tokens > 0.6 * max_context_tokens:
        signals["large_context"] = True
        score += 1.0
        reasons.append("large context")

    route = "plan_execute" if (score >= threshold or not in_envelope) else "direct"
    if not in_envelope and "out-of-envelope" not in reasons:
        reasons.append("out of parity envelope")
    # confidence in a DIRECT routing falls as the score approaches the threshold
    confidence = max(0.0, min(1.0, 1.0 - score / (threshold * 2)))
    return Decision(route=route, score=round(score, 2), confidence=round(confidence, 2),
                    signals=signals, reasons=reasons)
