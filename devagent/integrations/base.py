"""Provider protocol + the offline default (`NullProvider`).

A provider turns devagent intents into organizational artifacts: a tracker issue, a pull request,
an approval request. Every provider returns a `Ref` (a kind + an external id + an optional URL)
and appends a record of what it did to the run's outbox, so there is always a durable audit trail
regardless of which backend is wired."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

OUTBOX = ".devagent/integrations/outbox.jsonl"


@dataclass
class Ref:
    kind: str               # issue | pr | approval
    external_id: str
    url: str = ""
    provider: str = "null"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "external_id": self.external_id,
                "url": self.url, "provider": self.provider}


@runtime_checkable
class Provider(Protocol):
    name: str

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Ref: ...
    def create_pr(self, title: str, body: str, branch: str, base: str = "main") -> Ref: ...
    def request_approval(self, subject: str, detail: str) -> Ref: ...


def record_outbox(root: Path, provider: str, action: str, payload: dict) -> None:
    """Append an intent to the durable outbox (newline-delimited JSON)."""
    try:
        p = root / OUTBOX
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "provider": provider, "action": action, "payload": payload,
            }) + "\n")
    except Exception:  # noqa: BLE001 — the outbox is an audit convenience, never fatal
        pass


class NullProvider:
    """Records intents to the outbox and returns synthetic refs. Offline, deterministic, safe.
    The default provider — and what the test-suite uses — so org integration is fully exercisable
    without any credentials or network."""

    name = "null"

    def __init__(self, root: Path):
        self.root = root
        self._n = 0

    def _ref(self, kind: str, title: str, **payload) -> Ref:
        self._n += 1
        ext = f"NULL-{kind.upper()}-{self._n}"
        record_outbox(self.root, self.name, kind, {"id": ext, "title": title, **payload})
        return Ref(kind=kind, external_id=ext, url=f"null://{kind}/{self._n}", provider=self.name)

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Ref:
        return self._ref("issue", title, body=body, labels=labels or [])

    def create_pr(self, title: str, body: str, branch: str, base: str = "main") -> Ref:
        return self._ref("pr", title, body=body, branch=branch, base=base)

    def request_approval(self, subject: str, detail: str) -> Ref:
        return self._ref("approval", subject, detail=detail)
