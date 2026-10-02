from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.executor import Executor, RunReport, StepOutcome
from ddev_sync.engine.plan import Plan, PlannedStep
from ddev_sync.engine.steps import StepKind
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.transport.fake import FakeCommandRunner


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
