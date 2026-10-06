# Console Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the sync's console output quiet by default, legible in colour, honest about progress, and readable at three levels of detail.

**Architecture:** The reporter keeps its one job — turning executor events into strings — and gains two injected values, `colour` and `verbose`, decided at the CLI edge the way `doctor` already decides colour. Command output, which the reporter never sees, is surfaced instead by a decorator around `CommandRunner` installed at the edge, so neither `transport/` nor any operation changes shape. Progress reaches the terminal by letting the runner inherit stdout for one call, mirroring the `stdout_path` mode it already has.

**Tech Stack:** Python 3.12, click, questionary, uv, pytest.

**Spec:** `docs/specs/2026-10-06-console-output-design.md`. Read it for the reasoning; this plan is the construction.

## Global Constraints

- Tests: `uv run pytest`. Coverage gate is **100 % line and branch** (`--cov-fail-under=100`, `branch = true`); `# pragma: no cover` is forbidden anywhere in the project.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests` must pass. Ruff line length is 100.
- `subprocess` may only be imported in `src/ddev_sync/transport/runner.py`; a test parses the AST of every other module to enforce this.
- Everything code-side is English: code, comments, docstrings, test names, commit messages. **The step descriptions are user-visible output and are English too** — this project has no i18n layer and every other string it prints is English.
- Conventional Commits. Every commit message ends with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Write the message to a file and use `git commit -F <file>`; do not nest a heredoc inside another.
- Comments explain *why*, not *what*, and must be true of the code beside them. This project treats a comment that states something the adjacent code does not do as a defect in its own right.
- Colour is never sniffed inside a renderer. It is decided once at the edge with `colour_enabled(os.environ, sys.stdout)` and passed in, which is what keeps renderer tests plain string comparisons.
- Do not change `PlannedStep.detail` or anything in `cli/render.py`. The dry run prints `detail` and the full command is the point there.

---

### Task 1: Quiet default lines, a fixed marker column, and colour

**Files:**
- Modify: `src/ddev_sync/engine/steps.py` (add `STEP_DESCRIPTIONS` below `STEP_ORDER`)
- Modify: `src/ddev_sync/cli/reporter.py` (whole file)
- Modify: `src/ddev_sync/cli/commands.py` (`run_command`, around the `ConsoleReporter(...)` construction)
- Modify: `src/ddev_sync/cli/main.py` (`_run`, around the `run_command(...)` call)
- Test: `tests/cli/test_reporter.py`, `tests/engine/test_steps.py`

**Interfaces:**
- Consumes: `paint`, `paint_padded`, `Colour` from `ddev_sync.cli.colour`; `colour_enabled(environ, stream)` from the same module; `StepKind` from `ddev_sync.engine.steps`.
- Produces: `STEP_DESCRIPTIONS: dict[StepKind, str]`; `ConsoleReporter(write, *, colour: bool = False)`; `run_command(..., colour: bool = False)`. Task 2 adds a `verbose` parameter beside `colour` on both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_steps.py`:

```python
def test_every_step_kind_has_a_description() -> None:
    # The description is what the default output shows instead of a command
    # line, so a new kind without one would print an empty column - or raise
    # mid-sync, depending on how it is looked up. Keeping the check beside the
    # enum means the failure arrives when the kind is added.
    assert set(STEP_DESCRIPTIONS) == set(StepKind)
    assert all(text and text == text.strip() for text in STEP_DESCRIPTIONS.values())
```

Add `STEP_DESCRIPTIONS` to that module's existing `from ddev_sync.engine.steps import ...`.

Replace `test_no_ansi_escapes_reach_the_write_callback` in `tests/cli/test_reporter.py` and add four tests:

```python
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
    assert "\x1b[33m!\x1b[0m" in lines[2]
    # The padding stays outside the escapes, so every column still lines up.
    assert lines[0].index("db.dump") == lines[1].index("db.import")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_reporter.py tests/engine/test_steps.py -v`
