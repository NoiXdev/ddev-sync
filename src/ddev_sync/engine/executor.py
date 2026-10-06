"""Walk a Plan and perform each step.

The executor knows nothing about what any step does. It knows about ordering,
reporting, and what must still happen after something fails - which is the
entire reason it is a separate object from the operations.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ddev_sync.engine.plan import Plan, PlannedStep, command_entries_at, resolve_command_path
from ddev_sync.engine.registry import OPERATIONS
from ddev_sync.engine.steps import StepKind
from ddev_sync.errors import DdevSyncError, SyncInterrupted
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

        The one exception is Ctrl-C. An interrupt raises `SyncInterrupted`
        straight out of the step loop, so `post_sync` hooks do not run: the
        user asked everything to stop, and running further commands after
        that would be the tool deciding it knows better. The error says so
        when hooks were configured, rather than leaving that to be inferred.
        """
        for text in plan.notices:
            self.reporter.notice(text)

        outcomes: list[StepOutcome] = []
        stopped = False
        try:
            for step in plan.steps:
                if stopped:
                    outcomes.append(StepOutcome(step=step, status="skipped"))
                    continue
                outcome = self._run_step(step, context)
                outcomes.append(outcome)
                if outcome.status == "failed":
                    stopped = True
        except KeyboardInterrupt as interrupt:
            # `_run_step` catches only `DdevSyncError`, so an interrupt raised
            # inside a step lands here with `outcomes` still in scope - which
            # is all the progress tracking the message needs. The step that
            # was running has no outcome yet, so it is the next one in the
            # plan; there is none when the interrupt landed after the last
            # step had already been recorded.
            running = plan.steps[len(outcomes)] if len(outcomes) < len(plan.steps) else None
            raise SyncInterrupted(
                _interrupted_message(
                    outcomes,
                    running=running,
                    post_sync_skipped=bool(_post_sync_entries(context)),
                )
            ) from interrupt

        failed = any(outcome.status == "failed" for outcome in outcomes)
        self._run_post_sync(context)
        return RunReport(outcomes=tuple(outcomes), failed=failed)

    def _run_step(self, step: PlannedStep, context: ExecutionContext) -> StepOutcome:
        self.reporter.starting(step)
        try:
            self._dispatch(step, context)
        except DdevSyncError as error:
            outcome = StepOutcome(step=step, status="failed", error=error)
        else:
            outcome = StepOutcome(step=step, status="ok")
        self.reporter.finished(outcome)
        return outcome

    def _dispatch(self, step: PlannedStep, context: ExecutionContext) -> None:
        """Run one step's work, raising `DdevSyncError` on failure.

        A hook step (`is_hook`) carries a command entry - `{run: ...}` or
        `{operation: ...}`, the same shape `setup.commands` uses - not a copy
        of its parent step's params in the parent operation's expected shape.
        Looking it up in `OPERATIONS` by `step.kind` (every hook's `kind` is
        its parent's, by design - see `PlannedStep`) dispatched every hook to
        its *parent's* operation instead of running the hook's own command:
        a `post_db_import` hook silently re-ran the whole database import,
        ignoring its own `run:` entirely. `run_command_entry` is the same
        dispatch `setup.commands` already goes through, so a hook gets
        `allow_failure` and `workdir` handled identically to a setup command,
        which is the entire point of sharing the path.
        """
        if step.is_hook:
            run_command_entry(context, step.params)
            return
        operation = self._operations.get(step.kind)
        if operation is None:
            raise DdevSyncError(f"no operation registered for {step.kind.value}")
        operation.execute(context, step.params)

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


def _post_sync_entries(context: ExecutionContext) -> list[dict[str, Any]]:
    """Resolve `hooks.post_sync` through the same shape logic the plan
    builder's hooks and `setup` commands use (`engine.plan.resolve_command_path`
    / `command_entries_at`), rather than a hand-rolled check that only
    accepted a `{commands: [...]}` mapping. A bare list and a bare string are
    both valid everywhere else this shape appears (spec §4.5); requiring the
    mapping form here and nowhere else meant a cleanup hook written either of
    those two ways silently never ran.
    """
    path = resolve_command_path(context.merged.data, ("hooks", "post_sync"))
    return [
        entry
        for entry, _source in command_entries_at(
            context.merged.data, context.merged.list_sources, path
        )
    ]


def _describe(step: PlannedStep) -> str:
    """A step as a person would name it; a hook says it is a hook."""
    return f"a {step.kind.value} hook" if step.is_hook else step.kind.value


def _interrupted_message(
    outcomes: Sequence[StepOutcome],
    *,
    running: PlannedStep | None,
    post_sync_skipped: bool,
) -> str:
    """What an interrupt left behind, in the order someone needs to read it.

    The step that was running and the last one that finished come first,
    because those are the questions. Then the state of the local database,
    but only as far as it is known: an interrupt after the import and before
    sanitizing leaves production data on the machine, an interrupt *during*
    the import leaves the database possibly part-way replaced, and one before
    either must say nothing about data at all - a warning that fires when it
    did not would teach people to ignore it.

    A hook carries its parent's `kind` (see `PlannedStep`), so `kind` alone
    cannot say whether the import itself ran: a finished `pre_db_import` hook
    would look like a finished import and raise that warning falsely, and a
    finished `pre_sanitize` hook would look like sanitizing and hide it when
    it is true. The database state is therefore read from non-hook steps
    only, while the sentence names a hook as a hook. An interrupted
    `post_db_import` hook still warns, correctly, because the import step
    before it did finish, whereas an interrupted `pre_db_import` hook says
    nothing about data because the import has not started.
    """
    finished = [outcome.step for outcome in outcomes if outcome.status == "ok"]
    after = f"after {_describe(finished[-1])}" if finished else "before the first step finished"
    message = (
        f"Interrupted {after}."
        if running is None
        else f"Interrupted during {_describe(running)}, {after}."
    )

    kinds = {step.kind for step in finished if not step.is_hook}
    if running is not None and running.kind is StepKind.DB_IMPORT and not running.is_hook:
        # "May", because an interrupt cannot say how far the import got.
        message += (
            " The local database may be partly replaced with production data and is not"
            " sanitized - re-run with --steps db,sanitize, or treat this copy as production"
            " data until you have."
        )
    elif StepKind.DB_IMPORT in kinds and StepKind.SANITIZE not in kinds:
        message += (
            " The local database has been replaced with production data and was not"
            " sanitized - re-run with --steps sanitize, or treat this copy as production"
            " data until you have."
        )
    if post_sync_skipped:
        message += " The post_sync hooks did not run."
    return message
