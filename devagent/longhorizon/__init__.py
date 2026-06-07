"""V5 — autonomous long-horizon work.

Features that span weeks, teams, and services without losing coherence:
- epic decomposition (epic → story → task, each with pre/postconditions),
- multi-day task graphs with checkpointing + resume,
- predictive conflict detection before execution starts,
- a cross-team reservation system for shared resources,
- autonomous architectural proposals behind a human approval gate.

Organizational-workflow integration (Jira / GitHub / Slack) lives in `devagent.integrations`;
the decision-trail observability that backs `devagent trace` lives in `devagent.observability`."""
