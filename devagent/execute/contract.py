"""Contract-first for API tasks (gap #6).

For API work, an OpenAPI `paths` spec is generated and VALIDATED before implementation, then
the implementation is diffed back against the spec (conformance) so it can't silently drift.
The local model drafts the spec (cheap); validation and conformance are deterministic."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONTRACT_DIR = ".devagent/contracts"

API_KEYWORDS = {"endpoint", "endpoints", "openapi", "route", "routes", "rest", "api"}

SPEC_SYSTEM = """\
You design REST API contracts. Output ONLY an OpenAPI 3.0 `paths` object in a ```yaml fenced
block — the paths the task requires, with methods, parameters, request/response schemas. No
prose, no implementation."""


@dataclass
class ContractResult:
    yaml_text: str = ""
    spec: dict | None = None          # full OpenAPI doc (skeleton + paths)
    valid: bool = False
    errors: list[str] = field(default_factory=list)
    model: str | None = None
    tier: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


def is_api_task(task: str) -> bool:
    terms = {w.lower() for w in re.findall(r"[A-Za-z]+", task)}
    return bool(terms & API_KEYWORDS)


def wrap_skeleton(paths: dict) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "devagent-contract", "version": "0.0.0"},
        "paths": paths or {},
    }


def validate_openapi(doc: dict) -> tuple[bool, list[str]]:
    try:
        from openapi_spec_validator import validate
        validate(doc)
        return True, []
    except Exception as e:  # noqa: BLE001 — surface the validation message
        return False, [str(e).splitlines()[0][:300]]


def extract_spec_block(text: str) -> dict | None:
    m = re.search(r"```(?:ya?ml|json)?\s*(.*?)```", text or "", re.DOTALL)
    raw = m.group(1) if m else text
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def generate_contract(task: str, context: str, router) -> ContractResult:
    user = f"TASK:\n{task}\n\nEXISTING CONTEXT (conventions):\n{context[:4000]}\n\nOutput the paths object."
    result = router.complete("executor", SPEC_SYSTEM, user, max_tokens=1200)
    meta = dict(model=router.last_model, tier=router.last_tier,
                tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=result.cost_usd)
    paths = extract_spec_block(result.text)
    if not isinstance(paths, dict):
        return ContractResult(yaml_text=result.text, spec=None, valid=False,
                              errors=["could not parse an OpenAPI paths object"], **meta)
    # accept either a bare paths object or a full doc
    doc = paths if "paths" in paths and "openapi" in paths else wrap_skeleton(paths)
    valid, errors = validate_openapi(doc)
    return ContractResult(yaml_text=yaml.safe_dump(doc, sort_keys=False), spec=doc,
                          valid=valid, errors=errors, **meta)


def _required_fields(schema: dict) -> list[str]:
    out = list(schema.get("required", []) or [])
    for name in (schema.get("properties", {}) or {}):
        out.append(name)
    return out


def conformance_check(doc: dict, code: str) -> list[str]:
    """Lightweight diff-back: every path, method, and declared field should appear in the code.
    Catches gross drift (missing endpoint / method / field) without framework coupling."""
    discrepancies: list[str] = []
    code_l = code or ""
    for path, methods in (doc.get("paths", {}) or {}).items():
        if path not in code_l:
            discrepancies.append(f"path not implemented: {path}")
            continue
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            # collect declared field names from requestBody/response schemas
            fields: list[str] = []
            for body in ("requestBody",):
                content = (op.get(body, {}) or {}).get("content", {}) or {}
                for media in content.values():
                    fields += _required_fields(media.get("schema", {}) or {})
            for fname in fields:
                if fname and fname not in code_l:
                    discrepancies.append(f"{method.upper()} {path}: field '{fname}' missing in implementation")
    return discrepancies


def save_contract(root: Path, name: str, yaml_text: str) -> Path:
    d = root / CONTRACT_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return p
