"""Interactive shell — what you get when you type `devagent` with no subcommand.

Instead of spawning a fresh process per action, the REPL holds one resident session: the working
repo, a build-once config + router (so circuit-breaker / fallback state survives across turns),
the current run-flags, and history. You talk to it turn by turn, like the `claude` or `codex`
CLIs.

Input routing is deliberately predictable, because this tool edits code:
- plain text            → a coding TASK, run through the full pipeline (with confirmations),
- `/ask <question>`     → read-only Q&A about the repo via the local model (never edits),
- `/<session-command>`  → handled here (repo, flag toggles, clear, help, exit),
- any other `/<command>`→ passed straight through to the existing Typer CLI (so `/epic`, `/cost`,
                          `/trace`, `/undo`, … all work inside the shell with no duplication).

The parsing (`parse_line`) and flag logic are pure functions so they are unit-testable offline;
the loop itself is thin I/O."""
from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from . import __version__

SESSION_COMMANDS = {"exit", "quit", "q", "help", "?", "h", "ask", "a", "repo", "cd",
                    "clear", "flags", "dry", "auto", "review", "test", "parallel",
                    "executor", "planner"}
TOGGLES = {"dry", "auto", "review", "test", "parallel"}
ROLE_COMMANDS = {"executor", "planner"}


@dataclass
class Flags:
    dry: bool = False        # preview edits, write nothing
    auto: bool = False       # skip keep/rollback confirm (assume yes)
    review: bool = False     # reviewer agent on each diff
    test: bool = False       # run tests after apply, auto-rollback on failure
    parallel: bool = False   # run independent subtasks concurrently

    def label(self) -> str:
        on = [n for n in ("dry", "auto", "review", "test", "parallel") if getattr(self, n)]
        return f"[{','.join(on)}] " if on else ""


@dataclass
class Action:
    kind: str                       # empty|exit|help|task|ask|toggle|repo|clear|flags|passthrough
    arg: str = ""                   # raw argument text (task, question, path, toggle name)
    args: list[str] = field(default_factory=list)  # tokenized args (passthrough)


@dataclass
class Session:
    path: Path
    flags: Flags = field(default_factory=Flags)
    history: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)  # per-session role overrides
    cfg: object | None = None
    router: object | None = None


def parse_line(line: str) -> Action:
    """Map a raw input line to an Action. Pure — no I/O."""
    s = (line or "").strip()
    if not s:
        return Action("empty")
    if not s.startswith("/"):
        return Action("task", arg=s)

    body = s[1:].strip()
    cmd, _, rest = body.partition(" ")
    cmd = cmd.lower()
    rest = rest.strip()

    if cmd in ("exit", "quit", "q"):
        return Action("exit")
    if cmd in ("help", "?", "h"):
        return Action("help")
    if cmd in ("ask", "a"):
        return Action("ask", arg=rest)
    if cmd in ("repo", "cd"):
        return Action("repo", arg=rest)          # raw — keeps Windows backslash paths intact
    if cmd == "clear":
        return Action("clear")
    if cmd == "flags":
        return Action("flags")
    if cmd in TOGGLES:
        return Action("toggle", arg=cmd)
    if cmd in ROLE_COMMANDS:
        return Action("role", arg=cmd, args=([rest] if rest else []))

    # Unknown slash command → pass through to the Typer CLI.
    try:
        args = shlex.split(body)
    except ValueError:
        args = body.split()
    return Action("passthrough", args=args)


def toggle_flag(flags: Flags, name: str) -> bool:
    """Flip a run flag in place; return the new value. Pure-ish (mutates the dataclass)."""
    new = not getattr(flags, name)
    setattr(flags, name, new)
    return new


# ── I/O layer ────────────────────────────────────────────────────────────────────────────

class _Reader:
    """Line reader: prompt_toolkit (history + editing) when available on a TTY, else input()."""

    def __init__(self):
        self.session = None
        if sys.stdin.isatty():
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.history import FileHistory
                hist = Path.home() / ".devagent" / "repl_history"
                hist.parent.mkdir(parents=True, exist_ok=True)
                self.session = PromptSession(history=FileHistory(str(hist)))
            except Exception:  # noqa: BLE001 — any setup issue → plain input()
                self.session = None

    def read(self, prompt_str: str) -> str:
        if self.session is not None:
            return self.session.prompt(prompt_str)
        return input(prompt_str)


def _banner(session: Session, console: Console) -> None:
    console.print(f"[bold]devagent[/bold] {__version__} — interactive shell")
    console.print(f"repo: [cyan]{session.path}[/cyan]")
    console.print("[dim]type a task to run it · /ask <q> to ask · /help · /exit[/dim]\n")


HELP = """\
[bold]How to talk to devagent[/bold]
  <text>              run it as a coding task (decompose → execute → gate → apply)
  /ask <question>     ask about the repo — read-only, no edits
  /repo <path>        switch the working repo (alias /cd)
  /flags              show current run flags + role overrides
  /dry /auto /review /test /parallel   toggle a run flag
  /executor <model>   route execution to a model this session (e.g. /executor claude-cli);
                      no arg resets to config. /planner <model> likewise.
  /clear              forget this session's history
  /help               this help        /exit  quit (or Ctrl-D)

[bold]Everything else passes through to the CLI[/bold], e.g.:
  /status   /cost   /quality   /log   /trace   /undo
  /epic plan "<goal>"   /epic list   /epic show E-0001   /epic run E-0001
  /propose "<goal>"     /reserve service:x --owner me   /adr list
"""


