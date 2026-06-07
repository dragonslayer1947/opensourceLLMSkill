"""Wave scheduler for parallel execution (V3).

Groups subtasks into ordered WAVES that can each run concurrently:
  - dependency-respecting: a subtask runs only after everything in its `depends_on` has run;
  - file-disjoint: no two subtasks in the same wave touch the same file (the file-claim rule).

A subtask with no declared target_files has an unknown footprint, so it runs alone (its own
wave) to stay safe. Cycles / missing deps never deadlock — the remaining subtasks are forced
into a final wave."""
from __future__ import annotations


def schedule(subtasks: list) -> list[list]:
    """Return a list of waves; each wave is a list of subtasks safe to run in parallel."""
    remaining = list(subtasks)
    completed: set[str] = set()
    all_ids = {s.id for s in subtasks}
    waves: list[list] = []

    while remaining:
        # deps satisfied if every dependency is already completed or doesn't exist in this set
        ready = [s for s in remaining
                 if all(d in completed or d not in all_ids for d in (s.depends_on or []))]
        if not ready:
            ready = list(remaining)  # cycle / unresolved dep -> force progress

        wave: list = []
        claimed: set[str] = set()
        for s in ready:
            files = set(s.target_files or [])
            if not files:
                # unknown footprint: only alone in its own wave
                if not wave:
                    wave.append(s)
                    claimed = {"<all>"}
                break
            if "<all>" in claimed or files & claimed:
                continue
            wave.append(s)
            claimed |= files

        if not wave:                 # e.g. first ready item had no files but wave got seeded
            wave.append(ready[0])

        waves.append(wave)
        for s in wave:
            completed.add(s.id)
            remaining.remove(s)
    return waves


def max_parallelism(waves: list[list]) -> int:
    return max((len(w) for w in waves), default=0)
