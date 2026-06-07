"""Push an epic into an org tracker — one issue per story, persisted as a durable mapping.

`sync_epic` creates a tracker issue for the epic and each story (tasks are kept as the issue
body's checklist to avoid issue sprawl), then writes the node→external-id mapping to
`.devagent/epics/<id>/sync.json`. Re-syncing skips nodes already mapped, so it is idempotent and
safe to re-run as an epic grows."""
from __future__ import annotations

import json
from pathlib import Path

from ..longhorizon.epic import Epic
from .base import Provider


def sync_path(root: Path, epic_id: str) -> Path:
    return root / ".devagent" / "epics" / epic_id / "sync.json"


def load_mapping(root: Path, epic_id: str) -> dict:
    p = sync_path(root, epic_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _story_body(epic: Epic, story_id: str) -> str:
    tasks = epic.children_of(story_id)
    lines = [f"- [ ] {t.title}" for t in tasks]
    return "Tasks:\n" + "\n".join(lines) if lines else "(no tasks)"


def sync_epic(root: Path, epic: Epic, provider: Provider) -> dict:
    """Create/refresh tracker issues for the epic and its stories. Returns the node→ref mapping."""
    mapping = load_mapping(root, epic.id)
    if epic.root and epic.root.id not in mapping:
        ref = provider.create_issue(f"[epic] {epic.root.title}", epic.goal, labels=["epic"])
        mapping[epic.root.id] = ref.to_dict()
    for story in epic.stories():
        if story.id in mapping:
            continue
        ref = provider.create_issue(story.title, _story_body(epic, story.id), labels=["story"])
        mapping[story.id] = ref.to_dict()
    p = sync_path(root, epic.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return mapping
