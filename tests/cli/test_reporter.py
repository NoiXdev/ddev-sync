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


def test_no_ansi_escapes_reach_the_write_callback_when_colour_is_off() -> None:
    reporter, lines = capture()
    reporter.starting(step(StepKind.DB_DUMP))
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok"))
    reporter.notice("x")
    assert all("\x1b[" not in line for line in lines)


def test_the_default_line_describes_the_step_rather_than_its_command() -> None:
    reporter, lines = capture()
    reporter.starting(step(StepKind.DB_DUMP, detail="mysqldump --single-transaction $DB"))
    assert lines == ["  ..    db.dump       dumping the remote database"]
    assert "mysqldump" not in lines[0]


def test_a_finished_step_does_not_repeat_the_description() -> None:
    reporter, lines = capture()
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok"))
    assert lines == ["  OK    db.dump"]


def test_a_failed_step_keeps_a_separator_before_its_name() -> None:
    # The regression this guards: `_MARKS` used to carry its own padding, and
    # `FAIL` filled the field exactly, so a failure printed `FAILdb.import`.
    reporter, lines = capture()
    reporter.finished(
        StepOutcome(step=step(StepKind.DB_IMPORT), status="failed", error=DdevSyncError("x"))
    )
    assert lines[0].startswith("  FAIL  db.import")


def test_colour_marks_success_green_failure_red_and_notices_yellow() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append, colour=True)
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok"))
    reporter.finished(
        StepOutcome(step=step(StepKind.DB_IMPORT), status="failed", error=DdevSyncError("x"))
    )
    reporter.notice("heads up")
    assert "\x1b[32mOK\x1b[0m" in lines[0]
    assert "\x1b[31mFAIL\x1b[0m" in lines[1]
    # `lines[2]` is the failed step's error text, so the notice is the fourth line.
    assert "\x1b[33m!\x1b[0m" in lines[3]
    # The padding stays outside the escapes, so every column still lines up.
    assert lines[0].index("db.dump") == lines[1].index("db.import")


def test_a_hook_line_shows_its_own_command_not_its_parents_description() -> None:
    # A hook keeps its parent's kind, but its command is the user's own, so the
    # parent's fixed description would describe something that is not running.
    reporter, lines = capture()
    hook = PlannedStep(
        index=1,
        kind=StepKind.DB_IMPORT,
        where="web",
        detail="pre_db_import: ./scripts/backup.sh",
        source="project",
        is_hook=True,
    )
    reporter.starting(hook)
    assert lines == ["  ..    db.import     pre_db_import: ./scripts/backup.sh"]
    assert "replacing the local database" not in lines[0]


def test_error_text_sits_under_the_step_name() -> None:
    reporter, lines = capture()
    reporter.finished(
        StepOutcome(step=step(StepKind.DB_IMPORT), status="failed", error=DdevSyncError("boom"))
    )
    assert lines[1].index("boom") == lines[0].index("db.import")


def test_level_one_appends_the_command_detail() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append, verbose=1)
    reporter.starting(step(StepKind.DB_DUMP, detail="mysqldump --single-transaction $DB"))
    assert "mysqldump --single-transaction $DB" in lines[0]


def test_level_zero_keeps_the_fixed_description_for_a_regular_step() -> None:
    reporter, lines = capture()
    reporter.starting(step(StepKind.DB_DUMP, detail="mysqldump --single-transaction $DB"))
    assert "mysqldump" not in lines[0]


def test_level_three_reports_how_long_a_step_took() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append, verbose=3)
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok", duration=1.5))
    assert any("1.5" in line for line in lines)


def test_the_duration_is_not_shown_below_level_three() -> None:
    reporter, lines = capture()
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok", duration=1.5))
    assert not any("1.5" in line for line in lines)


def test_level_three_shows_no_duration_for_a_step_that_never_ran() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append, verbose=3)
    reporter.finished(StepOutcome(step=step(StepKind.SETUP), status="skipped"))
    assert "(" not in lines[0]