Expected: the five new tests FAIL — `STEP_DESCRIPTIONS` does not exist, `ConsoleReporter` takes no `colour`, and the current lines carry the command detail.

- [ ] **Step 3: Add the descriptions**

In `src/ddev_sync/engine/steps.py`, below `STEP_ORDER`:

```python
# What the default output shows instead of a command line. Kept beside the
# enum rather than in the reporter so that adding a kind without describing it
# fails in the module that defines it - the reporter would only print a blank
# column. The text is English like every other string this tool prints; there
# is no i18n layer.
STEP_DESCRIPTIONS: dict[StepKind, str] = {
    StepKind.PREFLIGHT: "checking the connection, paths and free space",
    StepKind.DB_DUMP: "dumping the remote database",
    StepKind.DB_DOWNLOAD: "fetching the dump",
    StepKind.DB_IMPORT: "replacing the local database",
    StepKind.ENV: "updating the local env file",
    StepKind.FILES_SYNC: "syncing files from the remote",
    StepKind.S3_SYNC: "syncing the S3 bucket",
    StepKind.SANITIZE: "removing production data",
    StepKind.SETUP: "running the project's setup commands",
}
```

- [ ] **Step 4: Rewrite the reporter**

Replace the body of `src/ddev_sync/cli/reporter.py` below its module docstring. Update that docstring too: it currently says colour is the caller's business and that nothing here applies it, which this task makes false. Say instead that the *decision* stays the caller's and is passed in, which is what keeps the output assertable.

```python
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
```

- [ ] **Step 5: Add `YELLOW` to the palette**

In `src/ddev_sync/cli/colour.py`, inside `class Colour`:

```python
    YELLOW = "33"
```

Its docstring says "(green and red)" — make it "(green, red and yellow)" so it stays true.

- [ ] **Step 6: Wire the decision through**

In `src/ddev_sync/cli/commands.py::run_command`, add a keyword-only `colour: bool = False` parameter and use it:

```python
    reporter = ConsoleReporter(write=echo, colour=colour)
```

In `src/ddev_sync/cli/main.py::_run`, decide it the way `doctor` already does and pass both it and a colour-aware `echo`:

```python
    use_colour = colour_enabled(os.environ, sys.stdout)
    return run_command(
        context,
        selection,
        runner=options.runner,
        dry_run=dry_run,
        assume_yes=assume_yes,
        # `color=True` only when colour was chosen: click strips escapes from a
        # non-terminal unless told otherwise, which is the very case
        # `FORCE_COLOR` asks for. `None` leaves click's default in place.
        echo=partial(click.echo, color=True if use_colour else None),
        only_feature_setup=options.only_feature_setup,
        colour=use_colour,
    )
```

`os`, `sys`, `partial` and `colour_enabled` are already imported in that module.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/cli/test_reporter.py tests/engine/test_steps.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full gate**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all pass, coverage still 100 %. Other tests may assert the old line shape — `tests/cli/test_main.py` and `tests/cli/golden/` are the likely places. A golden file that changes is expected; update it and say so in your report. A test that asserts the command detail appears on a step line is asserting the behaviour this task removes; update it and report it.

- [ ] **Step 9: Commit**

Write the message to a file, then:

```bash
git add src/ddev_sync tests
git commit -F <message-file>
```

Message: `feat(cli): describe each step instead of printing its command line`, with a body saying the default now shows a fixed description per step kind, that `FAIL` no longer eats its separator because the marker got its own column, and that colour is decided at the edge like `doctor`'s.

---

### Task 2: Three levels of detail, with a secrets registry

