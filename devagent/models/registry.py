"""Build model clients from config and resolve a role to its ordered client chain.

Clients are built lazily and cached: a model with a missing API key only errors if a role
actually tries to use it (so a local-only setup runs without any cloud keys)."""
from __future__ import annotations

from ..config import Config, ModelSpec
from .base import ModelClient


class Registry:
    def __init__(self, config: Config):
        self.config = config
        self._cache: dict[str, ModelClient] = {}
        self._errors: dict[str, str] = {}

    def _build(self, spec: ModelSpec) -> ModelClient:
        defaults = self.config.model_defaults
        if spec.protocol == "openai-compat":
            from .openai_compat import OpenAICompatClient

            return OpenAICompatClient(
                spec.name, spec.model_id, spec.tier, defaults,
                base_url=spec.base_url, api_key=spec.api_key, timeout_s=spec.timeout_s,
            )
        if spec.protocol == "anthropic":
            from .anthropic_client import AnthropicClient

            return AnthropicClient(
                spec.name, spec.model_id, spec.tier, defaults, api_key=spec.api_key,
            )
        if spec.protocol == "cli":
            from .cli_client import CLIClient

            return CLIClient(
                spec.name, spec.model_id, spec.tier, defaults,
                command=spec.command, mode=spec.mode, timeout_s=spec.timeout_s,
            )
        raise RuntimeError(f"unknown protocol '{spec.protocol}' for model '{spec.name}'")

    def get(self, name: str) -> ModelClient | None:
        """Return a client, or None if it can't be built (records why in self._errors)."""
        if name in self._cache:
            return self._cache[name]
        spec = self.config.models.get(name)
        if spec is None:
            self._errors[name] = "not declared in config"
            return None
        try:
            client = self._build(spec)
            self._cache[name] = client
            return client
        except Exception as e:  # noqa: BLE001 — surface as availability, not crash
            self._errors[name] = str(e)
            return None

    def resolve_chain(self, role: str) -> list[ModelClient]:
        chain: list[ModelClient] = []
        for name in self.config.role_chain(role):
            client = self.get(name)
            if client is not None:
                chain.append(client)
        return chain

    def error_for(self, name: str) -> str | None:
        return self._errors.get(name)
