"""Observability — the decision trail behind `devagent trace` (closes gap #10).

Every run records *why* it did what it did: the classifier's inputs and verdict, how much context
was assembled, which rules fired, the blast radius, and per-subtask cost / time / model / status.
The trail is written to `.devagent/traces/<session>.json` and rendered by `devagent trace`."""
