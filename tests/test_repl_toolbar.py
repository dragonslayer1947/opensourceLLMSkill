"""The persistent 'devagent active' bottom toolbar in the interactive shell."""
from pathlib import Path
from types import SimpleNamespace

from devagent.repl import _bottom_toolbar


def _fake_session(label="", name="myrepo"):
    return SimpleNamespace(flags=SimpleNamespace(label=lambda: label),
                           path=Path(f"/work/{name}"))


def test_toolbar_shows_blue_active_indicator():
    tb = _bottom_toolbar(_fake_session())
    assert tb is not None
    assert "● devagent active" in tb.value
    assert "ansiblue" in tb.value          # rendered in blue
    assert "myrepo" in tb.value            # shows the current repo
    assert "default" in tb.value           # no flags -> 'default'


def test_toolbar_reflects_active_flags():
    tb = _bottom_toolbar(_fake_session(label="[dry] "))
    assert "[dry]" in tb.value


def test_toolbar_never_raises():
    # A broken session must not break the prompt — returns None instead of throwing.
    bad = SimpleNamespace()  # missing .flags / .path
    assert _bottom_toolbar(bad) is None
