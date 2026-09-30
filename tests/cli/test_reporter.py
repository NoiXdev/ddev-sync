from ddev_sync.cli.reporter import ConsoleReporter
from ddev_sync.engine.executor import StepOutcome
from ddev_sync.engine.plan import PlannedStep
from ddev_sync.engine.steps import StepKind
from ddev_sync.errors import DdevSyncError


def step(kind: StepKind, detail: str = "doing a thing") -> PlannedStep:
    return PlannedStep(index=1, kind=kind, where="local", detail=detail, source="profile")


def capture() -> tuple[ConsoleReporter, list[str]]:
    lines: list[str] = []
    return ConsoleReporter(write=lines.append), lines


def test_a_successful_step_is_marked_ok() -> None:
    reporter, lines = capture()
    outcome = StepOutcome(step=step(StepKind.DB_DUMP), status="ok")
    reporter.starting(outcome.step)
    reporter.finished(outcome)
    assert any("OK" in line and "db.dump" in line for line in lines)


def test_a_failed_step_shows_the_error() -> None:
    reporter, lines = capture()
    outcome = StepOutcome(
        step=step(StepKind.DB_IMPORT), status="failed", error=DdevSyncError("disk full")
    )
    reporter.starting(outcome.step)
    reporter.finished(outcome)
    joined = "\n".join(lines)
    assert "FAIL" in joined
    assert "disk full" in joined


def test_a_skipped_step_is_marked_and_quiet() -> None:
    reporter, lines = capture()
    reporter.finished(StepOutcome(step=step(StepKind.SETUP), status="skipped"))
    assert any("skipped" in line for line in lines)


def test_notices_are_prefixed_so_they_stand_out() -> None:
    reporter, lines = capture()
    reporter.notice("using typo3@12")
    assert any(line.strip().startswith("!") for line in lines)


def test_the_sanitize_warning_fires_when_sanitize_is_not_in_the_plan() -> None:
    reporter, lines = capture()
    reporter.warn_unsanitized("production")
    joined = "\n".join(lines)
    assert "production" in joined
    assert "not sanitized" in joined.lower()


def test_no_ansi_escapes_reach_the_write_callback() -> None:
    reporter, lines = capture()
    reporter.starting(step(StepKind.DB_DUMP))
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok"))
    reporter.notice("x")
    assert all("\x1b[" not in line for line in lines)
