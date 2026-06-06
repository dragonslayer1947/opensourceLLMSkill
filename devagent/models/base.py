"""Abstract model client + completion result. Two concrete protocols implement this:
openai_compat (llama.cpp, GPT, OpenRouter, ...) and anthropic (Claude)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CompletionResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    model_name: str = ""
    cost_usd: float = 0.0  # API-equivalent cost reported by the provider (CLI subscription => marginal $0)


def estimate_tokens(text: str) -> int:
    """Cheap heuristic when a provider doesn't report usage (~4 chars/token)."""
    return max(1, len(text) // 4)


class ModelClient(ABC):
    def __init__(self, name: str, model_id: str, tier: str, defaults: dict):
        self.name = name
        self.model_id = model_id
        self.tier = tier
        self.defaults = defaults or {}

    @property
    def is_local(self) -> bool:
        return self.tier == "local"

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        cacheable_system: bool = False,
    ) -> CompletionResult:
        ...
