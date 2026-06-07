"""Live activity indicators — so the terminal never looks frozen.

The slow parts of a run are model calls (the `claude` CLI subprocess can take 30s+) and the gate
subprocesses; without feedback the terminal looks dead. `activity()` shows an animated spinner
with the current step, a ticking elapsed clock, and a "Ctrl-C to abort" hint while such an
operation runs, then clears itself.

It degrades safely:
- non-interactive console (pipe / CI / tests) → one quiet line, no animation;
- `enabled=False` (e.g. under --parallel, where only one live display may be active at once and
  the per-subtask `console.print`s already narrate progress) → a no-op pass-through."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from rich.console import Console


@contextmanager
def activity(console: Console, message: str, *, enabled: bool = True,
             spinner: str = "dots", hint: str = "Ctrl-C to abort"):
    if not enabled:
        yield
        return
    if not console.is_terminal:
        console.print(f"[dim]· {message}…[/dim]")
        yield
        return

    start = time.monotonic()
    status = console.status(f"[bold cyan]{message}[/bold cyan]…  [dim]({hint})[/dim]",
                            spinner=spinner)
    stop = threading.Event()

    def _tick() -> None:
        while not stop.wait(0.5):
            secs = time.monotonic() - start
            status.update(f"[bold cyan]{message}[/bold cyan]…  "
                          f"[dim]{secs:0.0f}s · {hint}[/dim]")

    status.start()
    ticker = threading.Thread(target=_tick, daemon=True)
    ticker.start()
    try:
        yield
    finally:
        stop.set()
        ticker.join(timeout=1.0)
        status.stop()
