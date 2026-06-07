"""Select an org-workflow provider from config — defaulting to the offline null provider.

Config (all optional; absent ⇒ null):

    [integrations]
    provider = "github"          # null | github | jira | slack

    [integrations.github]
    repo = "acme/orders"

    [integrations.jira]
    base_url = "https://acme.atlassian.net"
    project  = "ORD"
    email    = "bot@acme.com"
    token_env = "JIRA_API_TOKEN"

    [integrations.slack]
    webhook_env = "SLACK_WEBHOOK_URL"

If a real provider can't initialize (missing CLI / credentials), we degrade to null rather than
fail — org integration is always best-effort and never blocks core work."""
from __future__ import annotations

from pathlib import Path

from .base import NullProvider, Provider
from .github import IntegrationError


def get_provider(config, root: Path, *, override: str | None = None) -> Provider:
    section = {}
    try:
        section = (config.raw or {}).get("integrations", {}) or {}
    except AttributeError:
        section = {}
    name = (override or section.get("provider") or "null").lower()
    if name == "null":
        return NullProvider(root)
    try:
        if name == "github":
            from .github import GitHubProvider
            gh = section.get("github", {}) or {}
            return GitHubProvider(root, repo=gh.get("repo", ""),
                                  command=gh.get("command", "gh"))
        if name == "jira":
            from .jira import JiraProvider
            j = section.get("jira", {}) or {}
            return JiraProvider(root, base_url=j.get("base_url", ""), project=j.get("project", ""),
                                email=j.get("email", ""),
                                token_env=j.get("token_env", "JIRA_API_TOKEN"))
        if name == "slack":
            from .slack import SlackProvider
            s = section.get("slack", {}) or {}
            return SlackProvider(root, webhook_env=s.get("webhook_env", "SLACK_WEBHOOK_URL"))
    except IntegrationError:
        return NullProvider(root)
    return NullProvider(root)
