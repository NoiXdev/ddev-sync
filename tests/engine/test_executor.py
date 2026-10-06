from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.executor import Executor, RunReport, StepOutcome
from ddev_sync.engine.plan import Plan, PlannedStep
from ddev_sync.engine.steps import StepKind
from ddev_sync.errors import SyncInterrupted
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import confirm_destructive
from ddev_sync.transport.fake import FakeCommandRunner
from ddev_sync.transport.runner import CommandResult


class RecordingReporter:
    """A `Reporter` for tests.

    `finished_outcomes` is deliberately not called `finished`: an instance
    attribute of that name would shadow the `finished` *method* below it (the
    one the executor actually calls), since instance `__dict__` lookups win
    over class attributes. Naming them differently keeps both usable.
    """

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished_outcomes: list[StepOutcome] = []
        self.notices: list[str] = []

    def starting(self, step: PlannedStep) -> None:
        self.started.append(step.kind.value)

    def finished(self, outcome: StepOutcome) -> None:
        self.finished_outcomes.append(outcome)

    def notice(self, text: str) -> None:
        self.notices.append(text)


def context_at(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {})]),
        runner=FakeCommandRunner(),
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


def step(index: int, kind: StepKind, **extra: Any) -> PlannedStep:
    return PlannedStep(index=index, kind=kind, where="local", detail="x", source="test", **extra)


def plan_of(*steps: PlannedStep, notices: tuple[str, ...] = ()) -> Plan:
    return Plan(
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        profile_label="generic",
        project_name="acme",
        steps=steps,
        notices=notices,
    )


def executor_with(calls: list[str], failing: set[str] | None = None) -> Executor:
    broken = failing or set()

    class Recorder:
        def __init__(self, name: str) -> None:
            self._name = name

        def execute(self, _context: ExecutionContext, _params: Any) -> None:
            calls.append(self._name)
            if self._name in broken:
                raise OperationError(f"{self._name} exploded")

    return Executor(
        reporter=RecordingReporter(),
        operations={kind: Recorder(kind.value) for kind in StepKind},
    )


def test_every_step_runs_in_order(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = executor_with(calls)
    report = executor.run(
        plan_of(step(1, StepKind.PREFLIGHT), step(2, StepKind.DB_DUMP)), context_at(tmp_path)
    )
    assert calls == ["preflight", "db.dump"]
    assert report.failed is False
    assert [outcome.status for outcome in report.outcomes] == ["ok", "ok"]


def test_each_outcome_carries_how_long_its_step_took(tmp_path: Path) -> None:
    # Level three reports it, so it has to be measured even when nothing
    # displays it. `>= 0.0` rather than `> 0.0`: a step that does nothing can
    # finish inside the clock's resolution.
    executor = executor_with([])
    report = executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context_at(tmp_path))
    outcome = report.outcomes[0]
    assert outcome.duration is not None
    assert outcome.duration >= 0.0


def test_a_failed_step_carries_its_duration_too(tmp_path: Path) -> None:
    executor = executor_with([], failing={"preflight"})
    report = executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context_at(tmp_path))
    assert report.outcomes[0].status == "failed"
    assert report.outcomes[0].duration is not None


def test_the_reporter_sees_each_step_start_and_finish(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = executor_with(calls)
    executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context_at(tmp_path))
    reporter = executor.reporter
    assert isinstance(reporter, RecordingReporter)
    assert reporter.started == ["preflight"]
    assert len(reporter.finished_outcomes) == 1