**Files:**
- Create: `src/ddev_sync/cli/verbose.py`
- Modify: `src/ddev_sync/operations/base.py` (`ExecutionContext`, add a `secrets` field)
- Modify: `src/ddev_sync/operations/database.py` (`read_remote_credentials`, register the password)
- Modify: `src/ddev_sync/engine/executor.py` (`StepOutcome` gains a duration; `_run_step` measures it)
- Modify: `src/ddev_sync/cli/reporter.py` (a `verbose` level)
- Modify: `src/ddev_sync/cli/commands.py` (`run_command` builds the secrets set and wraps the runner)
- Modify: `src/ddev_sync/cli/main.py` (the `-v` option, carried on `_Options`)
- Test: `tests/cli/test_verbose.py` (new), `tests/cli/test_reporter.py`, `tests/operations/test_db_dump.py`, `tests/engine/test_executor.py`

**Interfaces:**
- Consumes: `ConsoleReporter(write, *, colour)` from Task 1; `CommandRunner`, `CommandResult` from `ddev_sync.transport.runner`.
- Produces: `VerboseRunner(inner, *, write, secrets, verbose)` implementing `CommandRunner`; `ExecutionContext.secrets: set[str]`; `StepOutcome.duration: float | None`; `ConsoleReporter(write, *, colour, verbose)`; `run_command(..., colour, verbose)`.

**Why a decorator rather than a reporter change.** The reporter is handed a `StepOutcome`, and one step runs many commands, so it never sees their output and cannot print it. Giving the runner a presentation callback would put output formatting inside `transport/`. A decorator at the CLI edge has every argv and every result, changes nothing in `transport/` or in any operation, and is removed entirely at level 0 and 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_verbose.py`:

```python
from ddev_sync.cli.verbose import VerboseRunner
from ddev_sync.transport.fake import FakeCommandRunner


def runner_with(stdout: str = "", stderr: str = "") -> FakeCommandRunner:
    fake = FakeCommandRunner()
    fake.script_prefix(["echo"], stdout=stdout, stderr=stderr)
    return fake


def test_level_two_prints_the_output_of_every_command() -> None:
    lines: list[str] = []
    runner = VerboseRunner(runner_with(stdout="two rows\n"), write=lines.append, secrets=set(), verbose=2)
    runner.run(["echo", "hello"])
    assert any("two rows" in line for line in lines)


def test_level_two_does_not_print_the_argument_vector() -> None:
    # The argv carries `--password=` for the local database calls; it belongs
    # to level three, which the user opted into more deliberately.
    lines: list[str] = []
    runner = VerboseRunner(runner_with(), write=lines.append, secrets=set(), verbose=2)
    runner.run(["echo", "--password=sekrit"])
    assert not any("--password" in line for line in lines)


def test_level_three_prints_the_argument_vector() -> None:
    lines: list[str] = []
    runner = VerboseRunner(runner_with(), write=lines.append, secrets=set(), verbose=3)
    runner.run(["echo", "hello"])
    assert any("echo hello" in line for line in lines)


def test_a_registered_secret_never_reaches_the_output() -> None:
    lines: list[str] = []
    secrets = {"hunter2"}
    runner = VerboseRunner(
        runner_with(stdout="password is hunter2\n"), write=lines.append, secrets=secrets, verbose=3
    )
    runner.run(["echo", "--password=hunter2"])
    assert not any("hunter2" in line for line in lines)


def test_a_secret_registered_after_construction_is_still_scrubbed() -> None:
    # The set is shared with the ExecutionContext and filled during the run,
    # when `read_remote_credentials` first resolves a password - which happens
    # after this wrapper was built.
    lines: list[str] = []
    secrets: set[str] = set()
    runner = VerboseRunner(runner_with(stdout="later\n"), write=lines.append, secrets=secrets, verbose=2)
    secrets.add("later")
    runner.run(["echo", "x"])
    assert not any("later" in line for line in lines)


def test_the_result_is_returned_unchanged() -> None:
    inner = runner_with(stdout="payload\n")
    runner = VerboseRunner(inner, write=lambda _: None, secrets=set(), verbose=3)
    assert runner.run(["echo", "x"]).stdout == "payload\n"
