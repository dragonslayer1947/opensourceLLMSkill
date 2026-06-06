"""The local executor — stateless, single-shot. Each subtask is a fresh minimal prompt; no
growing conversation. Produces search/replace edit blocks for a small slice of context."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..context.retrieve import ContextBundle
from ..decompose.planner import Subtask
from ..models.router import Router
from .edits import Edit, parse_edits

EXECUTOR_SYSTEM = """\
You are a precise coding model. Implement EXACTLY the requested change and nothing more.
Follow the conventions visible in the provided context.

Output ONLY search/replace edit blocks, one per change, in this exact format:

path/to/file.py
<<<<<<< SEARCH
<exact existing lines to replace>
=======
<new lines>
>>>>>>> REPLACE

Rules:
- The SEARCH text must match the existing file exactly (copy it from the context).
- To create a new file, use an empty SEARCH block.
- Keep changes minimal and localized. Do not reformat unrelated code.
- No explanation, no prose — only edit blocks.
"""


@dataclass
class ExecOutput:
    edits: list[Edit]
    raw: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None
    tier: str | None = None
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)


def build_executor_prompt(subtask: Subtask, bundle: ContextBundle, extra_guidance: str = "") -> tuple[str, str]:
    """The (system, user) pair for a generation. Shared by the executor and the audit so the
    local and frontier models are compared on an identical prompt."""
    context = bundle.render() or "(no file context retrieved — create new files as needed)"
    guidance = f"\nADDITIONAL GUIDANCE (follow precisely):\n{extra_guidance}\n" if extra_guidance else ""
    user = (
        f"TASK:\n{subtask.description}\n\n"
        f"CONTEXT (the only files you may edit; large files are windowed):\n{context}\n"
        f"{guidance}\n"
        f"Produce the edit blocks now."
    )
    return EXECUTOR_SYSTEM, user


def execute_subtask(
    subtask: Subtask,
    bundle: ContextBundle,
    router: Router,
    *,
    extra_guidance: str = "",
    role: str = "executor",
) -> ExecOutput:
    system, user = build_executor_prompt(subtask, bundle, extra_guidance)
    result = router.complete(role, system, user)
    edits = parse_edits(result.text)
    notes = [] if edits else ["no edit blocks parsed from model output"]
    return ExecOutput(
        edits=edits,
        raw=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        model=router.last_model,
        tier=router.last_tier,
        cost_usd=result.cost_usd,
        notes=notes,
    )