def test_plan_notices_are_reported_before_the_first_step(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = executor_with(calls)
    executor.run(
        plan_of(step(1, StepKind.PREFLIGHT), notices=("using typo3@12",)), context_at(tmp_path)
    )
    reporter = executor.reporter
    assert isinstance(reporter, RecordingReporter)
    assert reporter.notices == ["using typo3@12"]


def test_a_failing_step_stops_the_remaining_steps(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = executor_with(calls, failing={"db.dump"})
    report = executor.run(
        plan_of(
            step(1, StepKind.PREFLIGHT), step(2, StepKind.DB_DUMP), step(3, StepKind.DB_IMPORT)
        ),
        context_at(tmp_path),
    )
    assert calls == ["preflight", "db.dump"]
    assert report.failed is True
    assert [outcome.status for outcome in report.outcomes] == ["ok", "failed", "skipped"]


def test_the_failure_is_carried_on_the_outcome(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = executor_with(calls, failing={"db.dump"})
    report = executor.run(plan_of(step(1, StepKind.DB_DUMP)), context_at(tmp_path))
    assert report.outcomes[0].error is not None
    assert "db.dump exploded" in str(report.outcomes[0].error)


def test_params_reach_the_operation(tmp_path: Path) -> None:
    seen: list[Any] = []

    class Capturing:
        def execute(self, _context: ExecutionContext, params: Any) -> None:
            seen.append(params)

    executor = Executor(reporter=RecordingReporter(), operations={StepKind.FILES_SYNC: Capturing()})
    executor.run(
        plan_of(step(1, StepKind.FILES_SYNC, params={"remote": "a/", "local": "b/"})),
        context_at(tmp_path),
    )
    assert seen == [{"remote": "a/", "local": "b/"}]


def test_a_step_kind_with_no_operation_is_reported_not_crashed(tmp_path: Path) -> None:
    executor = Executor(reporter=RecordingReporter(), operations={})
    report = executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context_at(tmp_path))
    assert report.failed is True
    assert "no operation" in str(report.outcomes[0].error)


def test_an_empty_plan_succeeds_trivially(tmp_path: Path) -> None:
    executor = executor_with([])
    report = executor.run(plan_of(), context_at(tmp_path))
    assert report == RunReport(outcomes=(), failed=False)


def context_with_post_sync(tmp_path: Path, command: str = "cleanup") -> ExecutionContext:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    return ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers(
            [LayerDoc("project", {"hooks": {"post_sync": {"commands": [{"run": command}]}}})]
        ),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


def test_post_sync_runs_after_a_successful_run(tmp_path: Path) -> None:
    context = context_with_post_sync(tmp_path)
    executor = executor_with([])
    executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context)
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    assert [call[-3:] for call in runner.calls] == [("sh", "-c", "cleanup")]


def test_post_sync_still_runs_after_a_failure(tmp_path: Path) -> None:
    context = context_with_post_sync(tmp_path)
    executor = executor_with([], failing={"preflight"})
    report = executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context)
    assert report.failed is True
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    assert [call[-3:] for call in runner.calls] == [("sh", "-c", "cleanup")]


def test_a_failing_post_sync_hook_is_reported_but_does_not_mask_the_run(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="cleanup broke")
    context = ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(name="p", host="h", user="u", port=22, path="/p/", stage=Stage.DEV),
        merged=merge_layers(
            [LayerDoc("project", {"hooks": {"post_sync": {"commands": [{"run": "x"}]}}})]
        ),
        runner=runner,
        downloads_dir=tmp_path / ".downloads",
        assume_yes=True,
    )
    executor = executor_with([])
    report = executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context)
    assert report.failed is False
    reporter = executor.reporter
    assert isinstance(reporter, RecordingReporter)
    assert any("post_sync hook failed" in text for text in reporter.notices)
    # Proves the hook really reached the scripted web-container command: an
    # unscripted prefix would also produce a "hook failed" notice, so the
    # message alone cannot tell the two apart.
    assert any("cleanup broke" in text for text in reporter.notices)


def test_no_post_sync_hooks_means_no_extra_commands(tmp_path: Path) -> None:
    context = context_at(tmp_path)
    executor = executor_with([])
    executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context)
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    assert runner.calls == []


@pytest.mark.parametrize(
    "hooks",
    ["not-a-mapping", {"post_sync": "not-a-mapping"}, {"post_sync": {"commands": "no"}}],
)
def test_a_malformed_post_sync_block_is_ignored(tmp_path: Path, hooks: Any) -> None:
    context = ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(name="p", host="h", user="u", port=22, path="/p/", stage=Stage.DEV),
        merged=merge_layers([LayerDoc("project", {"hooks": hooks})]),
        runner=FakeCommandRunner(),
        downloads_dir=tmp_path / ".downloads",
        assume_yes=True,
    )
    report = executor_with([]).run(plan_of(step(1, StepKind.PREFLIGHT)), context)
    assert report.failed is False