```

Add to `tests/cli/test_reporter.py`:

```python
def test_level_one_appends_the_command_detail() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append, verbose=1)
    reporter.starting(step(StepKind.DB_DUMP, detail="mysqldump --single-transaction $DB"))
    assert "mysqldump --single-transaction $DB" in lines[0]


def test_level_three_reports_how_long_a_step_took() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append, verbose=3)
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok", duration=1.5))
    assert any("1.5" in line for line in lines)


def test_the_duration_is_not_shown_below_level_three() -> None:
    reporter, lines = capture()
    reporter.finished(StepOutcome(step=step(StepKind.DB_DUMP), status="ok", duration=1.5))
    assert not any("1.5" in line for line in lines)
```

Add to `tests/operations/test_db_dump.py`:

```python
def test_a_resolved_remote_password_is_registered_for_scrubbing(tmp_path: Path) -> None:
    # Whatever prints command output at -vv has to know what to remove, and
    # this is the one funnel every credential source passes through.
    config = {
        "database": {
            "remote_credentials": "explicit",
            "credentials": {
                "name": "mydb",
                "user": "u",
                "password": "hunter2",
                "host": "h",
                "port": "3306",
            },
        }
    }
    context = make_context(FakeCommandRunner(), tmp_path, config)
    read_remote_credentials(context)
    assert "hunter2" in context.secrets
```

Add to `tests/engine/test_executor.py` (use that file's existing context and plan helpers):

```python
def test_each_outcome_carries_how_long_its_step_took(tmp_path: Path) -> None:
    # Level three reports it, so it has to be measured even when nothing
    # displays it. `>= 0.0` rather than `> 0.0`: a step that does nothing can
    # finish inside the clock's resolution.
    executor = executor_with([])
    report = executor.run(plan_of(step(1, StepKind.PREFLIGHT)), context_at(tmp_path))
    outcome = report.outcomes[0]
    assert outcome.duration is not None
    assert outcome.duration >= 0.0
```

`executor_with`, `plan_of`, `step` and `context_at` are that file's own helpers,
already used by `test_every_step_runs_in_order`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_verbose.py tests/cli/test_reporter.py tests/operations/test_db_dump.py tests/engine/test_executor.py -v`
Expected: FAIL — `ddev_sync.cli.verbose` does not exist, `ConsoleReporter` takes no `verbose`, `ExecutionContext` has no `secrets`, `StepOutcome` has no `duration`.

- [ ] **Step 3: Add the secrets field**

In `src/ddev_sync/operations/base.py`, beside `cache` on `ExecutionContext`:

```python
    # Mutable for the same reason as `cache` above, and `compare=False` for the
    # same reason too. Whatever prints command output at `-vv` scrubs these
    # from every line, and it is handed this very set rather than a copy,
    # because the passwords are resolved during the run, after it was built.
    #
    # The local database password is deliberately absent. It is
    # `database.LOCAL_DB_PASSWORD`, the fixed value DDEV documents publicly,
    # and scrubbing the three characters `db` would mangle the database name,
    # the container host `ddev-<project>-db` and half of every line besides.
    secrets: set[str] = field(default_factory=set, repr=False, compare=False)
```

- [ ] **Step 4: Register the password**

In `src/ddev_sync/operations/database.py::read_remote_credentials`, after the credentials are resolved and before they are cached:

```python
    if credentials.password:
        context.secrets.add(credentials.password)
```

Place it so every source passes through it — this function is the single funnel for `explicit`, `env` and `wp_cli`, and it is memoised, so the registration happens once. Guard on truthiness: an empty password would otherwise scrub the empty string out of every line.

- [ ] **Step 5: Write the decorator**

Create `src/ddev_sync/cli/verbose.py`:

