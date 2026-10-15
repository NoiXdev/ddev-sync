"""Turn executor progress into lines of text.

The reporter emits plain strings through an injected `write` callback. Whether
those strings carry colour is decided by the caller and passed in as a
boolean, never sniffed from the environment here; keeping the decision out of
this module is what makes the output assertable in a test without a terminal.
"""

from collections.abc import Callable

from ddev_sync.cli.colour import Colour, paint_padded
from ddev_sync.cli.scrub import scrub
from ddev_sync.engine.executor import StepOutcome
from ddev_sync.engine.plan import PlannedStep
from ddev_sync.engine.steps import STEP_DESCRIPTIONS

# The marker gets its own column rather than carrying padding inside each
# string. `"FAIL"` used to fill a four-character field exactly, so a failure
# printed `FAILdb.import` with no separator; and a coloured marker cannot be
# padded by the f-string at all, because the escape sequences count towards
# the width (see `colour.paint_padded`).
_MARK_WIDTH = 6
_NAME_WIDTH = 14
_RUNNING = ".."
_MARKS = {"ok": "OK", "failed": "FAIL", "skipped": "--"}
# Failure text sits under the step name, one column in from the marker.
# A progress line sits in the same column and for the same reason: both
# belong to the step above them rather than standing on their own.
_UNDER_NAME = " " * (2 + _MARK_WIDTH)
_MARK_COLOURS = {"ok": Colour.GREEN, "failed": Colour.RED}


class ConsoleReporter:
    """Writes one line per step, plus notices and warnings."""

    def __init__(
        self,
        write: Callable[[str], None],
        *,
        colour: bool = False,
        verbose: int = 0,
        secrets: set[str] | None = None,
    ) -> None:
        self._emit = write
        self._colour = colour
        self._verbose = verbose
        # Held, not copied: the passwords are registered on the execution
        # context during the run, by
        # `operations/database.py::read_remote_credentials`, which is long
        # after this reporter is built. The default is an empty set so that a
        # caller with nothing to hide - `doctor`, the tests - needs no
        # argument, and so that nothing has to decide whether to scrub.
        self._secrets = secrets if secrets is not None else set()

    def _write(self, text: str) -> None:
        """Write one line, with every registered secret taken out of it.

        Here rather than at the call sites: a reporter line is built from a
        step's own error text, and which of those might one day interpolate a
        password is not a thing to keep track of. Two error paths are already
        scrubbed by hand in `operations/`, before the text becomes an
        exception; this is the gate they both pass through anyway.
        """
        self._emit(scrub(text, self._secrets))

    def starting(self, step: PlannedStep) -> None:
        """Announce a step that is about to run, and say what it will do.

        From `-v` up every step shows its `detail`: its plan detail, as the dry
        run prints it, which is the command line only for some kinds. Below
        that a step shows its kind's fixed description - except a hook, which
        keeps its parent's `kind` but is the user's own command, so the
        parent's description would be false for it; its `detail` is the command
        itself at every level.
        """
        show_detail = step.is_hook or self._verbose >= 1
        what = step.detail if show_detail else STEP_DESCRIPTIONS[step.kind]
        self._write(f"  {_RUNNING:<{_MARK_WIDTH}}{step.kind.value:<{_NAME_WIDTH}}{what}")

    def finished(self, outcome: StepOutcome) -> None:
        """Report how a step ended.

        No description here: `starting` printed it one line above, and
        repeating it is the noise this pass exists to remove.
        """
        line = f"  {self._mark(outcome.status)}{outcome.step.kind.value:<{_NAME_WIDTH}}"
        if outcome.status == "skipped":
            line += "skipped"
        if self._verbose >= 3 and outcome.duration is not None:
            line = f"{line.rstrip()}  ({outcome.duration:.1f}s)"
        self._write(line.rstrip())
        if outcome.error is not None:
            for text in str(outcome.error).splitlines():
                self._write(f"{_UNDER_NAME}{text}")

    def progress(self, text: str) -> None:
        """Say what the running step is waiting for.

        Written before the wait, not after, so the line a reader is looking
        at is the one still outstanding; the next line appearing is how they
        learn this one came back. Unmarked on purpose - a marker column would
        claim it is a step, and the step is the line above.
        """
        self._write(f"{_UNDER_NAME}{text}")

    def notice(self, text: str) -> None:
        """Report something the user should read."""
        self._write(f"  {self._bang()}{text}")

    def warn_unsanitized(self, stage: str) -> None:
        """Warn that data from `stage` is on this machine unsanitized."""
        self._write("")
        self._write(f"  {self._bang()}data from {stage} is on your machine and was not sanitized")

    def _mark(self, status: str) -> str:
        text = _MARKS.get(status, "?")
        colour = _MARK_COLOURS.get(status)
        if colour is None:
            return f"{text:<{_MARK_WIDTH}}"
        return paint_padded(text, _MARK_WIDTH, colour, enabled=self._colour)

    def _bang(self) -> str:
        return paint_padded("!", _MARK_WIDTH, Colour.YELLOW, enabled=self._colour)