class _SpyOperation:
    """Not a Recorder: every executor test above replaces `OPERATIONS`
    wholesale with one `Recorder` per `StepKind`, so a hook that dispatches
    to its *parent's* operation lands on the same object a correctly
    dispatched hook would have reached - the fake cannot tell the two apart.
    This spy stands in for exactly one operation (`DB_IMPORT`'s), so a hook
    reaching it is distinguishable from the hook's own command reaching the
    real runner.
    """

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _context: ExecutionContext, _params: Any) -> None:
        self.calls += 1


def test_a_hook_step_runs_its_own_command_not_its_parent_operation(tmp_path: Path) -> None:
    """A `post_db_import`-shaped hook step carries `{"run": "..."}`, not a
    copy of the real db.import step's params - looking it up by `step.kind`
    in `OPERATIONS` (every hook's `kind` is its parent's, by design) used to
    dispatch it straight to `DbImportOperation`, which ignores `params`
    entirely and just re-ran the whole import. The hook's own command must
    reach `context.runner` instead, and the parent operation must not run a
    second time for the hook on top of its own real invocation.
    """
    spy = _SpyOperation()
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    context = ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {})]),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )
    executor = Executor(reporter=RecordingReporter(), operations={StepKind.DB_IMPORT: spy})
    plan = plan_of(
        step(1, StepKind.DB_IMPORT, is_hook=True, params={"run": "echo HOOK-RAN"}),
        step(2, StepKind.DB_IMPORT),
    )
    report = executor.run(plan, context)
    assert report.failed is False
    assert [call[-3:] for call in runner.calls] == [("sh", "-c", "echo HOOK-RAN")]
    assert spy.calls == 1


def interrupting_executor(interrupted: StepKind) -> Executor:
    """An executor whose operation for `interrupted` raises `KeyboardInterrupt`.

    The other operations succeed, so the plan's earlier steps finish and the
    interrupt lands exactly where a test puts it - the same shape as
    `executor_with`, raising what Ctrl-C raises instead of `OperationError`.
    """

    class Interrupting:
        def __init__(self, kind: StepKind) -> None:
            self._kind = kind

        def execute(self, _context: ExecutionContext, _params: Any) -> None:
            if self._kind is interrupted:
                raise KeyboardInterrupt

    return Executor(
        reporter=RecordingReporter(),
        operations={kind: Interrupting(kind) for kind in StepKind},
    )


def interrupted_message(executor: Executor, plan: Plan, context: ExecutionContext) -> str:
    with pytest.raises(SyncInterrupted) as error:
        executor.run(plan, context)
    return str(error.value)


def test_an_interrupt_reports_the_last_step_that_finished(tmp_path: Path) -> None:
    # The executor already knows: it has been appending outcomes as it goes,
    # and an interrupt used to fly past that list and discard it.
    message = interrupted_message(
        interrupting_executor(StepKind.DB_IMPORT),
        plan_of(step(1, StepKind.DB_DUMP), step(2, StepKind.DB_IMPORT)),
        context_at(tmp_path),
    )
    assert "after db.dump" in message


def test_an_interrupt_during_sanitize_warns_the_database_is_not_fully_sanitized(
    tmp_path: Path,
) -> None:
    message = interrupted_message(
        interrupting_executor(StepKind.SANITIZE),
        plan_of(step(1, StepKind.DB_IMPORT), step(2, StepKind.SANITIZE)),
        context_at(tmp_path),
    )
    assert "during sanitize, after db.import" in message
    # Not "not sanitized": an interrupt mid-sanitize may have applied some rules.
    assert "was not fully sanitized" in message
    assert "--steps sanitize" in message


