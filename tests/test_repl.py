import io

from rich.console import Console

from devagent import repl
from devagent.repl import Action, Flags, Session, dispatch, parse_line, toggle_flag


def _console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def _session(tmp_path):
    return Session(path=tmp_path)


# ── parse_line ──────────────────────────────────────────────────────────────

def test_plain_text_is_a_task():
    a = parse_line("add a /health endpoint")
    assert a.kind == "task" and a.arg == "add a /health endpoint"


def test_empty_line():
    assert parse_line("   ").kind == "empty"


def test_exit_aliases():
    for s in ("/exit", "/quit", "/q"):
        assert parse_line(s).kind == "exit"


def test_help_aliases():
    for s in ("/help", "/?", "/h"):
        assert parse_line(s).kind == "help"


def test_ask_keeps_question_text():
    a = parse_line("/ask what does the router do?")
    assert a.kind == "ask" and a.arg == "what does the router do?"


def test_repo_keeps_raw_windows_path():
    a = parse_line(r"/repo C:\Users\me\proj")
    assert a.kind == "repo" and a.arg == r"C:\Users\me\proj"   # backslashes preserved


def test_toggles():
    for name in ("dry", "auto", "review", "test", "parallel"):
        a = parse_line(f"/{name}")
        assert a.kind == "toggle" and a.arg == name


def test_role_command_with_model():
    a = parse_line("/executor claude-cli")
    assert a.kind == "role" and a.arg == "executor" and a.args == ["claude-cli"]


def test_role_command_reset_has_no_args():
    a = parse_line("/planner")
    assert a.kind == "role" and a.arg == "planner" and a.args == []


def test_unknown_slash_is_passthrough_tokenized():
    a = parse_line('/epic plan "build checkout"')
    assert a.kind == "passthrough"
    assert a.args == ["epic", "plan", "build checkout"]   # shlex unquotes


def test_passthrough_unbalanced_quotes_falls_back():
    a = parse_line('/epic plan "oops')
    assert a.kind == "passthrough" and a.args[:2] == ["epic", "plan"]


# ── toggle_flag ─────────────────────────────────────────────────────────────

def test_toggle_flips_and_returns_value():
    f = Flags()
    assert toggle_flag(f, "dry") is True and f.dry is True
    assert toggle_flag(f, "dry") is False and f.dry is False


def test_flags_label():
    f = Flags(dry=True, review=True)
    assert f.label() == "[dry,review] "
    assert Flags().label() == ""


# ── dispatch (no-model paths) ─────────────────────────────────────────────────

def test_dispatch_exit_returns_false(tmp_path):
    assert dispatch(Action("exit"), _session(tmp_path), _console()) is False


def test_dispatch_help_renders(tmp_path):
    c = _console()
    assert dispatch(Action("help"), _session(tmp_path), c) is True
    assert "coding task" in c.file.getvalue()


def test_dispatch_toggle_mutates_session(tmp_path):
    s = _session(tmp_path)
    dispatch(Action("toggle", arg="parallel"), s, _console())
    assert s.flags.parallel is True


def test_dispatch_role_sets_and_resets(tmp_path):
    s = _session(tmp_path)
    dispatch(Action("role", arg="executor", args=["claude-cli"]), s, _console())
    assert s.roles == {"executor": "claude-cli"}
    dispatch(Action("role", arg="executor", args=[]), s, _console())
    assert s.roles == {}


def test_dispatch_clear_empties_history(tmp_path):
    s = _session(tmp_path)
    s.history.append("old task")
    s.touched.append("a.py")
    dispatch(Action("clear"), s, _console())
    assert s.history == [] and s.touched == []


def test_dispatch_repo_switches_to_valid_dir(tmp_path):
    s = _session(tmp_path)
    sub = tmp_path / "other"
    sub.mkdir()
    dispatch(Action("repo", arg=str(sub)), s, _console())
    assert s.path == sub.resolve()


def test_dispatch_repo_rejects_bad_dir(tmp_path):
    s = _session(tmp_path)
    c = _console()
    dispatch(Action("repo", arg=str(tmp_path / "nope")), s, c)
    assert s.path == tmp_path
    assert "not a directory" in c.file.getvalue()


def test_run_repl_eof_exits_immediately(tmp_path, monkeypatch):
    """A reader that immediately signals EOF should make the loop exit cleanly."""
    class EOFReader:
        def read(self, _):
            raise EOFError

    monkeypatch.setattr(repl, "_Reader", lambda: EOFReader())
    repl.run_repl(str(tmp_path))   # must return without hanging or raising
