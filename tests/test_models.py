import json

from devagent.models.base import CompletionResult, ModelClient
from devagent.models.cli_client import CLIClient, _load_json
from devagent.models.registry import Registry
from devagent.models.router import Router, RoutingError


class Fake(ModelClient):
    def __init__(self, name, *, fail=False):
        super().__init__(name, "m", "cloud", {})
        self.fail = fail
        self.calls = 0

    def complete(self, system, user, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return CompletionResult(text="ok", tokens_in=1, tokens_out=1, model_name=self.name)


def _router(make_config, **clients):
    cfg = make_config()
    reg = Registry(cfg)
    reg._cache.update(clients)
    return Router(reg), reg


def test_router_uses_primary(make_config):
    r, _ = _router(make_config, cloud=Fake("cloud"), local=Fake("local"))
    res = r.complete("planner", "s", "u")
    assert res.text == "ok" and r.last_model == "cloud"


def test_router_falls_through_to_backup(make_config):
    primary, backup = Fake("cloud", fail=True), Fake("local")
    r, _ = _router(make_config, cloud=primary, local=backup)
    res = r.complete("planner", "s", "u")
    assert res.model_name == "local" and r.last_model == "local"
    assert primary.calls == 2  # 1 try + 1 retry (retries=1) then fell through


def test_router_all_fail_raises(make_config):
    r, _ = _router(make_config, cloud=Fake("cloud", fail=True), local=Fake("local", fail=True))
    try:
        r.complete("planner", "s", "u")
        assert False, "expected RoutingError"
    except RoutingError:
        pass


def test_load_json_plain():
    assert _load_json('{"a": 1}') == {"a": 1}


def test_load_json_with_leading_noise():
    out = "hook warning line\n" + json.dumps({"result": "x", "is_error": False})
    assert _load_json(out)["result"] == "x"


def test_cli_parse_claude_success():
    c = CLIClient("cli", "sonnet", "cli", {}, command="claude", mode="claude", timeout_s=1)
    payload = json.dumps({
        "result": "hello", "is_error": False, "total_cost_usd": 0.01,
        "usage": {"input_tokens": 3, "output_tokens": 5},
    })
    text, cost, tin, tout = c._parse(payload, "sys", "user")
    assert text == "hello" and cost == 0.01 and tin == 3 and tout == 5


def test_cli_parse_claude_error_raises():
    c = CLIClient("cli", "sonnet", "cli", {}, command="claude", mode="claude", timeout_s=1)
    payload = json.dumps({"result": "Not logged in", "is_error": True, "usage": {}})
    try:
        c._parse(payload, "s", "u")
        assert False, "expected RuntimeError on is_error"
    except RuntimeError:
        pass


def test_claude_invocation_uses_stdin_and_system():
    c = CLIClient("cli", "opus", "cli", {}, command="claude", mode="claude", timeout_s=1)
    args, stdin_text, out_file = c._invocation("SYS", "USER")
    assert "-p" in args and "--output-format" in args and "--model" in args
    assert "opus" in args and "SYS" in args            # system passed as flag
    assert stdin_text == "USER" and out_file is None   # user prompt via stdin


def test_codex_invocation_reads_stdin_writes_file():
    c = CLIClient("cx", "gpt", "cli", {}, command="codex", mode="codex", timeout_s=1)
    args, stdin_text, out_file = c._invocation("SYS", "USER")
    assert args[1] == "exec" and "read-only" in args and args[-1] == "-"
    assert "SYS" in stdin_text and "USER" in stdin_text  # codex has no system flag → folded in
    assert out_file is not None and str(out_file).endswith(".out.txt")