def _do_task(session: Session, task: str, console: Console) -> None:
    from . import pipeline
    from .models.router import RoutingError
    f = session.flags
    try:
        result = pipeline.run(task, str(session.path), dry_run=f.dry, assume_yes=f.auto,
                              console=console, review=f.review, test=f.test, parallel=f.parallel,
                              role_overrides=session.roles or None)
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        console.print("[dim]no local server? run [bold]/executor claude-cli[/bold] to execute "
                      "via the Claude CLI (no local model needed), then retry. Or /status.[/dim]")
        return
    session.history.append(task)
    for o in result.outcomes:
        if o.status == "applied":
            session.touched.extend(o.changed_files)
    from .cli import _run_summary
    _run_summary(result)


def _do_ask(session: Session, question: str, console: Console) -> None:
    if not question:
        console.print("[yellow]usage: /ask <question about the repo>[/yellow]")
        return
    from .context.cache import build_index_cached
    from .context.retrieve import retrieve
    from .models.router import RoutingError
    env = session.cfg.envelope  # type: ignore[union-attr]
    index = build_index_cached(session.path)
    bundle = retrieve(index, question,
                      max_context_tokens=int(env.get("max_context_tokens", 12000)),
                      max_file_lines=int(env.get("max_file_lines", 400)))
    system = ("You answer questions about THIS codebase for a senior engineer. Be concise and "
              "concrete, cite file paths. You are READ-ONLY: do not propose edits unless asked.")
    user = f"REPO CONTEXT (retrieved):\n{bundle.render()}\n\nQUESTION: {question}"
    from .ui import activity
    try:
        with activity(console, "Thinking"):
            res = session.router.complete("executor", system, user, max_tokens=800)  # type: ignore[union-attr]
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        return
    console.print(res.text.strip() or "[dim](no answer)[/dim]")


def _do_repo(session: Session, raw: str, console: Console) -> None:
    if not raw:
        console.print(f"repo: [cyan]{session.path}[/cyan]")
        return
    new = Path(raw.strip().strip('"').strip("'")).expanduser()
    if not new.is_dir():
        console.print(f"[red]not a directory:[/red] {new}")
        return
    session.path = new.resolve()
    session.touched.clear()
    console.print(f"repo → [cyan]{session.path}[/cyan]")


def _do_passthrough(args: list[str], console: Console) -> None:
    import click
    import typer

    from .cli import app
    from .models.router import RoutingError
    cmd = typer.main.get_command(app)
    try:
        cmd.main(args=args, prog_name="devagent", standalone_mode=False)
    except SystemExit:
        pass
    except click.ClickException as e:
        e.show()
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
    except Exception as e:  # noqa: BLE001 — a passthrough command must not kill the shell
        console.print(f"[red]error:[/red] {e}")


def dispatch(action: Action, session: Session, console: Console) -> bool:
    """Execute one action. Returns False when the shell should exit."""
    k = action.kind
    if k in ("empty",):
        return True
    if k == "exit":
        return False
    if k == "help":
        console.print(HELP)
    elif k == "task":
        _do_task(session, action.arg, console)
    elif k == "ask":
        _do_ask(session, action.arg, console)
    elif k == "repo":
        _do_repo(session, action.arg, console)
    elif k == "clear":
        session.history.clear()
        session.touched.clear()
        console.print("[dim]session history cleared[/dim]")
    elif k == "flags":
        f = session.flags
        roles = ", ".join(f"{r}={m}" for r, m in session.roles.items()) or "config defaults"
        console.print(f"dry={f.dry} auto={f.auto} review={f.review} test={f.test} "
                      f"parallel={f.parallel}  ·  roles: {roles}")
    elif k == "toggle":
        val = toggle_flag(session.flags, action.arg)
        console.print(f"{action.arg} = [bold]{val}[/bold]")
    elif k == "role":
        if action.args:
            session.roles[action.arg] = action.args[0]
            console.print(f"{action.arg} → [bold]{action.args[0]}[/bold] (this session)")
        else:
            session.roles.pop(action.arg, None)
            console.print(f"{action.arg} reset to config default")
    elif k == "passthrough":
        _do_passthrough(action.args, console)
    return True


def run_repl(path: str = ".") -> None:
    from .config import load_config
    from .models.registry import Registry
    from .models.router import Router

    console = Console()
    cfg = load_config()
    session = Session(path=Path(path).resolve(), cfg=cfg, router=Router(Registry(cfg)))
    _banner(session, console)
    reader = _Reader()

    while True:
        prompt_str = f"\n{session.flags.label()}devagent ({session.path.name})> "
        try:
            line = reader.read(prompt_str)
        except KeyboardInterrupt:
            console.print("[dim](ctrl-c — type /exit or Ctrl-D to quit)[/dim]")
            continue
        except EOFError:
            break
        try:
            if not dispatch(parse_line(line), session, console):
                break
        except KeyboardInterrupt:
            console.print("\n[yellow]aborted — back to prompt[/yellow]")
    console.print("[dim]bye[/dim]")
