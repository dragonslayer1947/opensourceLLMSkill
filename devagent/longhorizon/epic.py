"""Epic decomposition — the top of the planning hierarchy.

A long-horizon goal is decomposed by a frontier model into a tree:

    epic → story → task

Each node carries explicit **preconditions** ("what must be true before this can start") and
**postconditions** ("what is true once this is done"). Tasks are leaves: each is a small,
in-envelope unit a local model can execute, with `target_files` and `depends_on`.

The frontier model writes the *plan only* (the same discipline as `decompose.planner`); its
output is tiny but it creates the structure that keeps every leaf inside the parity envelope.

The immutable plan lives in `.devagent/epics/<id>/epic.yaml`. Mutable progress (which node is
done) is kept separately by `longhorizon.runner` so a multi-day epic can checkpoint and resume."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

EPICS_DIR = ".devagent/epics"
KINDS = ("epic", "story", "task")

DECOMPOSE_SYSTEM = """\
You are a staff engineer who breaks a large goal into a coherent, multi-week plan.
You do NOT write code. You output a plan only.

Produce a tree: the goal is the EPIC; under it are STORIES (shippable slices of value); under
each story are TASKS. Each TASK must be small enough for a junior model to implement with a tiny
slice of context: touch at most {max_files} file(s), be one coherent change, have a testable
outcome. Order tasks so dependencies come first.

Every node states its pre/postconditions:
- preconditions: what must already be true before work can start (one short phrase each),
- postconditions: what is verifiably true once the node is complete.

