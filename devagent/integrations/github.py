"""GitHub provider via the `gh` CLI — uses the user's existing `gh auth` (no token plumbing).

Like the `claude` CLI model provider, this shells out to a tool the user has already logged in,
so there is no API-key handling here. If `gh` is missing or not authenticated, the methods raise
a clear `IntegrationError`; callers fall back to the null provider. Nothing is imported at module
load that requires `gh`, so this file is safe to build and test offline."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import Ref, record_outbox


class IntegrationError(RuntimeError):
    pass


class GitHubProvider:
    name = "github"

    def __init__(self, root: Path, repo: str = "", command: str = "gh"):
        self.root = root
        self.repo = repo          # "owner/name"; empty => gh infers from cwd
        self.command = command

    def _gh(self, args: list[str]) -> str:
        if not shutil.which(self.command):
            raise IntegrationError(f"`{self.command}` not found on PATH — install GitHub CLI or "
                                   f"use the null provider")
        try:
            proc = subprocess.run([self.command, *args], cwd=str(self.root),
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise IntegrationError(f"gh failed: {e}") from e
        if proc.returncode != 0:
            raise IntegrationError(f"gh {' '.join(args)} → {proc.stderr.strip()[:300]}")
        return proc.stdout.strip()

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Ref:
        args = ["issue", "create", "--title", title, "--body", body or title]
        if self.repo:
            args += ["--repo", self.repo]
        for lab in labels or []:
            args += ["--label", lab]
        out = self._gh(args)
        url = out.splitlines()[-1] if out else ""
        record_outbox(self.root, self.name, "issue", {"title": title, "url": url})
        return Ref("issue", external_id=url.rsplit("/", 1)[-1], url=url, provider=self.name)

    def create_pr(self, title: str, body: str, branch: str, base: str = "main") -> Ref:
        args = ["pr", "create", "--title", title, "--body", body or title,
                "--head", branch, "--base", base]
        if self.repo:
            args += ["--repo", self.repo]
        url = self._gh(args).splitlines()[-1]
        record_outbox(self.root, self.name, "pr", {"title": title, "url": url, "branch": branch})
        return Ref("pr", external_id=url.rsplit("/", 1)[-1], url=url, provider=self.name)

    def request_approval(self, subject: str, detail: str) -> Ref:
        # GitHub approvals are PR reviews; surface as a labelled issue requesting sign-off.
        ref = self.create_issue(f"[approval] {subject}", detail, labels=["needs-approval"])
        return Ref("approval", external_id=ref.external_id, url=ref.url, provider=self.name)
