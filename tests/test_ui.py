import io
import time

from rich.console import Console

from devagent.ui import activity


def _nonterminal():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def _terminal():
    # force_terminal=True makes is_terminal True even when writing to a buffer
    return Console(file=io.StringIO(), force_terminal=True, width=100)


def test_nonterminal_prints_one_line_and_yields():
    c = _nonterminal()
    ran = False
    with activity(c, "Indexing the repo"):
        ran = True
    assert ran
    out = c.file.getvalue()
    assert "Indexing the repo" in out


def test_disabled_is_silent_passthrough():
    c = _nonterminal()
    with activity(c, "should not appear", enabled=False):
        pass
    assert c.file.getvalue() == ""


def test_terminal_spinner_runs_and_stops_cleanly():
    """On a 'terminal', the spinner thread starts and is torn down without raising."""
    c = _terminal()
    with activity(c, "Planning subtasks"):
        time.sleep(0.05)
    # message text should have been rendered at least once
    assert "Planning subtasks" in c.file.getvalue()


def test_propagates_exception_but_stops_spinner():
    c = _terminal()
    try:
        with activity(c, "work"):
            raise ValueError("boom")
    except ValueError:
        pass
    # a second activity must still work (the previous live display was stopped)
    with activity(c, "again"):
        pass