def test_an_interrupt_before_the_import_does_not_warn_about_data(tmp_path: Path) -> None:
    # Nothing was replaced yet, so claiming production data is on the machine
    # would be false - and a warning that cries wolf is worse than none.
    message = interrupted_message(
        interrupting_executor(StepKind.DB_DUMP),
        plan_of(step(1, StepKind.DB_DUMP)),
        context_at(tmp_path),
    )
    assert message == "Interrupted during db.dump, before the first step finished."


def test_a_sanitize_only_rerun_interrupted_says_what_it_cannot_know(tmp_path: Path) -> None:
    """`--steps sanitize` is the remedy the two definite warnings recommend,
    and it was the one blind spot: the data branch required DB_IMPORT among
    the finished steps, so an interrupt halfway through a sanitize-only
    re-run said "Interrupted during sanitize, after preflight." and nothing
    at all about the data. This run cannot know what the database holds - the
    import was in an earlier one - so the sentence is conditional rather than
    absent, and still carries the remedy.
    """
    message = interrupted_message(
        interrupting_executor(StepKind.SANITIZE),
        plan_of(step(1, StepKind.PREFLIGHT), step(2, StepKind.SANITIZE)),
        context_at(tmp_path),
    )
    assert message.startswith("Interrupted during sanitize, after preflight.")
    assert "Sanitizing did not finish" in message
    assert "if the local database holds production data" in message
    assert "may be only partly sanitized" in message
    assert "--steps sanitize" in message
    # It must claim no more than that: nothing here knows the database *was*
    # replaced, and saying so would be the false alarm the hedge exists for.
    assert "has been replaced" not in message


def test_a_sanitize_only_rerun_that_finished_sanitizing_says_nothing(tmp_path: Path) -> None:
    # The conditional sentence is about sanitizing having been cut short. A
    # run that got through it has nothing left to warn about, whatever an
    # earlier run imported.
    message = interrupted_message(
        interrupting_executor(StepKind.SETUP),
        plan_of(step(1, StepKind.SANITIZE), step(2, StepKind.SETUP)),
        context_at(tmp_path),
    )
    assert message == "Interrupted during setup, after sanitize."


def test_an_interrupt_once_sanitizing_finished_does_not_warn(tmp_path: Path) -> None:
    message = interrupted_message(
        interrupting_executor(StepKind.SETUP),
        plan_of(step(1, StepKind.DB_IMPORT), step(2, StepKind.SANITIZE), step(3, StepKind.SETUP)),
        context_at(tmp_path),
    )
    assert "after sanitize" in message
    assert "sanit" not in message.removeprefix("Interrupted during setup, after sanitize.")


def test_a_finished_import_hook_does_not_stand_in_for_the_import(tmp_path: Path) -> None:
    # A hook carries its parent's `kind`, so a finished `pre_db_import` hook
    # looks like a finished import to anything that only reads `kind`. No
    # import step has finished, though, and saying production data is on the
    # machine would be false.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"])
    message = interrupted_message(
        interrupting_executor(StepKind.ENV),
        plan_of(
            step(1, StepKind.DB_IMPORT, is_hook=True, params={"run": "x"}),
            step(2, StepKind.ENV),
        ),
        replace(context_at(tmp_path), runner=runner),
    )
    assert message == "Interrupted during env, after a db.import hook."


def test_a_finished_pre_sanitize_hook_does_not_hide_the_warning(tmp_path: Path) -> None:
    # The same confusion in the dangerous direction: a finished
    # `pre_sanitize` hook must not count as sanitising having happened.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"])
    message = interrupted_message(
        interrupting_executor(StepKind.SANITIZE),
        plan_of(
            step(1, StepKind.DB_IMPORT),
            step(2, StepKind.SANITIZE, is_hook=True, params={"run": "x"}),
            step(3, StepKind.SANITIZE),
        ),
        replace(context_at(tmp_path), runner=runner),
    )
    assert "during sanitize, after a sanitize hook" in message
    assert "was not fully sanitized" in message


class InterruptingRunner(FakeCommandRunner):
    """A runner whose every command is interrupted, as a hook's would be."""

    def run(self, argv: Sequence[str], **_kwargs: Any) -> CommandResult:
        raise KeyboardInterrupt