```python
"""A `CommandRunner` that narrates what it runs.

Installed at the CLI edge for `-vv` and above, and absent below it. It lives
here rather than in `transport/` because what it does is presentation: the
runner's job is to execute an argv and report the result, and formatting that
result for a terminal is the CLI's.

It exists at all because the reporter cannot do this. The reporter is handed a
`StepOutcome` per step, while one step runs many commands, so it never sees an
argv or any output. This wrapper sees both.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ddev_sync.transport.runner import CommandResult, CommandRunner


class VerboseRunner:
    """Delegates to `inner` and prints what it ran, scrubbed of `secrets`."""

    def __init__(
        self,
        inner: CommandRunner,
        *,
        write: Callable[[str], None],
        secrets: set[str],
        verbose: int,
    ) -> None:
        self._inner = inner
        self._write = write
        # Held, not copied: the passwords are resolved during the run, by
        # `operations/database.py::read_remote_credentials`, which is long
        # after this wrapper is built.
        self._secrets = secrets
        self._verbose = verbose

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
        passthrough: bool = False,
    ) -> CommandResult:
        """Run `argv` through the wrapped runner, printing what it did."""
        if self._verbose >= 3:
            self._emit(f"$ {' '.join(argv)}")
        result = self._inner.run(
            argv, cwd=cwd, env=env, stdout_path=stdout_path, passthrough=passthrough
        )
        for stream in (result.stdout, result.stderr):
            for line in stream.splitlines():
                self._emit(line)
        return result

    def _emit(self, line: str) -> None:
        self._write(f"      {self._scrub(line)}")

    def _scrub(self, line: str) -> str:
        for secret in self._secrets:
            line = line.replace(secret, "***")
        return line
```

**Note on `passthrough`:** Task 3 adds that parameter to the protocol. Write it here now, with the default, so the two tasks do not have to meet in the middle. Until Task 3 lands, `FakeCommandRunner.run` will not accept it — which is why the tests above never pass it.

- [ ] **Step 6: Measure the duration**

In `src/ddev_sync/engine/executor.py`, add to `StepOutcome`:

```python
    duration: float | None = None
```

and in `_run_step`, wrap the dispatch:

```python
    def _run_step(self, step: PlannedStep, context: ExecutionContext) -> StepOutcome:
        self.reporter.starting(step)
        started = time.monotonic()
        try:
            self._dispatch(step, context)
        except DdevSyncError as error:
            outcome = StepOutcome(
                step=step, status="failed", error=error, duration=time.monotonic() - started
            )
        else:
            outcome = StepOutcome(step=step, status="ok", duration=time.monotonic() - started)
        self.reporter.finished(outcome)
        return outcome
```

`time.monotonic`, not `time.time`: a clock adjustment mid-sync must not produce a negative duration. Add `import time` at the top.

- [ ] **Step 7: Teach the reporter the levels**

In `src/ddev_sync/cli/reporter.py`, add `verbose: int = 0` to `__init__` beside `colour`, store it, and use it:

```python
    def starting(self, step: PlannedStep) -> None:
        """Announce a step that is about to run, and say what it will do."""
        text = STEP_DESCRIPTIONS[step.kind] if self._verbose == 0 else step.detail
        self._write(f"  {_RUNNING:<{_MARK_WIDTH}}{step.kind.value:<{_NAME_WIDTH}}{text}")
```

and in `finished`, after the status line is built and before it is written:

```python
        if self._verbose >= 3 and outcome.duration is not None:
            line = f"{line.rstrip()}  ({outcome.duration:.1f}s)"
```

- [ ] **Step 8: Add the flag and wire it**

In `src/ddev_sync/cli/main.py`, add `verbose: int` to `_Options`, add the option to the group that already defines `--yes` and `--dry-run`:

```python
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Show more: -v the commands, -vv their output, -vvv the full argv and timings.",
)
```

carry it into `_Options`, and pass `verbose=options.verbose` to `run_command`.

In `src/ddev_sync/cli/commands.py::run_command`, add `verbose: int = 0`, share one set, and wrap:

```python
    secrets: set[str] = set()
    reporter = ConsoleReporter(write=echo, colour=colour, verbose=verbose)
    if verbose >= 2:
        runner = VerboseRunner(runner, write=echo, secrets=secrets, verbose=verbose)
    execution = _execution_context(context, runner=runner, assume_yes=assume_yes, secrets=secrets)
```