Return ONLY this JSON (no prose):
{{
  "title": "<short epic title>",
  "preconditions": ["..."],
  "postconditions": ["..."],
  "stories": [
    {{"id": "S1", "title": "...", "description": "...",
      "preconditions": ["..."], "postconditions": ["..."],
      "tasks": [
        {{"id": "T1.1", "title": "...", "description": "...",
          "target_files": ["path/a.py"], "depends_on": [],
          "preconditions": ["..."], "postconditions": ["..."]}}
      ]}}
  ]
}}
"""


@dataclass
class Node:
    id: str
    kind: str                       # epic | story | task
    title: str
    description: str = ""
    parent: str | None = None
    depends_on: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title}
        if self.description:
            d["description"] = self.description
        if self.parent:
            d["parent"] = self.parent
        for key in ("depends_on", "preconditions", "postconditions", "target_files"):
            val = getattr(self, key)
            if val:
                d[key] = val
        return d


@dataclass
class Epic:
    id: str
    goal: str
    nodes: list[Node] = field(default_factory=list)
    created: str = ""
    decomposed: bool = False
    planner_model: str | None = None

    # --- traversal helpers -------------------------------------------------
    def by_id(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    @property
    def root(self) -> Node | None:
        return next((n for n in self.nodes if n.kind == "epic"), None)

    def children_of(self, node_id: str) -> list[Node]:
        return [n for n in self.nodes if n.parent == node_id]

    def tasks(self) -> list[Node]:
        """Leaf task nodes — the executable units."""
        return [n for n in self.nodes if n.kind == "task"]

    def stories(self) -> list[Node]:
        return [n for n in self.nodes if n.kind == "story"]

    def to_yaml(self) -> str:
        payload = {
            "id": self.id,
            "goal": self.goal,
            "created": self.created,
            "decomposed": self.decomposed,
            "planner_model": self.planner_model,
            "nodes": [n.to_dict() for n in self.nodes],
        }
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def epics_dir(root: Path) -> Path:
    return root / EPICS_DIR


def epic_dir(root: Path, epic_id: str) -> Path:
    return epics_dir(root) / epic_id


def epic_path(root: Path, epic_id: str) -> Path:
    return epic_dir(root, epic_id) / "epic.yaml"


def next_epic_id(root: Path) -> str:
    d = epics_dir(root)
    existing = [p.name for p in d.glob("E-*")] if d.exists() else []
    nums = [int(m.group(1)) for n in existing if (m := re.match(r"E-(\d+)$", n))]
    return f"E-{(max(nums) + 1) if nums else 1:04d}"


def save_epic(root: Path, epic: Epic) -> Path:
    d = epic_dir(root, epic.id)
    d.mkdir(parents=True, exist_ok=True)
    p = epic_path(root, epic.id)
    p.write_text(epic.to_yaml(), encoding="utf-8")
    return p


def _node_from_dict(data: dict) -> Node:
    return Node(
        id=str(data.get("id", "?")),
        kind=str(data.get("kind", "task")),
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        parent=data.get("parent"),
        depends_on=[str(x) for x in data.get("depends_on", []) or []],
        preconditions=[str(x) for x in data.get("preconditions", []) or []],
        postconditions=[str(x) for x in data.get("postconditions", []) or []],
        target_files=[str(x) for x in data.get("target_files", []) or []],
    )


def load_epic(root: Path, epic_id: str) -> Epic | None:
    p = epic_path(root, epic_id)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    nodes = [_node_from_dict(n) for n in data.get("nodes", []) or []]
    return Epic(
        id=str(data.get("id", epic_id)),
        goal=str(data.get("goal", "")),
        nodes=nodes,
        created=str(data.get("created", "")),
        decomposed=bool(data.get("decomposed", False)),
        planner_model=data.get("planner_model"),
    )


def list_epics(root: Path) -> list[Epic]:
    d = epics_dir(root)
    if not d.exists():
        return []
    out = []
    for sub in sorted(d.iterdir()):
        if sub.is_dir() and (sub / "epic.yaml").exists():
            e = load_epic(root, sub.name)
            if e:
                out.append(e)
    return out


def _extract_json_object(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text or "", re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        raw = m.group(0) if m else None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def build_epic(epic_id: str, goal: str, parsed: dict) -> Epic:
    """Flatten the planner's nested JSON into an ordered node list with parent links.

    Story/task ids from the model are namespaced under the epic to guarantee uniqueness, and
    `depends_on` references are remapped to the namespaced ids so the runner can resolve them."""
    nodes: list[Node] = [Node(
        id=epic_id, kind="epic", title=str(parsed.get("title", goal))[:120],
        description=goal,
        preconditions=[str(x) for x in parsed.get("preconditions", []) or []],
        postconditions=[str(x) for x in parsed.get("postconditions", []) or []],
    )]

    # First pass: assign stable namespaced ids and remember the local->global mapping.
    id_map: dict[str, str] = {}
    stories = parsed.get("stories", []) or []
    for si, story in enumerate(stories, 1):
        if not isinstance(story, dict):
            continue
        sid = f"{epic_id}.S{si}"
        id_map[str(story.get("id") or f"S{si}")] = sid
        for ti, task in enumerate(story.get("tasks", []) or [], 1):
            if isinstance(task, dict):
                tid = f"{sid}.T{ti}"
                id_map[str(task.get("id") or f"T{si}.{ti}")] = tid

    def remap(refs) -> list[str]:
        return [id_map.get(str(r), str(r)) for r in (refs or [])]

    for si, story in enumerate(stories, 1):
        if not isinstance(story, dict):
            continue
        sid = id_map[str(story.get("id") or f"S{si}")]
        nodes.append(Node(
            id=sid, kind="story", parent=epic_id,
            title=str(story.get("title", f"Story {si}"))[:120],
            description=str(story.get("description", "")),
            depends_on=remap(story.get("depends_on")),
            preconditions=[str(x) for x in story.get("preconditions", []) or []],
            postconditions=[str(x) for x in story.get("postconditions", []) or []],
        ))
        for ti, task in enumerate(story.get("tasks", []) or [], 1):
            if not isinstance(task, dict):
                continue
            tid = id_map[str(task.get("id") or f"T{si}.{ti}")]
            nodes.append(Node(
                id=tid, kind="task", parent=sid,
                title=str(task.get("title", f"Task {si}.{ti}"))[:120],
                description=str(task.get("description", "")),
                depends_on=remap(task.get("depends_on")),
                preconditions=[str(x) for x in task.get("preconditions", []) or []],
                postconditions=[str(x) for x in task.get("postconditions", []) or []],
                target_files=[str(p) for p in task.get("target_files", []) or [] if p],
            ))
    return Epic(id=epic_id, goal=goal, nodes=nodes, created=_now(), decomposed=True)


def decompose_epic(
    epic_id: str, goal: str, router, *, max_subtask_files: int, skeleton: str = "",
) -> Epic:
    """Frontier-model decomposition of a goal into an epic tree. On unusable output, falls back
    to a single-task epic so the caller always gets a runnable plan."""
    system = DECOMPOSE_SYSTEM.format(max_files=max_subtask_files)
    user = (
        f"GOAL:\n{goal}\n\n"
        f"REPO MAP (signatures only):\n{skeleton or '(empty repo)'}\n\n"
        f"Decompose into epic → stories → tasks."
    )
    result = router.complete("planner", system, user, max_tokens=3000, cacheable_system=True)
    parsed = _extract_json_object(result.text)
    if not parsed or not parsed.get("stories"):
        epic = _single_task_epic(epic_id, goal)
        epic.planner_model = result.model_name
        return epic
    epic = build_epic(epic_id, goal, parsed)
    epic.planner_model = result.model_name
    return epic


def _single_task_epic(epic_id: str, goal: str) -> Epic:
    return Epic(
        id=epic_id, goal=goal, created=_now(), decomposed=True,
        nodes=[
            Node(id=epic_id, kind="epic", title=goal[:120], description=goal),
            Node(id=f"{epic_id}.S1", kind="story", parent=epic_id, title=goal[:120]),
            Node(id=f"{epic_id}.S1.T1", kind="task", parent=f"{epic_id}.S1",
                 title=goal[:120], description=goal),
        ],
    )
