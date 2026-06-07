"""Test generator (V3) — drafts unit tests for a source file using the local model. The test
path derivation and code extraction are deterministic; the model only writes the test body."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

TESTGEN_SYSTEM = """\
You write focused, runnable unit tests (pytest) for the given module. Cover the main behaviors
and edge cases. Import from the module under test. Output ONLY the test file contents in a single
```python fenced block — no prose."""


def test_path_for(src_rel: str) -> str:
    p = PurePosixPath(src_rel)
    return f"tests/test_{p.stem}.py"


def extract_code_block(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text or "", re.DOTALL)
    return (m.group(1) if m else (text or "")).strip() + "\n"


def generate_tests(src_rel: str, code: str, router, role: str = "executor") -> tuple[str, dict]:
    user = (f"MODULE: {src_rel}\n\n```python\n{code[:8000]}\n```\n\n"
            f"Write pytest tests for this module.")
    result = router.complete(role, TESTGEN_SYSTEM, user, max_tokens=2000)
    meta = {"model": result.model_name, "tier": result.tier,
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd}
    return extract_code_block(result.text), meta
