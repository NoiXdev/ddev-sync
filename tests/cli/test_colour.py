import io

import pytest

from ddev_sync.cli.colour import Colour, colour_enabled, paint, paint_padded


class _Stream(io.StringIO):
    """A stream whose `isatty` answer the test chooses."""

    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_paint_wraps_the_text_in_the_colour_and_a_reset_when_enabled() -> None:
    assert paint("OK", Colour.GREEN, enabled=True) == "\x1b[32mOK\x1b[0m"
    assert paint("FAIL", Colour.RED, enabled=True) == "\x1b[31mFAIL\x1b[0m"


def test_paint_returns_the_text_untouched_when_disabled() -> None:
    assert paint("OK", Colour.GREEN, enabled=False) == "OK"


def test_paint_padded_pads_with_plain_spaces_outside_the_escape_sequence() -> None:
    # The padding must follow the reset: `f"{paint(...):<6}"` would count the
    # escape bytes towards the width and add no padding at all.
    assert paint_padded("OK", 6, Colour.GREEN, enabled=True) == "\x1b[32mOK\x1b[0m    "
    assert paint_padded("FAIL", 6, Colour.RED, enabled=True) == "\x1b[31mFAIL\x1b[0m  "


def test_paint_padded_is_plain_left_justification_when_disabled() -> None:
    assert paint_padded("OK", 6, Colour.GREEN, enabled=False) == "OK    "


def test_colour_is_enabled_on_a_terminal_with_a_clean_environment() -> None:
    assert colour_enabled({}, _Stream(tty=True)) is True


def test_colour_is_disabled_when_the_stream_is_not_a_terminal() -> None:
    # A pipe, a file, `grep`, DDEV's own output capture.
    assert colour_enabled({}, _Stream(tty=False)) is False


def test_a_non_empty_no_color_disables_colour_even_on_a_terminal() -> None:
    assert colour_enabled({"NO_COLOR": "1"}, _Stream(tty=True)) is False


def test_an_empty_no_color_is_ignored() -> None:
    # no-color.org: the variable counts only when it is non-empty.
    assert colour_enabled({"NO_COLOR": ""}, _Stream(tty=True)) is True


def test_no_color_beats_force_color() -> None:
    environ = {"NO_COLOR": "1", "FORCE_COLOR": "1"}
    assert colour_enabled(environ, _Stream(tty=True)) is False


def test_force_color_enables_colour_without_a_terminal() -> None:
    assert colour_enabled({"FORCE_COLOR": "1"}, _Stream(tty=False)) is True


def test_force_color_zero_disables_colour_even_on_a_terminal() -> None:
    # Someone who writes `FORCE_COLOR=0` plainly means "no colour"; the chalk
    # convention is followed so that intent is not overridden.
    assert colour_enabled({"FORCE_COLOR": "0"}, _Stream(tty=True)) is False


def test_an_empty_force_color_is_ignored() -> None:
    assert colour_enabled({"FORCE_COLOR": ""}, _Stream(tty=False)) is False


def test_force_color_beats_term_dumb() -> None:
    environ = {"FORCE_COLOR": "1", "TERM": "dumb"}
    assert colour_enabled(environ, _Stream(tty=False)) is True


def test_term_dumb_disables_colour_on_a_terminal() -> None:
    assert colour_enabled({"TERM": "dumb"}, _Stream(tty=True)) is False


def test_another_term_value_does_not_disable_colour() -> None:
    assert colour_enabled({"TERM": "xterm-256color"}, _Stream(tty=True)) is True


@pytest.mark.parametrize("colour", list(Colour))
def test_every_colour_is_a_plain_ansi_foreground_code(colour: Colour) -> None:
    # Green and red only, no bold, no background, no 256-colour or truecolour:
    # the sixteen-colour foreground range every terminal has understood for
    # decades.
    assert colour.value in {"31", "32"}