and give `_execution_context` a `secrets: set[str]` parameter it passes to `ExecutionContext`.

- [ ] **Step 9: Run the tests, then the gate**

Run: `uv run pytest tests/cli/test_verbose.py tests/cli/test_reporter.py tests/operations/test_db_dump.py tests/engine/test_executor.py -v`
Expected: PASS.

Then: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all pass at 100 % coverage. If a `StepOutcome(...)` built in another test now needs a duration, it does not — the field has a default.

- [ ] **Step 10: Commit**

Message: `feat(cli): add -v, -vv and -vvv`, with a body explaining that command output is surfaced by a decorator around the runner because the reporter only ever sees one outcome per step, and that every level scrubs resolved remote passwords while the fixed local one is deliberately not registered.

---

### Task 3: rsync progress on the terminal

**Files:**
- Modify: `src/ddev_sync/transport/runner.py` (`CommandRunner` protocol, `SubprocessRunner.run`)
- Modify: `src/ddev_sync/transport/fake.py` (`FakeCommandRunner.run`, `Invocation`)
- Modify: `src/ddev_sync/operations/files.py` (`FilesRsyncOperation.execute`)
- Modify: `src/ddev_sync/operations/base.py` (`ExecutionContext`, add `stdout_is_terminal`)
- Modify: `src/ddev_sync/cli/commands.py` (`_execution_context`), `src/ddev_sync/cli/main.py` (`_run`)
- Test: `tests/transport/test_runner.py`, `tests/operations/test_files.py`

