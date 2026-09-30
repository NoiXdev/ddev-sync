"""Walk a Plan and perform each step.

The executor knows nothing about what any step does. It knows about ordering,
reporting, and what must still happen after something fails - which is the
entire reason it is a separate object from the operations.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ddev_sync.engine.plan import Plan, PlannedStep
from ddev_sync.engine.registry import OPERATIONS
from ddev_sync.engine.steps import StepKind
from ddev_sync.errors import DdevSyncError
from ddev_sync.operations.base import ExecutionContext, Operation
from ddev_sync.operations.exec import run_command_entry


@dataclass(frozen=True)
class StepOutcome:
    """What happened to one planned step."""

    step: PlannedStep
    status: str
    error: Exception | None = None


@dataclass(frozen=True)
class RunReport:
    """The result of a whole run."""

    outcomes: tuple[StepOutcome, ...]
    failed: bool


class Reporter(Protocol):
    """Receives progress as the executor works."""

    def starting(self, step: PlannedStep) -> None:
        """A step is about to run."""

    def finished(self, outcome: StepOutcome) -> None:
        """A step finished, succeeded or not."""

    def notice(self, text: str) -> None:
        """Something the user should read before, during or after the run."""


class Executor:
    """Runs the steps of a plan in order."""

    def __init__(
        self,
        reporter: Reporter,
        operations: Mapping[StepKind, Operation] | None = None,
    ) -> None:
        self.reporter = reporter
        self._operations = OPERATIONS if operations is None else operations

    def run(self, plan: Plan, context: ExecutionContext) -> RunReport:
        """Execute every step, stopping the rest after the first failure.

        `post_sync` hooks run in a `finally`-like step afterwards regardless
        of whether the run succeeded: cleanup that only happens on the happy
        path is not cleanup. A failing `post_sync` hook is reported as a
        notice and does not flip a successful run's verdict to failed.
        """
        for text in plan.notices:
            self.reporter.notice(text)

        outcomes: list[StepOutcome] = []
        stopped = False
        for step in plan.steps:
            if stopped:
                outcomes.append(StepOutcome(step=step, status="skipped"))
                continue
            outcome = self._run_step(step, context)
            outcomes.append(outcome)
            if outcome.status == "failed":
                stopped = True

        failed = any(outcome.status == "failed" for outcome in outcomes)
        self._run_post_sync(context)
        return RunReport(outcomes=tuple(outcomes), failed=failed)

    def _run_step(self, step: PlannedStep, context: ExecutionContext) -> StepOutcome:
        self.reporter.starting(step)
        operation = self._operations.get(step.kind)
        if operation is None:
            outcome = StepOutcome(
                step=step,
                status="failed",
                error=DdevSyncError(f"no operation registered for {step.kind.value}"),
            )
            self.reporter.finished(outcome)
            return outcome
        try:
            operation.execute(context, step.params)
        except DdevSyncError as error:
            outcome = StepOutcome(step=step, status="failed", error=error)
        else:
            outcome = StepOutcome(step=step, status="ok")
        self.reporter.finished(outcome)
        return outcome

    def _run_post_sync(self, context: ExecutionContext) -> None:
        """Run `hooks.post_sync` commands whether or not the run succeeded.

        A failure here is reported as a notice, not raised: a cleanup hook
        going wrong must never overwrite the verdict of the run it is
        cleaning up after.
        """
        for entry in _post_sync_entries(context):
            try:
                run_command_entry(context, entry)
            except DdevSyncError as error:
                self.reporter.notice(f"post_sync hook failed: {error}")


def _post_sync_entries(context: ExecutionContext) -> list[Mapping[str, object]]:
    node = context.merged.data.get("hooks", {})
    if not isinstance(node, dict):
        return []
    block = node.get("post_sync", {})
    if not isinstance(block, dict):
        return []
    commands = block.get("commands", [])
    if not isinstance(commands, list):
        return []
    return [entry for entry in commands if isinstance(entry, dict)]
