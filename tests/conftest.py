"""Shared fixtures. Tests cover the offline machinery — no network, no model calls."""
from __future__ import annotations

import pytest

from devagent.config import Config, ModelSpec, Pricing


@pytest.fixture
def tmp_repo(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "calc.py").write_text(
        "from typing import List\n\n\n"
        "class Calc:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n\n"
        "    def total(self, xs: List[int]) -> int:\n"
        "        return sum(xs)\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def make_config(tmp_path):
    def _make(**over):
        cfg = Config(
            models={
                "local": ModelSpec("local", "openai-compat", "m", tier="local", base_url="http://x/v1"),
                "cloud": ModelSpec("cloud", "anthropic", "c", tier="frontier", api_key_env="X"),
                "cli": ModelSpec("cli", "cli", "sonnet", tier="cli", command="claude"),
            },
            roles={
                "executor": ["local"], "planner": ["cloud", "local"],
                "reviewer": ["cloud", "local"], "compressor": ["local"], "classifier": ["local"],
            },
            fallback={"retries": 1, "backoff_s": 0, "circuit_break_after": 2},
            model_defaults={"temperature": 0.2, "max_tokens": 256},
            envelope={"max_context_tokens": 12000, "max_file_lines": 400, "max_subtask_files": 3},
            gate={"run_types": False, "run_lint": False, "run_security": False, "run_tests": False},
            reporting={"counterfactual_model": "cli", "parity_target": 0.9},
            pricing={"local": Pricing(0, 0), "cloud": Pricing(15, 75), "sonnet": Pricing(3, 15)},
            db_path=tmp_path / "tasks.db",
        )
        for k, v in over.items():
            setattr(cfg, k, v)
        return cfg
    return _make
