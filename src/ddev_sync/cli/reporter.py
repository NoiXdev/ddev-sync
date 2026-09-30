"""Turn executor progress into lines of text.

The reporter emits plain strings through an injected `write` callback. Colour
and cursor tricks are the caller's business; keeping them out of here is what
makes the output assertable in a test without a terminal.
"""

from collections.abc import Callable

from ddev_sync.engine.executor import StepOutcome
from ddev_sync.engine.plan import PlannedStep

_MARKS = {"ok": "OK  ", "failed": "FAIL", "skipped": "--  "}


class ConsoleReporter:
    """Writes one line per step, plus notices and warnings."""

    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write

    def starting(self, step: PlannedStep) -> None:
        """Announce a step that is about to run."""
        self._write(f"  ..  {step.kind.value:<14}{step.detail}")

    def finished(self, outcome: StepOutcome) -> None:
        """Report how a step ended."""
        mark = _MARKS.get(outcome.status, "?   ")
        if outcome.status == "skipped":
            self._write(f"  {mark}{outcome.step.kind.value:<14}skipped")
            return
        self._write(f"  {mark}{outcome.step.kind.value:<14}{outcome.step.detail}")
        if outcome.error is not None:
            for line in str(outcome.error).splitlines():
                self._write(f"      {line}")

    def notice(self, text: str) -> None:
        """Report something the user should read."""
        self._write(f"  !   {text}")

    def warn_unsanitized(self, stage: str) -> None:
        """Warn that data from `stage` is on this machine unsanitized."""
        self._write("")
        self._write(f"  !   data from {stage} is on your machine and was not sanitized")
