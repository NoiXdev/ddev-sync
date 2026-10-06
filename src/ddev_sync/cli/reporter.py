"""Turn executor progress into lines of text.

The reporter emits plain strings through an injected `write` callback. Whether
those strings carry colour is decided by the caller and passed in as a
boolean, never sniffed from the environment here; keeping the decision out of
this module is what makes the output assertable in a test without a terminal.
"""

from collections.abc import Callable

from ddev_sync.cli.colour import Colour, paint_padded
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
_MARK_COLOURS = {"ok": Colour.GREEN, "failed": Colour.RED}


class ConsoleReporter:
    """Writes one line per step, plus notices and warnings."""

    def __init__(self, write: Callable[[str], None], *, colour: bool = False) -> None:
        self._write = write
        self._colour = colour

    def starting(self, step: PlannedStep) -> None:
        """Announce a step that is about to run, and say what it will do."""
        self._write(
            f"  {_RUNNING:<{_MARK_WIDTH}}{step.kind.value:<{_NAME_WIDTH}}"
            f"{STEP_DESCRIPTIONS[step.kind]}"
        )

    def finished(self, outcome: StepOutcome) -> None:
        """Report how a step ended.

        No description here: `starting` printed it one line above, and
        repeating it is the noise this pass exists to remove.
        """
        line = f"  {self._mark(outcome.status)}{outcome.step.kind.value:<{_NAME_WIDTH}}"
        if outcome.status == "skipped":
            line += "skipped"
        self._write(line.rstrip())
        if outcome.error is not None:
            for text in str(outcome.error).splitlines():
                self._write(f"      {text}")

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
