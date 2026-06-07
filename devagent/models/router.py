"""Role-based routing with fallback chains, retry/backoff, and a circuit breaker.

The rest of the system never names a model — it asks for a *role* ("executor", "planner",
...). The router picks the primary, retries with backoff on transient failures, and falls
through the chain if a provider is down or rate-limited."""
from __future__ import annotations

import time

from .base import CompletionResult
from .registry import Registry


class RoutingError(RuntimeError):
    pass


class Router:
    def __init__(self, registry: Registry):
        self.registry = registry
        fb = registry.config.fallback
        self.retries = int(fb.get("retries", 2))
        self.backoff_s = float(fb.get("backoff_s", 1.5))
        self.circuit_break_after = int(fb.get("circuit_break_after", 3))
        self._consecutive_failures: dict[str, int] = {}
        # Per-call record of which model actually served (read by the ledger).
        self.last_model: str | None = None
        self.last_tier: str | None = None

    def _circuit_open(self, name: str) -> bool:
        return self._consecutive_failures.get(name, 0) >= self.circuit_break_after

    def complete(self, role: str, system: str, user: str, **kw) -> CompletionResult:
        chain = self.registry.resolve_chain(role)
        if not chain:
            raise RoutingError(
                f"no usable model for role '{role}'. Check config [roles] and API keys."
            )
        last_err: Exception | None = None
        for client in chain:
            if self._circuit_open(client.name):
                continue
            for attempt in range(self.retries + 1):
                try:
                    result = client.complete(system, user, **kw)
                    self._consecutive_failures[client.name] = 0
                    # carry identity on the result (thread-safe under parallel execution)
                    result.model_name = result.model_name or client.name
                    result.tier = client.tier
                    self.last_model = client.name   # kept for non-concurrent callers/tests
                    self.last_tier = client.tier
                    return result
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    self._consecutive_failures[client.name] = (
                        self._consecutive_failures.get(client.name, 0) + 1
                    )
                    if attempt < self.retries:
                        time.sleep(self.backoff_s * (attempt + 1))
            # exhausted this client's retries -> fall through to next in chain
        raise RoutingError(f"all models failed for role '{role}': {last_err}")
