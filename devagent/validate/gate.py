"""The deterministic verification gate — runs after every generation, before escalation.

Order: syntax -> types (mypy) -> lint (ruff) -> security (bandit) -> tests (pytest).
Each check is ~$0. A missing tool is reported as 'skipped', never a silent pass. The gate
result is the objective quality signal recorded in the ledger.

Escalation is triggered by THIS failing — not by any model's self-reported confidence."""
from __future__ import annotations

import ast
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    status: str          # "pass" | "fail" | "skipped"
    detail: str = ""


@dataclass
class GateReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    def to_dict(self) -> dict:
        return {c.name: c.status for c in self.checks}

    def render(self) -> str:
        lines = []
        for c in self.checks:
            mark = {"pass": "✓", "fail": "✗", "skipped": "–"}[c.status]
            lines.append(f"  {mark} {c.name}: {c.status}" + (f"  {c.detail[:200]}" if c.detail else ""))
        return "\n".join(lines)


def _run(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return -1, "tool not installed"
    except subprocess.TimeoutExpired:
        return -2, "timed out"


def _syntax_check(root: Path, files: list[str]) -> CheckResult:
    bad = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        p = root / rel
        if not p.exists():
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            bad.append(f"{rel}:{e.lineno} {e.msg}")
    if bad:
        return CheckResult("syntax", "fail", "; ".join(bad))
    return CheckResult("syntax", "pass")


def _tool_check(name: str, exe: str, args: list[str], root: Path, files: list[str],
                py_only: bool = True) -> CheckResult:
    if shutil.which(exe) is None:
        return CheckResult(name, "skipped", "not installed")
    targets = [f for f in files if f.endswith(".py")] if py_only else files
    if not targets:
        return CheckResult(name, "skipped", "no applicable files")
    code, out = _run([exe, *args, *targets], root)
    if code == -1:
        return CheckResult(name, "skipped", "not installed")
    if code == -2:
        return CheckResult(name, "fail", "timed out")
    if code == 0:
        return CheckResult(name, "pass")
    return CheckResult(name, "fail", out.strip()[-600:])


def gate_strength(gate_cfg: dict) -> dict:
    """Report the gate's REAL correctness floor for this environment — accuracy is only as strong
    as the checks that actually run. A missing tool (mypy/bandit) or a disabled check silently
    lowers the floor; surfacing it keeps savings from being quoted without the quality caveat."""
    active = ["syntax"]      # ast parse: always on, no external tool
    skipped: list[str] = []
    for name, key, tool in (("types", "run_types", "mypy"),
                            ("lint", "run_lint", "ruff"),
                            ("security", "run_security", "bandit")):
        if gate_cfg.get(key, True) and shutil.which(tool) is not None:
            active.append(name)
        else:
            reason = "disabled" if not gate_cfg.get(key, True) else "not installed"
            skipped.append(f"{name} ({reason})")
    # "types"/"security" missing materially weakens the floor; lint-only is the weakest useful gate.
    weakened = any(s.startswith(("types", "security")) for s in skipped)
    floor = "full" if not skipped else ("reduced" if not weakened else "weak")
    return {"active": active, "skipped": skipped, "floor": floor}


def run_gate(root: Path, changed_files: list[str], gate_cfg: dict) -> GateReport:
    report = GateReport()
    report.checks.append(_syntax_check(root, changed_files))

    if gate_cfg.get("run_types", True):
        report.checks.append(_tool_check(
            "types", "mypy", ["--ignore-missing-imports", "--no-error-summary"], root, changed_files))
    if gate_cfg.get("run_lint", True):
        report.checks.append(_tool_check("lint", "ruff", ["check"], root, changed_files))
    if gate_cfg.get("run_security", True):
        report.checks.append(_tool_check("security", "bandit", ["-q", "-ll"], root, changed_files))
    if gate_cfg.get("run_tests", False):
        if shutil.which("pytest") is None:
            report.checks.append(CheckResult("tests", "skipped", "pytest not installed"))
        else:
            code, out = _run(gate_cfg.get("test_command", "pytest -q").split(), root, timeout=600)
            report.checks.append(
                CheckResult("tests", "pass" if code == 0 else "fail", out.strip()[-600:]))
    return report