def test_an_interrupted_post_import_hook_still_warns_because_the_import_finished(
    tmp_path: Path,
) -> None:
    # The hook is interrupted, not the import: the import step itself
    # finished, so the database really does hold production data.
    message = interrupted_message(
        interrupting_executor(StepKind.SANITIZE),
        plan_of(
            step(1, StepKind.DB_IMPORT),
            step(2, StepKind.DB_IMPORT, is_hook=True, params={"run": "x"}),
        ),
        replace(context_at(tmp_path), runner=InterruptingRunner()),
    )
    assert message.startswith("Interrupted during a db.import hook, after db.import.")
    assert "was not sanitized" in message


def test_an_interrupt_skips_post_sync_hooks_and_says_so(tmp_path: Path) -> None:
    context = context_with_post_sync(tmp_path)
    message = interrupted_message(
        interrupting_executor(StepKind.DB_DUMP), plan_of(step(1, StepKind.DB_DUMP)), context
    )
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    assert runner.calls == []
    assert "post_sync hooks did not run" in message


def test_an_interrupt_does_not_mention_post_sync_when_none_are_configured(
    tmp_path: Path,
) -> None:
    message = interrupted_message(
        interrupting_executor(StepKind.DB_DUMP),
        plan_of(step(1, StepKind.DB_DUMP)),
        context_at(tmp_path),
    )
    assert "post_sync" not in message


def test_the_interrupt_is_chained_so_the_original_is_not_lost(tmp_path: Path) -> None:
    with pytest.raises(SyncInterrupted) as error:
        interrupting_executor(StepKind.DB_DUMP).run(
            plan_of(step(1, StepKind.DB_DUMP)), context_at(tmp_path)
        )
    assert isinstance(error.value.__cause__, KeyboardInterrupt)


def test_an_interrupt_during_the_import_says_the_database_may_be_partly_replaced(
    tmp_path: Path,
) -> None:
    # This is the moment the warning is needed most, and the one the "last
    # finished step" alone would stay silent about: nothing has finished
    # that touches the database, yet it may be half-replaced. "May", because
    # an interrupt cannot say how far the import got.
    #
    # The fake operation raises at the entry of `execute`, so this pins the
    # message for "the import step was running and was not at its prompt" and
    # nothing finer - it cannot tell one instant inside the step from
    # another. What makes the message true of every such instant is the
    # operation's own ordering (the drop before any rewrite of the dump),
    # which `tests/operations/test_db_import.py::
    # test_the_dump_is_rewritten_after_the_drop_and_before_the_import` pins.
    message = interrupted_message(
        interrupting_executor(StepKind.DB_IMPORT),
        plan_of(step(1, StepKind.DB_DUMP), step(2, StepKind.DB_IMPORT)),
        context_at(tmp_path),
    )
    assert message.startswith("Interrupted during db.import, after db.dump.")
    assert "may be partly replaced" in message
    assert "is not sanitized" in message
    assert "has been replaced" not in message


def test_an_interrupted_import_hook_says_nothing_about_the_data(tmp_path: Path) -> None:
    # The same trap as the finished hooks: a `pre_db_import` hook carries
    # `kind == DB_IMPORT`, but the import has not started when it runs.
    message = interrupted_message(
        interrupting_executor(StepKind.DB_IMPORT),
        plan_of(
            step(1, StepKind.DB_IMPORT, is_hook=True, params={"run": "x"}),
            step(2, StepKind.DB_IMPORT),
        ),
        replace(context_at(tmp_path), runner=InterruptingRunner()),
    )
    assert message == "Interrupted during a db.import hook, before the first step finished."


class _InterruptedAfterLastStep(tuple[PlannedStep, ...]):
    """A step sequence whose iteration is interrupted once it has yielded every step.

    That is Ctrl-C landing in the instant after the last outcome was recorded
    and before the loop noticed it had finished - the one place no step is
    running, which an ordinary operation cannot reach.
    """

    def __iter__(self) -> Iterator[PlannedStep]:
        yield from super().__iter__()
        raise KeyboardInterrupt


