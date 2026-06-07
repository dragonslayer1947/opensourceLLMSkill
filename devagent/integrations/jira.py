"""Jira provider via REST (urllib, stdlib only — no extra dependency).

Activates when configured with a `base_url`, a `project` key, and a token in an env var. Issues
are created against `/rest/api/2/issue`; approvals are modelled as a `Task` issue tagged for
sign-off. With no credentials the constructor raises `IntegrationError`, so callers fall back to
the null provider. No network is touched at import time."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .base import Ref, record_outbox
from .github import IntegrationError


class JiraProvider:
    name = "jira"

    def __init__(self, root: Path, base_url: str = "", project: str = "",
                 email: str = "", token_env: str = "JIRA_API_TOKEN", timeout_s: int = 30):
        self.root = root
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.email = email
        self.token = os.environ.get(token_env, "")
        self.timeout_s = timeout_s
        if not (self.base_url and self.project and self.token):
            raise IntegrationError(
                "jira provider needs base_url + project + a token "
                f"(env {token_env}); falling back to null")

    def _auth_header(self) -> str:
        if self.email:  # Atlassian Cloud: basic email:token
            raw = f"{self.email}:{self.token}".encode("utf-8")
            return "Basic " + base64.b64encode(raw).decode("ascii")
        return f"Bearer {self.token}"  # Jira Server / PAT

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": self._auth_header()},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            raise IntegrationError(f"jira POST {path} failed: {e}") from e

    def _create(self, summary: str, description: str, issue_type: str,
                labels: list[str] | None = None) -> Ref:
        payload = {"fields": {
            "project": {"key": self.project}, "summary": summary[:250],
            "description": description, "issuetype": {"name": issue_type},
            "labels": labels or [],
        }}
        data = self._post("/rest/api/2/issue", payload)
        key = str(data.get("key", "?"))
        url = f"{self.base_url}/browse/{key}"
        record_outbox(self.root, self.name, issue_type.lower(), {"key": key, "summary": summary})
        return Ref("issue", external_id=key, url=url, provider=self.name)

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Ref:
        return self._create(title, body, "Story", labels)

    def create_pr(self, title: str, body: str, branch: str, base: str = "main") -> Ref:
        # Jira has no PRs; record the intent as a Task referencing the branch.
        return self._create(f"{title} ({branch}→{base})", body, "Task")

    def request_approval(self, subject: str, detail: str) -> Ref:
        ref = self._create(f"[approval] {subject}", detail, "Task", labels=["needs-approval"])
        return Ref("approval", external_id=ref.external_id, url=ref.url, provider=self.name)
