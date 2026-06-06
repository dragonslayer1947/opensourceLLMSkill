"""Anthropic (Claude) client. Sends the system prompt as a cacheable block so a stable
prefix (ADRs, architecture summary) hits the prompt cache across calls."""
from __future__ import annotations

from .base import CompletionResult, ModelClient


class AnthropicClient(ModelClient):
    def __init__(self, name, model_id, tier, defaults, api_key):
        super().__init__(name, model_id, tier, defaults)
        from anthropic import Anthropic  # lazy import

        if not api_key:
            raise RuntimeError(
                f"model '{name}' needs an API key — set the env var named in its api_key_env."
            )
        self._client = Anthropic(api_key=api_key, max_retries=0)

    def complete(self, system, user, *, max_tokens=None, temperature=None, cacheable_system=False):
        if cacheable_system:
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            system_param = system

        resp = self._client.messages.create(
            model=self.model_id,
            system=system_param,
            max_tokens=max_tokens or self.defaults.get("max_tokens", 8192),
            temperature=temperature if temperature is not None else self.defaults.get("temperature", 0.2),
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        u = resp.usage
        return CompletionResult(
            text=text,
            tokens_in=getattr(u, "input_tokens", 0) or 0,
            tokens_out=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            model_name=self.name,
        )