def test_an_interrupt_between_steps_still_reports_what_finished(tmp_path: Path) -> None:
    # No step is running then, so there is no "during", only what finished -
    # and the lookup of the running step must not run off the end of the plan.
    plan = replace(
        plan_of(step(1, StepKind.DB_DUMP)),
        steps=_InterruptedAfterLastStep(plan_of(step(1, StepKind.DB_DUMP)).steps),
    )
    message = interrupted_message(
        interrupting_executor(StepKind.PREFLIGHT), plan, context_at(tmp_path)
    )
    assert message == "Interrupted after db.dump."


class PromptingOperation:
    """An operation that asks `confirm_destructive` first, as `db.import` does.

    `interrupting_executor` raises the moment `execute` is called, so it cannot
    tell "waiting at the prompt" from "in the middle of the import". This asks
    through the real `confirm_destructive`, so what reaches the executor is
    whatever the real prompt raises on Ctrl-C.
    """

    def execute(self, _context: ExecutionContext, _params: Any) -> None:
        confirm_destructive("This will drop the local database. Continue?")


def ctrl_c_at_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)


def test_an_interrupt_at_the_drop_prompt_says_nothing_was_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The likeliest Ctrl-C of the whole run: the user is asked whether to drop
    # their database and changes their mind. Nothing has been touched, so
    # "may be partly replaced" would be the false alarm this tool must not raise.
    ctrl_c_at_the_prompt(monkeypatch)
    executor = Executor(
        reporter=RecordingReporter(),
        operations={StepKind.DB_DUMP: _SpyOperation(), StepKind.DB_IMPORT: PromptingOperation()},
    )
    message = interrupted_message(
        executor,
        plan_of(step(1, StepKind.DB_DUMP), step(2, StepKind.DB_IMPORT)),
        context_at(tmp_path),
    )
    assert message.startswith("Interrupted at the confirmation prompt of db.import, after db.dump.")
    assert "had not changed anything" in message
    assert "replaced" not in message
    assert "sanit" not in message


def test_an_interrupt_at_a_later_prompt_still_reports_what_earlier_steps_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The prompt tells us the running step changed nothing - not that the
    # import before it did not happen. That data is still on the machine
    # unsanitized, and still the one thing worth saying.
    ctrl_c_at_the_prompt(monkeypatch)
    executor = Executor(
        reporter=RecordingReporter(),
        operations={StepKind.DB_IMPORT: _SpyOperation(), StepKind.ENV: PromptingOperation()},
    )
    message = interrupted_message(
        executor,
        plan_of(step(1, StepKind.DB_IMPORT), step(2, StepKind.ENV)),
        context_at(tmp_path),
    )
    assert "confirmation prompt of env, after db.import" in message
    assert "has been replaced with production data and was not sanitized" in message


def test_an_interrupt_in_the_post_sync_hooks_still_reports_the_unsanitized_database(
    tmp_path: Path,
) -> None:
    # The steps are finished by then, so what they left behind is known - and
    # `post_sync` is no reason to lose it.
    message = interrupted_message(
        interrupting_executor(StepKind.SANITIZE),
        plan_of(step(1, StepKind.DB_IMPORT)),
        replace(context_with_post_sync(tmp_path), runner=InterruptingRunner()),
    )
    assert message.startswith("Interrupted after db.import.")
    assert "was not sanitized" in message
    assert "post_sync hooks were interrupted and may not have finished" in message
    assert "did not run" not in message


def test_an_unreadable_post_sync_block_does_not_replace_the_interrupt_report(
    tmp_path: Path,
) -> None:
    # `mode` without `commands` is a ConfigError when the hooks are resolved.
    # Reporting the interrupt must not die of it and end with exit code 1.
    context = replace(
        context_at(tmp_path),
        merged=merge_layers([LayerDoc("project", {"hooks": {"post_sync": {"mode": "append"}}})]),
    )
    message = interrupted_message(
        interrupting_executor(StepKind.DB_DUMP), plan_of(step(1, StepKind.DB_DUMP)), context
    )
    assert message == "Interrupted during db.dump, before the first step finished."
