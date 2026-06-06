"""CLI-based provider — spawns an installed AI CLI (e.g. `claude`) as a subprocess instead of
calling a metered API. This uses the user's *subscription* auth (Pro/Max), so there is no
per-token API billing. Every exchange is also written to ~/.devagent/cli_io/ as an audit trail.

For `claude`: `claude -p --output-format json --model <m> --append-system-prompt <sys>` with the
user prompt piped via stdin, run from a neutral working directory so it does NOT scan the target
repo (we supply context ourselves). The JSON gives result text, token usage, and total_cost_usd
(the API-equivalent cost — used as our counterfactual; marginal cost is $0 on a subscription)."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .base import CompletionResult, ModelClient, estimate_tokens

CLI_IO_DIR = Path.home() / ".devagent" / "cli_io"
NEUTRAL_CWD = Path.home() / ".devagent" / "_cli_cwd"


class CLIClient(ModelClient):
    def __init__(self, name, model_id, tier, defaults, command, mode, timeout_s):
        super().__init__(name, model_id, tier, defaults)
        self.command = command
        self.mode = mode
        self.timeout_s = timeout_s
        self.exe = shutil.which(command) or command

    def _invocation(self, system: str, user: str) -> tuple[list[str], str, Path | None]:
        """Return (args, stdin_text, output_file). output_file is set for adapters that write
        their final answer to a file (codex) rather than parseable stdout."""
        if self.mode == "claude":
            args = [self.exe, "-p", "--output-format", "json",
                    "--model", self.model_id, "--append-system-prompt", system]
            return args, user, None
        if self.mode == "codex":
            CLI_IO_DIR.mkdir(parents=True, exist_ok=True)
            out = CLI_IO_DIR / f"codex-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.out.txt"
            args = [self.exe, "exec", "-m", self.model_id, "-s", "read-only",
                    "--skip-git-repo-check", "-C", str(NEUTRAL_CWD), "--color", "never",
                    "-o", str(out), "-"]
            return args, f"{system}\n\n{user}", out
        # generic: prompt via stdin, plain text on stdout
        return [self.exe, "-p"], f"{system}\n\n{user}", None

    def complete(self, system, user, *, max_tokens=None, temperature=None, cacheable_system=False):
        NEUTRAL_CWD.mkdir(parents=True, exist_ok=True)
        args, stdin_text, out_file = self._invocation(system, user)
        try:
            p = subprocess.run(
                args, input=stdin_text, capture_output=True, text=True,
                encoding="utf-8", timeout=self.timeout_s, cwd=str(NEUTRAL_CWD),
            )
        except FileNotFoundError:
            raise RuntimeError(f"CLI '{self.command}' not found on PATH")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"CLI '{self.command}' timed out after {self.timeout_s}s")

        if out_file is not None:
            # codex / file-output adapters: the final message is written to out_file
            text = out_file.read_text(encoding="utf-8", errors="replace").strip() if out_file.exists() else ""
            cost, tin, tout = 0.0, estimate_tokens(system + user), estimate_tokens(text)
        else:
            text, cost, tin, tout = self._parse(p.stdout, system, user)

        self._persist(args, stdin_text, p.stdout, p.stderr)
        if not text and p.returncode != 0:
            raise RuntimeError(f"CLI '{self.command}' failed (exit {p.returncode}): {p.stderr.strip()[:300]}")
        return CompletionResult(text=text, tokens_in=tin, tokens_out=tout,
                                cost_usd=cost, model_name=self.name)

    def _parse(self, stdout: str, system: str, user: str):
        if self.mode == "claude":
            d = _load_json(stdout)
            if d:
                if d.get("is_error"):
                    raise RuntimeError(f"claude CLI: {d.get('result', 'error')}")
                u = d.get("usage", {}) or {}
                return (
                    d.get("result", "") or "",
                    float(d.get("total_cost_usd", 0.0) or 0.0),
                    int(u.get("input_tokens", 0) or 0),
                    int(u.get("output_tokens", 0) or 0),
                )
        # generic / fallback: treat stdout as the answer text
        return stdout.strip(), 0.0, estimate_tokens(system + user), estimate_tokens(stdout)

    def _persist(self, args: list[str], user: str, stdout: str, stderr: str) -> None:
        try:
            CLI_IO_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (CLI_IO_DIR / f"{ts}-{self.name}.json").write_text(json.dumps({
                "model": self.name, "args": args, "stdin": user,
                "stdout": stdout, "stderr": stderr,
            }, indent=2), encoding="utf-8")
        except OSError:
            pass  # audit trail is best-effort


def _load_json(stdout: str) -> dict | None:
    s = (stdout or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # tolerate leading noise: parse the last non-empty line
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None