**Interfaces:**
- Consumes: `ExecutionContext` from Task 2 (which added `secrets`); `_execution_context(context, *, runner, assume_yes, secrets)`.
- Produces: `CommandRunner.run(..., passthrough: bool = False)`; `ExecutionContext.stdout_is_terminal: bool`; `Invocation.passthrough: bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/operations/test_files.py` (use that file's existing context helper):

```python
PAIR = {"remote": "public/fileadmin/", "local": "public/fileadmin/"}


def test_rsync_asks_for_passthrough_when_stdout_is_a_terminal(tmp_path: Path) -> None:
    # rsync is already given `--info=progress2`; the only reason the progress
    # was never seen is that the runner captured it.
    runner = ok_runner()
    context = replace(make_context(runner, tmp_path), stdout_is_terminal=True)
    FilesRsyncOperation().execute(context, PAIR)
    assert runner.invocations[0].passthrough is True


def test_rsync_does_not_ask_for_passthrough_without_a_terminal(tmp_path: Path) -> None:
    # In a CI log rsync's `\r`-separated progress becomes thousands of lines.
    runner = ok_runner()
    context = make_context(runner, tmp_path)
    FilesRsyncOperation().execute(context, PAIR)
    assert runner.invocations[0].passthrough is False


def test_a_failed_passthrough_rsync_still_reports_its_stderr(tmp_path: Path) -> None:
    # Passthrough inherits stdout only. stderr stays captured precisely so
    # this message keeps working.
    runner = FakeCommandRunner()
    runner.script_prefix(["rsync"], returncode=23, stderr="rsync: link_stat failed")
    context = replace(make_context(runner, tmp_path), stdout_is_terminal=True)
    with pytest.raises(OperationError) as caught:
        FilesRsyncOperation().execute(context, PAIR)
    assert "link_stat failed" in str(caught.value)
```

`make_context` and `ok_runner` are that file's own helpers. Add
`from dataclasses import replace` at the top; `tests/operations/test_db_import.py`
and `tests/engine/test_executor.py` already use `replace` on a context the same
way. The second test relies on `stdout_is_terminal` defaulting to `False`.

Add to `tests/transport/test_runner.py`:

```python
def test_passthrough_inherits_stdout_but_still_captures_stderr(tmp_path: Path) -> None:
    # The contract the files step depends on: its progress goes to the
    # terminal, its error message still has something to quote.
    result = SubprocessRunner().run(
        ["sh", "-c", "echo out; echo err >&2; exit 3"], passthrough=True
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert "err" in result.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/operations/test_files.py tests/transport/test_runner.py -v`
Expected: FAIL — `run()` takes no `passthrough`, `Invocation` has no such field, the context has no `stdout_is_terminal`.

- [ ] **Step 3: Extend the runner**

In `src/ddev_sync/transport/runner.py`, add `passthrough: bool = False` to the `CommandRunner.run` protocol signature and to `SubprocessRunner.run`. In the implementation, before the capturing branch:

```python
            if passthrough:
                return self._run_passthrough(command, cwd, env)
```

and add:

```python
    def _run_passthrough(
        self,
        command: list[str],
        cwd: Path | None,
        env: Mapping[str, str] | None,
    ) -> CommandResult:
        """Let the child write to this process's stdout, keeping its stderr.

        Only stdout is inherited. rsync writes its `--info=progress2` output
        there and its errors to stderr, and `operations/files.py` quotes that
        stderr in its failure message - inheriting both would empty the one
        thing the error path needs. The same split the `stdout_path` mode
        above uses, with the terminal in place of the file.

        `CommandResult.stdout` is therefore empty: a caller that needs to read
        a command's stdout must not ask for passthrough.
        """
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=None,
            stderr=subprocess.PIPE,
            check=False,
        )
        return CommandResult(
            argv=tuple(command),
            returncode=completed.returncode,
            stdout="",
            stderr=completed.stderr.decode(errors="replace"),
        )
```

Note `stdout_path` and `passthrough` are mutually exclusive in practice — no caller passes both, and `stdout_path` is checked first, so it wins. Say that in the protocol's docstring.

- [ ] **Step 4: Extend the fake**

In `src/ddev_sync/transport/fake.py`, add `passthrough: bool` to `Invocation` and accept the parameter in `run`, recording it on the invocation. Give the field a default of `False` so the existing construction sites do not all have to change — or pass it explicitly if the fake builds `Invocation` in one place, which is cleaner.

- [ ] **Step 5: Carry the terminal fact**

In `src/ddev_sync/operations/base.py`, add to `ExecutionContext`:

```python
    # Decided at the CLI edge like the colour flag, never sniffed here: an
    # operation that called `sys.stdout.isatty()` itself would be untestable
    # without a pty, and would answer differently in the sidecar than on a
    # host.
    stdout_is_terminal: bool = False
```

In `src/ddev_sync/operations/files.py::execute`, replace the runner call:

```python
        result = context.runner.run(argv, passthrough=context.stdout_is_terminal)
```

Add a comment saying rsync already carries `--info=progress2` and that passthrough is what lets it be seen, and that stderr survives for the failure message below.

In `cli/commands.py::_execution_context`, add a `stdout_is_terminal: bool` parameter and pass it through; in `cli/main.py::_run`, supply `sys.stdout.isatty()` — alongside the colour decision already made there — and hand it to `run_command`, which passes it on.

- [ ] **Step 6: Run the tests, then the gate**

Run: `uv run pytest tests/operations/test_files.py tests/transport/test_runner.py -v`, then
`uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all pass at 100 % coverage.

- [ ] **Step 7: Commit**

Message: `feat(files): let rsync's progress reach the terminal`, with a body noting that `--info=progress2` was already being asked for and captured, that only stdout is inherited so the failure message keeps its stderr, and that the terminal check is made at the edge.

---

### Task 4: A blank line before every prompt

**Files:**
- Modify: `src/ddev_sync/cli/prompts.py` (`_select`, `_checkbox`)
- Modify: `src/ddev_sync/operations/base.py` (`confirm`)
- Test: `tests/cli/test_prompts.py`, `tests/operations/test_base.py`

**Interfaces:**
- Consumes: nothing from the other tasks. This task is independent and may be done at any point.
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

Add to `tests/cli/test_prompts.py`, following how that file already replaces `_select` and `_checkbox` and fakes a terminal:

```python
class _FakeQuestion:
    def unsafe_ask(self) -> str:
        return "production"


def test_a_picker_is_preceded_by_a_blank_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A question flush against the step line above it does not read as a
    # question; this is the separation the owner asked for. Driving `_select`
    # directly, because every other test in this file replaces it - here it is
    # the thing under test, so questionary is replaced instead.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "select", lambda *_a, **_k: _FakeQuestion())
    assert prompts._select("Which target?", ["production"]) == "production"
    assert capsys.readouterr().out == "\n"


def test_no_blank_line_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # With no terminal there is no question, so a stray newline would appear
    # in a piped log for nothing.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert prompts._select("Which target?", ["production"]) is None
    assert capsys.readouterr().out == ""
```

`sys` and `questionary` are already imported at the top of that file.

Add to `tests/operations/test_base.py`:

```python
def test_a_confirmation_is_preceded_by_a_blank_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The destructive confirmation is the prompt the owner saw flush against
    # the `db.import` line above it.
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert confirm("Continue?", subject="a destructive action") is True
    assert capsys.readouterr().out == "\n"
```

`monkeypatch.setattr("builtins.input", lambda _prompt: ...)` is exactly how
`test_confirm_reads_the_answer_from_stdin` in that file already does it.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_prompts.py tests/operations/test_base.py -v`
Expected: FAIL — nothing writes a blank line today.

- [ ] **Step 3: Add the blank lines**

In `src/ddev_sync/cli/prompts.py`, in both `_select` and `_checkbox`, after the `isatty` guard and before the questionary call:

```python
    # A blank line so the question separates from the step lines above it.
    # After the guard, not before: with no terminal there is no question, and
    # a stray newline would then appear in a piped log for no reason.
    print()
```

In `src/ddev_sync/operations/base.py::confirm`, inside the `try`, immediately before `input(...)`:

```python
        print()
```

with the same one-line reason. Place it inside the `try` so that a `KeyboardInterrupt` arriving between the newline and the prompt is still converted to `InterruptedAtPrompt`.

- [ ] **Step 4: Run the tests, then the gate**

Run: `uv run pytest tests/cli/test_prompts.py tests/operations/test_base.py -v`, then
`uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all pass at 100 % coverage. A golden CLI snapshot that now carries an extra blank line is expected; update it and report it.

- [ ] **Step 5: Commit**

Message: `feat(cli): separate every prompt from the output above it`, with a body noting that prompts come from two places — the pickers in `cli/prompts.py` and the destructive confirmation in `operations/base.py` — so fixing one would have left half the questions flush.

---

## Self-review

**Spec coverage.** Step lines and descriptions → Task 1. Verbosity levels → Task 2. Scrubbing and the secrets registry → Task 2. Progress → Task 3. Colour and the marker column → Task 1. Prompts → Task 4. Testing requirements are distributed across the tasks that create the behaviour. Nothing in the spec is unassigned.

**Type consistency.** `ConsoleReporter(write, *, colour: bool = False, verbose: int = 0)` — Task 1 adds `colour`, Task 2 adds `verbose`. `run_command(..., colour: bool = False, verbose: int = 0, stdout_is_terminal: bool = False)` — one parameter per task, all keyword-only with defaults, so each task's change compiles on its own. `ExecutionContext` gains `secrets` in Task 2 and `stdout_is_terminal` in Task 3, both with defaults. `CommandRunner.run` gains `passthrough` in Task 3; Task 2's `VerboseRunner` already forwards it, which is stated there.

**Ordering.** Task 2 depends on Task 1 (the reporter's shape). Tasks 3 and 4 depend on nothing, except that Task 3's `_execution_context` signature is the one Task 2 leaves behind. Run them in order 1, 2, 3, 4.
