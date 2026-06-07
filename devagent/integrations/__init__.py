"""Organizational-workflow integration — Jira, GitHub, Slack as first-class primitives (V5).

The system is provider-agnostic, exactly like the model layer: a single `Provider` protocol
(`base.py`) with `create_issue` / `create_pr` / `request_approval`, plus N config-declared
providers. The **default is `null`**: it writes every intent to `.devagent/integrations/outbox.jsonl`
and returns synthetic ids — fully offline, side-effect-free, and the same "data onto files"
pattern the CLI model uses. Real providers (`github` via the `gh` CLI, `jira` via REST, `slack`
via a webhook) activate only when configured with credentials, so nothing here requires network
access to build, test, or run."""
