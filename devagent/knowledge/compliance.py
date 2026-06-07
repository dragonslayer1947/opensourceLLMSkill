"""Compliance constraint profiles (V4). Named profiles (PCI-DSS, SOC2, HIPAA) expand into
safety-rule sets that are merged with the repo's `.devagent/rules.yaml` and ADR-derived
constraints. Enable per repo via `[compliance] profiles = ["pci-dss"]` in the config."""
from __future__ import annotations

from ..validate.safety_rules import Rule

_SECRET = r"(?i)(secret|api[_-]?key|password|token)\s*[:=]\s*\S{6,}"

PROFILES: dict[str, list[Rule]] = {
    "pci-dss": [
        Rule(id="pci-no-secret", action="block", content_regex=_SECRET,
             message="PCI-DSS: no hardcoded secrets — use a vault/env."),
        Rule(id="pci-payment-review", action="require_flag", path_glob="**/payment*/**",
             flag="security-review", message="PCI-DSS: payment code needs --flag security-review."),
        Rule(id="pci-billing-review", action="require_flag", path_glob="**/billing/**",
             flag="security-review", message="PCI-DSS: billing code needs --flag security-review."),
        Rule(id="pci-no-log-card", action="warn",
             content_regex=r"(?i)log.*\b(pan|card_?number|cvv|cvc)\b",
             message="PCI-DSS: never log card data (PAN/CVV)."),
    ],
    "soc2": [
        Rule(id="soc2-auth-review", action="require_flag", path_glob="**/auth/**",
             flag="access-review", message="SOC2: auth change needs --flag access-review."),
        Rule(id="soc2-no-secret", action="block", content_regex=_SECRET,
             message="SOC2: no hardcoded credentials."),
    ],
    "hipaa": [
        Rule(id="hipaa-phi-review", action="require_flag", path_glob="**/patient*/**",
             flag="phi-review", message="HIPAA: PHI code needs --flag phi-review."),
        Rule(id="hipaa-no-log-phi", action="warn",
             content_regex=r"(?i)log.*\b(ssn|mrn|dob|diagnosis)\b",
             message="HIPAA: do not log PHI (SSN/MRN/DOB/diagnosis)."),
    ],
}


def available() -> list[str]:
    return sorted(PROFILES)


def expand(profiles: list[str]) -> list[Rule]:
    out: list[Rule] = []
    for name in profiles or []:
        out.extend(PROFILES.get(name.lower(), []))
    return out
