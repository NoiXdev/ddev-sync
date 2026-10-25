import io
import threading

import pytest

from ddev_sync.cli.waiting import (
    FRAMES,
    Waiting,
    not_shown,
    render,
    waiting_enabled,
)

ERASE = "\r\x1b[2K"


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Recording(io.StringIO):
    """A stream that says each time something was flushed to it."""

    def __init__(self) -> None:
        super().__init__()
        self.flushed = threading.Event()

    def flush(self) -> None:
        super().flush()
        self.flushed.set()


def test_a_frame_names_what_is_being_waited_on() -> None:
    assert render(0, "Asking Deployer", 0.0) == f"{FRAMES[0]} Asking Deployer"


def test_the_frames_cycle() -> None:
    assert render(len(FRAMES) + 1, "x", 0.0).startswith(FRAMES[1])


def test_a_short_wait_is_not_counted_and_a_long_one_is() -> None:
    # Counting the first seconds would only flicker; past them it is what
    # tells a slow wait from a hung one.
    assert "s" not in render(0, "x", 2.9).removeprefix(f"{FRAMES[0]} x")
    assert render(0, "x", 12.7) == f"{FRAMES[0]} x · 12s"


def test_only_a_terminal_that_can_rewrite_a_line_is_shown_anything() -> None:
    assert waiting_enabled({}, _Terminal()) is True
    assert waiting_enabled({"TERM": "dumb"}, _Terminal()) is False
    assert waiting_enabled({}, io.StringIO()) is False
    # No colour is involved, so asking for none changes nothing.
    assert waiting_enabled({"NO_COLOR": "1"}, _Terminal()) is True


def test_a_disabled_wait_writes_nothing_at_all() -> None:
    # A pipe, a CI log or DDEV's capture keep the run exactly as quiet as before.
    stream = io.StringIO()
    with Waiting(stream, enabled=False)("Asking Deployer"):
        pass
    assert stream.getvalue() == ""


def test_not_shown_is_a_wait_that_does_nothing() -> None:
    with not_shown("anything"):
        pass


def test_a_wait_is_drawn_and_erased_when_it_ends() -> None:
    stream = _Recording()
    waiting = Waiting(stream, enabled=True, interval=0.01)
    with waiting("Asking Deployer"):
        assert stream.flushed.wait(5)
    written = stream.getvalue()
    assert f"{ERASE}{FRAMES[0]} Asking Deployer" in written
    # The last thing written is the erase, so the next line starts clean.
    assert written.endswith(ERASE)


def test_an_inner_wait_replaces_the_outer_one_and_gives_it_back() -> None:
    stream = _Recording()
    waiting = Waiting(stream, enabled=True, interval=0.01)
    with waiting("Reading the project"):
        assert stream.flushed.wait(5)
        with waiting("Asking Deployer"):
            stream.flushed.clear()
            assert stream.flushed.wait(5)
            assert stream.getvalue().rsplit(ERASE, 1)[-1].endswith("Asking Deployer")
        stream.flushed.clear()
        assert stream.flushed.wait(5)
        assert stream.getvalue().rsplit(ERASE, 1)[-1].endswith("Reading the project")
    assert stream.getvalue().endswith(ERASE)


def test_a_wait_counts_from_when_it_began(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    stream = _Recording()
    waiting = Waiting(stream, enabled=True, interval=0.01, clock=lambda: now[0])
    with waiting("Asking Deployer"):
        now[0] = 112.0
        stream.flushed.clear()
        assert stream.flushed.wait(5)
        stream.flushed.clear()
        assert stream.flushed.wait(5)
    assert "Asking Deployer · 12s" in stream.getvalue()


def test_the_same_waiting_can_show_a_second_wait_after_the_first() -> None:
    stream = _Recording()
    waiting = Waiting(stream, enabled=True, interval=0.01)
    with waiting("first"):
        assert stream.flushed.wait(5)
    stream.flushed.clear()
    with waiting("second"):
        assert stream.flushed.wait(5)
    assert "second" in stream.getvalue()
    assert stream.getvalue().endswith(ERASE)


def test_an_error_inside_a_wait_still_erases_the_line() -> None:
    stream = _Recording()
    waiting = Waiting(stream, enabled=True, interval=0.01)
    with pytest.raises(RuntimeError), waiting("Asking Deployer"):
        raise RuntimeError("dep failed")
    assert stream.getvalue().endswith(ERASE)
