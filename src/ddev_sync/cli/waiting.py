"""One line that says what the engine is waiting on, animated while it waits.

Before the first question a sync can sit silent for half a minute: a
`deployer:` target runs `dep config` in the web container, and Deployer
evaluates every host's configuration to print it, some of it over SSH. Nothing
the engine does can make that faster, but a terminal that shows nothing for
that long looks hung, so this says what is happening and that it still is.

It writes to stderr and only to a terminal: the line is rewritten in place
with `\\r` and erased when the wait ends, which a pipe, a CI log or DDEV's
output capture would keep as a pile of fragments. There the run stays exactly
as quiet as it was. Nothing is printed while a question is on screen, because
callers hold it around work, never around a prompt.

Waits nest. An outer "reading the project" is replaced by an inner "asking
Deployer" for as long as that lasts, and comes back after it, so a deep call
can say what it is doing without knowing who is already showing something.
"""

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import TextIO

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
INTERVAL_SECONDS = 0.1
# Below this a wait is short enough that counting it would only flicker.
SHOW_SECONDS_FROM = 3
_ERASE_LINE = "\r\x1b[2K"

# What a caller that may or may not be shown something takes: a function from
# the sentence to a context held for as long as the wait lasts.
Wait = Callable[[str], AbstractContextManager[None]]


def not_shown(_text: str) -> AbstractContextManager[None]:
    """The `Wait` for a caller with nothing to show it on."""
    return nullcontext()


def waiting_enabled(environ: Mapping[str, str], stream: TextIO) -> bool:
    """Whether `stream` can take a line that is rewritten in place.

    Only a terminal can, and not one that declares itself `dumb`: it would
    print the erase sequence as text. `NO_COLOR` is not consulted - it asks
    for no colour, and this has none.
    """
    return environ.get("TERM") != "dumb" and stream.isatty()


def render(frame: int, text: str, seconds: float) -> str:
    """The line for one frame, without the erase that precedes it."""
    line = f"{FRAMES[frame % len(FRAMES)]} {text}"
    if seconds >= SHOW_SECONDS_FROM:
        line += f" · {int(seconds)}s"
    return line


class Waiting:
    """Shows the innermost wait on `stream` until every wait has ended."""

    def __init__(
        self,
        stream: TextIO,
        *,
        enabled: bool,
        interval: float = INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._enabled = enabled
        self._interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        # The texts being waited on, innermost last, each with when it began.
        self._waits: list[tuple[str, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __call__(self, text: str) -> AbstractContextManager[None]:
        return self._wait(text) if self._enabled else nullcontext()

    @contextmanager
    def _wait(self, text: str) -> Iterator[None]:
        with self._lock:
            self._waits.append((text, self._clock()))
            first = len(self._waits) == 1
        if first:
            self._stop.clear()
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        try:
            yield
        finally:
            with self._lock:
                self._waits.pop()
                last = not self._waits
                if last:
                    # Under the lock, so the thread never finds the list
                    # empty without also finding itself told to stop.
                    self._stop.set()
            if last:
                assert self._thread is not None
                self._thread.join()
                self._thread = None
                self._stream.write(_ERASE_LINE)
                self._stream.flush()

    def _animate(self) -> None:
        frame = 0
        while True:
            with self._lock:
                # A slice, not `[-1]`: the last wait may have ended between
                # this thread's sleep and the lock, and then there is nothing
                # to draw - `stop` is already set and the loop ends below.
                for text, began in self._waits[-1:]:
                    self._stream.write(_ERASE_LINE + render(frame, text, self._clock() - began))
                    self._stream.flush()
            frame += 1
            if self._stop.wait(self._interval):
                return
