"""Terminal colour: when to use it, and how to apply it without breaking layout.

There are two separate jobs here, kept apart on purpose.

`colour_enabled` answers "should this output be coloured?" from an environment
and a stream passed in as arguments, so the answer is testable without
touching `os.environ` or `sys.stdout`. A caller asks it once, at the edge of
the program, and hands the boolean to the pure renderers - a renderer that
sniffed the environment itself would no longer be snapshot-testable.

`paint` and `paint_padded` apply the colour. Only plain ANSI foreground codes
are supported (green, red and yellow): every terminal has understood them for
decades, so there is nothing to detect beyond "is colour wanted at all".
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import TextIO

_ESCAPE = "\x1b["
_RESET = f"{_ESCAPE}0m"


class Colour(StrEnum):
    """The SGR foreground codes in use; the value is the code itself."""

    RED = "31"
    GREEN = "32"
    YELLOW = "33"


def colour_enabled(environ: Mapping[str, str], stream: TextIO) -> bool:
    """Whether output written to `stream` should carry colour.

    Precedence, strongest first:

    1. `NO_COLOR` set to a non-empty value turns colour off, whatever else is
       set (no-color.org). It is the user's explicit refusal and must beat
       every other signal, including `FORCE_COLOR`, so that a user who exports
       it once in their shell profile never sees colour from any tool.
    2. `FORCE_COLOR` set to a non-empty value decides on its own, ahead of
       `TERM` and the terminal check. Any value but `0` turns colour on
       without a terminal, which is what CI logs and `less -R` need. It beats
       `TERM=dumb` because it is an explicit request, whereas `TERM` is only
       a guess about the terminal's abilities. `0` turns colour off, even on
       a terminal: this follows the chalk convention rather than CPython's,
       where any value forces colour on. Someone who types `FORCE_COLOR=0`
       plainly wants no colour, and respecting that intent was judged worth
       more than matching CPython. It is a deliberate choice, so it need not
       be weighed again.
    3. `TERM=dumb` turns colour off: such a terminal cannot interpret escape
       sequences and would print them as literal characters.
    4. Otherwise colour is on exactly when `stream` is a terminal, so a pipe,
       a file, `grep` and DDEV's output capture stay plain.

    An empty `NO_COLOR` or `FORCE_COLOR` counts as unset in both cases, as
    the no-color.org convention asks, because `export NO_COLOR=` is how a
    user clears the variable.
    """
    if environ.get("NO_COLOR"):
        return False
    force = environ.get("FORCE_COLOR")
    if force:
        return force != "0"
    if environ.get("TERM") == "dumb":
        return False
    return stream.isatty()


def paint(text: str, colour: Colour, *, enabled: bool) -> str:
    """`text` in `colour`, or unchanged when colour is not `enabled`."""
    if not enabled:
        return text
    return f"{_ESCAPE}{colour.value}m{text}{_RESET}"


def paint_padded(text: str, width: int, colour: Colour, *, enabled: bool) -> str:
    """`text` left-justified to `width`, with only the text itself coloured.

    The padding is applied to the plain text and the escape sequences are put
    around the text afterwards. Escape sequences are characters, so
    `f"{paint(...):<6}"` would count them towards the width - the padded cell
    would then be too short, or not padded at all, and every column after it
    would move. The padding stays outside the colour so that nothing but the
    word itself is tinted.
    """
    padding = " " * max(width - len(text), 0)
    return f"{paint(text, colour, enabled=enabled)}{padding}"
