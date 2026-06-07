"""Slack provider via an incoming webhook (urllib, stdlib only).

Slack has no issues or PRs, so this provider specializes in **approvals**: it posts an approval
request to a configured webhook URL and returns a ref. `create_issue` / `create_pr` post a
notification for parity with the protocol. The webhook URL comes from an env var; with none set
the constructor raises `IntegrationError` and callers fall back to null. No network at import."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .base import Ref, record_outbox
from .github import IntegrationError


class SlackProvider:
    name = "slack"

    def __init__(self, root: Path, webhook_env: str = "SLACK_WEBHOOK_URL", timeout_s: int = 15):
        self.root = root
        self.webhook = os.environ.get(webhook_env, "")
        self.timeout_s = timeout_s
        if not self.webhook:
            raise IntegrationError(
                f"slack provider needs a webhook URL (env {webhook_env}); falling back to null")

    def _post(self, text: str) -> None:
        req = urllib.request.Request(
            self.webhook, data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=self.timeout_s).close()  # noqa: S310
        except (urllib.error.URLError, OSError) as e:
            raise IntegrationError(f"slack post failed: {e}") from e

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Ref:
        self._post(f":memo: *{title}*\n{body}")
        record_outbox(self.root, self.name, "issue", {"title": title})
        return Ref("issue", external_id=title[:40], provider=self.name)

    def create_pr(self, title: str, body: str, branch: str, base: str = "main") -> Ref:
        self._post(f":twisted_rightwards_arrows: *PR* {title} (`{branch}`→`{base}`)\n{body}")
        record_outbox(self.root, self.name, "pr", {"title": title, "branch": branch})
        return Ref("pr", external_id=branch, provider=self.name)

    def request_approval(self, subject: str, detail: str) -> Ref:
        self._post(f":white_check_mark: *Approval requested:* {subject}\n{detail}")
        record_outbox(self.root, self.name, "approval", {"subject": subject})
        return Ref("approval", external_id=subject[:40], provider=self.name)
