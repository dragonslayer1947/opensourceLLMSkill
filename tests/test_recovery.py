"""Execution-loop reliability: the local retrieval feedback loop (#7) and the green-tree
invariant (#6). A context-shaped failure must be recovered LOCALLY (no frontier escalation); an
unrecoverable integration failure must roll the subtask back rather than leave a broken tree."""
from rich.console import Console

from devagent import pipeline
from devagent.context.index import build_index
from devagent.decompose.planner import Plan, Subtask
from devagent.models.base import CompletionResult, ModelClient
from devagent.models.registry import Registry
from devagent.models.router import Router


class SeqClient(ModelClient):
    """Returns canned edit-block text per call (last response repeats)."""
    def __init__(self, name, responses):
        super().__init__(name, "m", "local", {})
        self.responses = responses
        self.calls = 0

    def complete(self, system, user, **kw):
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return CompletionResult(text=self.responses[i], tokens_in=1, tokens_out=1,
                                model_name=self.name)


def _create(path, body):
    return f"{path}\n<<<<<<< SEARCH\n=======\n{body}\n>>>>>>> REPLACE\n"


def _store_repo(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "store.py").write_text("class S:\n    pass\n\nstore = S()\n", encoding="utf-8")


def _run(tmp_path, make_config, responses):
    cfg = make_config()
    client = SeqClient("local", responses)
    reg = Registry(cfg)
    reg._cache["local"] = client          # executor role -> "local"
    router = Router(reg)
    idx = build_index(tmp_path)
    result = pipeline.RunResult(session_id="t", plan=Plan([], False, None))
    st = Subtask(id="s1", description="create app/main.py using the store singleton",
                 target_files=["app/main.py"])
    outcome = pipeline._run_subtask(st, tmp_path, cfg, router, idx, Console(), result,
                                    dry_run=False, use_spinner=False)
    return outcome, client, result


def test_context_failure_recovers_locally(tmp_path, make_config):
    _store_repo(tmp_path)
    bad = _create("app/main.py", "from app.store import get_store\n\nx = get_store\n")  # drift
    good = _create("app/main.py", "from app.store import store\n\nx = store\n")
    outcome, client, result = _run(tmp_path, make_config, [bad, good])

    assert outcome.status == "applied"
    assert client.calls == 2  # first attempt failed integration -> ONE local widen-retry fixed it
    assert "from app.store import store" in (tmp_path / "app" / "main.py").read_text()
    # recovery stayed local — no frontier escalation was spent
    assert result.calls and all(c.tier == "local" for c in result.calls)
    assert not outcome.escalated


def test_unrecoverable_integration_rolls_back(tmp_path, make_config):
    _store_repo(tmp_path)
    bad = _create("app/main.py", "from app.store import nope\n\nx = nope\n")  # always wrong
    outcome, client, result = _run(tmp_path, make_config, [bad])

    assert outcome.status == "integration_failed"
    # green tree invariant: the half-built file was rolled back, not left behind
    assert not (tmp_path / "app" / "main.py").exists()
