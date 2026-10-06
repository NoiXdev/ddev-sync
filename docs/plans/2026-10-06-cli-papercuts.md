# Four CLI papercuts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `ddev sync` offering steps the project does not have, stop it accepting a step that would do nothing, answer `ddev sync help`, and end a Ctrl-C with a sentence instead of a traceback.

**Architecture:** All four are small and local. The step work rests on one observation: the planner is **pure** — `engine/plan.py` imports no runner, spawns nothing and reads no file — so "would this step do anything here?" is answered by building a throwaway plan and looking at which kinds came out. No second source of truth about what a project contains. The Ctrl-C work rests on a second observation: `Executor.run` already accumulates `outcomes` as it goes, and an interrupt today flies past that list and discards it; catching it where the list already lives needs no progress-tracking machinery at all.

**Tech Stack:** Python 3.13, click, pytest, uv.

**Where these came from:** a real session using the tool against `t3-13-ssg`. The step picker offered `s3` to a project with no S3; `ddev sync help` answered `Error: No such command 'help'`; Ctrl-C printed a traceback. A fourth report — that a missing target must abort every command that syncs — was measured and is **already true** (a bare sync, `doctor` and `build` all exit 1; `targets` exits 0, which is right for a listing), so it is not in this plan.

## Decisions already taken

These were settled before this plan; do not relitigate them.

1. **A step is available when it would contribute at least one plan entry.** Not a hand-written "does this project have S3" rule that can drift from the planner, and not a partial filter.
2. **An explicitly selected unavailable step is an error**, naming what is available. A sync that reports success having done nothing is the failure class this project keeps finding; a typo in `--steps` should not be silent.
3. **Ctrl-C names where it stopped.** Exit 130, and the message says which step last completed — above all when the local database has been replaced but not yet sanitised, because that is the only thing worth knowing at that moment.

## Global Constraints

- **`uv run pytest` must pass at 100 % line and branch coverage.** `# pragma: no cover` is **forbidden** — `exclude_lines` is deliberately empty, so a pragma cannot be used to reach the number.
- `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` (strict over `src`, `tests`, `tools`) must all be clean, **at the committed state**.
- **`subprocess` stays confined to `src/ddev_sync/transport/runner.py`**, enforced by an AST-parsing test.
- Tests verify real behaviour, not mocks. **TDD: the failing test first.**
- English throughout; comments explain *why*, in full sentences.
- Conventional Commits; every message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Git history is linear, fast-forward only. **Do not push.**

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/ddev_sync/engine/plan.py` | **Modify.** Add `available_selection_groups(...)`, which builds a throwaway plan and reports which groups produced work. It belongs here because it needs `build_plan`, and `plan.py` already imports `steps.py` — the reverse would be a cycle. |
| `src/ddev_sync/cli/commands.py` | **Modify.** `resolve_selection` takes the available set: filters the picker and `--yes`, rejects an unavailable explicit choice. |
| `src/ddev_sync/cli/main.py` | **Modify.** Pass the available set at the one call site; add the `help` command; map an interrupt to exit 130. |
| `src/ddev_sync/engine/executor.py` | **Modify.** Catch `KeyboardInterrupt` where `outcomes` already lives and raise a typed error carrying what completed. |
| `src/ddev_sync/errors.py` | **Modify.** `SyncInterrupted`. |

---

### Task 1: Only offer steps that would do something, and refuse the ones that would not

**Files:**
- Modify: `src/ddev_sync/engine/plan.py`
- Modify: `src/ddev_sync/cli/commands.py:111-130` (`resolve_selection`)
- Modify: `src/ddev_sync/cli/main.py:331`
- Test: `tests/engine/test_plan.py`, `tests/cli/test_commands.py`

**Interfaces:**
- Produces: `available_selection_groups(*, target, project, profile, merged, features=..., only_feature_setup=False) -> frozenset[str]` in `engine/plan.py`, and `resolve_selection(steps, assume_yes, *, available: frozenset[str]) -> list[str]`.

**What you are working with.** `SELECTION_GROUPS` (`engine/steps.py:39`) maps each group name to the `StepKind`s it selects: `db` → `(DB_DUMP, DB_DOWNLOAD, DB_IMPORT, ENV)`, `files` → `(FILES_SYNC,)`, `s3` → `(S3_SYNC,)`, `sanitize` → `(SANITIZE,)`, `setup` → `(SETUP,)`. `build_plan` returns a `Plan` whose `steps` is a tuple of `PlannedStep`, each carrying `kind`. `ALWAYS` adds `PREFLIGHT` regardless and belongs to no group.

**Use `any`, not `all`.** The `db` group names four kinds and a TYPO3 project legitimately produces no `ENV` entry (its profile sets `env: strategy: none`), while still dumping and importing. A group is available when **any** of its kinds appears.

- [ ] **Step 1: Write the failing test for the probe**

In `tests/engine/test_plan.py`. Follow the file's existing helpers for building a target, project, profile and merged config rather than inventing new ones — read the top of the file first.

```python
def test_a_group_is_available_when_it_would_produce_at_least_one_step() -> None:
    available = available_selection_groups(
        target=_target(), project=_project(), profile=_profile(), merged=_merged_with_files()
    )
    assert "db" in available
    assert "files" in available


def test_a_group_that_would_produce_nothing_is_not_offered() -> None:
    # No shipped profile configures S3, so a project that never mentions it
    # must not be asked about it - which is the report this task came from.
    available = available_selection_groups(
        target=_target(), project=_project(), profile=_profile(), merged=_merged_with_files()
    )
    assert "s3" not in available


def test_db_is_available_even_when_the_profile_writes_no_env_file() -> None:
    # `db` names four kinds and TYPO3 produces no ENV entry at all, so the
    # test is that availability asks "any of these", not "all of these".
    available = available_selection_groups(
        target=_target(), project=_project(), profile=_typo3_profile(), merged=_merged_without_env()
    )
    assert "db" in available


def test_every_name_it_returns_is_a_real_selection_group() -> None:
    available = available_selection_groups(
        target=_target(), project=_project(), profile=_profile(), merged=_merged_with_files()
    )
    assert available <= set(SELECTION_GROUPS)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/engine/test_plan.py -k available -v`
Expected: FAIL — `available_selection_groups` does not exist.

- [ ] **Step 3: Implement the probe**

In `src/ddev_sync/engine/plan.py`, beside `build_plan`:

```python
def available_selection_groups(
    *,
    target: ResolvedTarget,
    project: DdevProject,
    profile: ResolvedProfile,
    merged: MergeResult,
    features: FeatureResolution = _NO_FEATURES,
    only_feature_setup: bool = False,
) -> frozenset[str]:
    """Which selection groups would actually produce work for this project.

    Answered by building a throwaway plan with everything selected and
    seeing which kinds came out, rather than by a second set of rules about
    what a project contains. Building a plan is pure - nothing in this
    module runs a command or reads a file - so the probe costs nothing and
    cannot drift from what a real run would do, which a hand-written "does
    this project have S3" check eventually would.

    A group counts as available when *any* of its kinds appears, not all of
    them: `db` names four, and a profile that writes no env file (TYPO3's
    does not) still dumps and imports.
    """
    plan = build_plan(
        target=target,
        project=project,
        profile=profile,
        merged=merged,
        steps=steps_for_selection(sorted(SELECTION_GROUPS)),
        features=features,
        only_feature_setup=only_feature_setup,
    )
    produced = {step.kind for step in plan.steps}
    return frozenset(
        name for name, kinds in SELECTION_GROUPS.items() if any(kind in produced for kind in kinds)
    )
```

Import `SELECTION_GROUPS` and `steps_for_selection` from `engine.steps` if the module does not already have them.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/engine/test_plan.py -k available -v`
Expected: PASS, all four.

- [ ] **Step 5: Write the failing tests for the selection**

In `tests/cli/test_commands.py`:

```python
_AVAILABLE = frozenset({"db", "files", "sanitize", "setup"})


def test_an_explicit_step_that_would_do_nothing_here_is_refused() -> None:
    with pytest.raises(DdevSyncError) as error:
        resolve_selection("db,s3", assume_yes=True, available=_AVAILABLE)
    message = str(error.value)
    assert "s3" in message
    assert "db, files, sanitize, setup" in message


def test_an_unknown_step_name_still_says_it_is_unknown() -> None:
    # Distinct from the case above: "nosuchstep" is not a step at all, while
    # "s3" is a real one this project has no work for. The two deserve
    # different sentences, and conflating them would send someone hunting
    # for a typo they did not make.
    with pytest.raises((DdevSyncError, ValueError)) as error:
        resolve_selection("nosuchstep", assume_yes=True, available=_AVAILABLE)
    assert "nosuchstep" in str(error.value)


def test_assume_yes_selects_only_what_is_available() -> None:
    assert resolve_selection(None, assume_yes=True, available=_AVAILABLE) == [
        "db",
        "files",
        "sanitize",
        "setup",
    ]


def test_an_explicit_available_step_is_kept() -> None:
    assert resolve_selection("db", assume_yes=True, available=_AVAILABLE) == ["db"]
```

And the picker itself — the report this task came from was about what the menu showed:

```python
def test_the_picker_is_offered_only_the_available_names(monkeypatch) -> None:
    offered: list[list[str]] = []

    def record(available: list[str]) -> list[str]:
        offered.append(available)
        return ["db"]

    monkeypatch.setattr("ddev_sync.cli.commands.choose_steps", record)
    resolve_selection(None, assume_yes=False, available=_AVAILABLE)

    assert offered == [["db", "files", "sanitize", "setup"]]
```

Adjust the monkeypatch target if this file already has a way of driving `choose_steps` without a terminal — read it first and follow that rather than introducing a second style.

- [ ] **Step 6: Run them and watch them fail**

Run: `uv run pytest tests/cli/test_commands.py -k "step or selection" -v`
Expected: FAIL — `resolve_selection` takes no `available`.

- [ ] **Step 7: Implement the selection change**

In `src/ddev_sync/cli/commands.py`, give `resolve_selection` a keyword-only `available: frozenset[str]` and use it in all three branches: reject an explicit name that is not available, pass `sorted(available)` to `choose_steps`, and return `sorted(available)` for `--yes`.

Keep the existing unknown-name error where it is — `steps_for_selection` (`engine/steps.py:66-68`) already raises `ValueError(f"unknown step {name!r}; choose from: …")`, and an unavailable step is a different thing from a misspelled one. Your new message must say that the step is not available **for this project** and list what is, so the two cannot be confused.

Update the no-terminal message so the `--steps (…)` hint lists what is actually available rather than every group that exists.

- [ ] **Step 8: Pass it in at the call site**

`src/ddev_sync/cli/main.py:330-331` already builds the context before resolving the selection, so everything the probe needs is in hand:

```python
    context = _build_run_context(options)
    selection = resolve_selection(
        steps,
        assume_yes,
        available=available_selection_groups(
            target=context.target,
            project=context.project,
            profile=context.profile,
            merged=context.merged,
            features=context.features,
            only_feature_setup=options.only_feature_setup,
        ),
    )
```

**Check `build_cmd`'s path too.** It calls `_run` with `steps=_BUILD_STEPS` (`"setup"`), which is explicit, so it now goes through the unavailable check — and a project whose profile contributes no setup commands would start failing. Decide whether that is right (it is the same "you asked for something that would do nothing" rule) and say in your report what you concluded, with a test either way.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS at 100 % coverage. Existing tests calling `resolve_selection` with two arguments will fail to compile — update them, and say in your report how many you touched.

- [ ] **Step 10: Commit**

```bash
git add src/ddev_sync/engine/plan.py src/ddev_sync/cli/commands.py src/ddev_sync/cli/main.py tests/
git commit -F - <<'MSG'
feat(cli): offer only the steps this project would actually run

The picker listed `db`, `files`, `s3`, `sanitize` and `setup` for every
project, so a site with no S3 bucket was still asked about one, and `--yes`
selected it too. Availability now comes from the planner itself - build a
throwaway plan with everything selected and see which kinds come out -
rather than from a second set of rules about what a project contains, which
would eventually disagree with what a real run does.

Asking for a step explicitly that would produce nothing is now an error
naming what is available. A sync that reports success having done nothing is
the failure this project keeps finding, and a typo in `--steps` should not
be one of its causes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 2: `ddev sync help`

**Files:**
- Modify: `src/ddev_sync/cli/main.py`
- Test: `tests/cli/test_main.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: a `help` subcommand on the `cli` group.

**Why it is worth a task.** `ddev help` works for DDEV itself, so `ddev sync help` is the guess a user makes — and today it answers `Error: No such command 'help'.` while only `--help` works. The guess that fails is the one people make.

- [ ] **Step 1: Write the failing test**

In `tests/cli/test_main.py`, following the file's existing way of invoking the CLI:

```python
def test_help_is_a_command_as_well_as_a_flag() -> None:
    # `ddev help` works for DDEV itself, so `ddev sync help` is the guess a
    # user makes. It used to answer "No such command 'help'".
    result = invoke(["help"])
    assert result.exit_code == 0
    assert "doctor" in result.output
    assert "targets" in result.output


def test_help_the_command_and_help_the_flag_print_the_same_thing() -> None:
    assert invoke(["help"]).output == invoke(["--help"]).output
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/cli/test_main.py -k help -v`
Expected: FAIL — `No such command 'help'`.

- [ ] **Step 3: Add the command**

In `src/ddev_sync/cli/main.py`, beside the other subcommands:

```python
@cli.command("help")
@click.pass_context
def help_cmd(ctx: click.Context) -> int:
    """Show this help."""
    # `ddev help` works for DDEV itself, so `ddev sync help` is what a user
    # guesses. The group's help is what they want, not this command's, which
    # is why it reaches for the parent context.
    parent = ctx.parent
    click.echo((parent or ctx).get_help())
    return 0
```

`ctx.parent` is typed optional, so the `or ctx` is not decoration — mypy is strict here. Match the return convention of the other commands: they return an `int` exit code rather than `None`.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/cli/test_main.py -k help -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`

```bash
git add src/ddev_sync/cli/main.py tests/cli/test_main.py
git commit -F - <<'MSG'
feat(cli): answer `ddev sync help`

`ddev help` works for DDEV itself, so `ddev sync help` is the guess a user
makes - and it answered "No such command 'help'". Only `--help` worked,
which is the form they did not try.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 3: End a Ctrl-C with a sentence, not a traceback

**Files:**
- Modify: `src/ddev_sync/errors.py`
- Modify: `src/ddev_sync/engine/executor.py:61-85`
- Modify: `src/ddev_sync/cli/main.py:426-445` (`main`)
- Test: `tests/engine/test_executor.py`, `tests/cli/test_main.py`

**Interfaces:**
- Produces: `SyncInterrupted(DdevSyncError)` in `errors.py`.

**The observation this rests on.** `Executor.run` (`engine/executor.py:72-79`) already builds `outcomes` as it goes, appending a `StepOutcome(step, status)` per step. `_run_step` catches only `DdevSyncError`, so a `KeyboardInterrupt` raised inside a step propagates straight out of `run` and **that list is discarded**. Catching it in `run`, where the list is already in scope, is all the progress tracking this needs.

**What the message must say.** The last completed step, and — the part that matters — whether the local database has been replaced without being sanitised. An interrupt after `db.import` and before `sanitize` leaves unsanitised production data on the machine, which is the one fact worth knowing at that moment. There is precedent for the sentence: `ConsoleReporter.warn_unsanitized` already exists for the related case where `sanitize` was never selected.

- [ ] **Step 1: Write the failing tests**

In `tests/engine/test_executor.py`, using the file's existing fake operations and reporter:

```python
def test_an_interrupt_reports_the_last_step_that_finished() -> None:
    # The executor already knows: it has been appending outcomes as it goes,
    # and an interrupt used to fly past that list and discard it.
    executor = Executor(reporter=_recording_reporter())
    plan = _plan_with(steps=[_ok_step(StepKind.DB_DUMP), _interrupting_step(StepKind.DB_IMPORT)])

    with pytest.raises(SyncInterrupted) as error:
        executor.run(plan, _context())

    assert "db.dump" in str(error.value)


def test_an_interrupt_after_the_import_warns_the_database_is_unsanitised() -> None:
    executor = Executor(reporter=_recording_reporter())
    plan = _plan_with(
        steps=[_ok_step(StepKind.DB_IMPORT), _interrupting_step(StepKind.SANITIZE)]
    )

    with pytest.raises(SyncInterrupted) as error:
        executor.run(plan, _context())

    message = str(error.value)
    assert "not sanitised" in message or "not sanitized" in message


def test_an_interrupt_before_the_import_does_not_warn_about_data() -> None:
    # Nothing was replaced yet, so claiming production data is on the machine
    # would be false - and a warning that cries wolf is worse than none.
    executor = Executor(reporter=_recording_reporter())
    plan = _plan_with(steps=[_interrupting_step(StepKind.DB_DUMP)])

    with pytest.raises(SyncInterrupted) as error:
        executor.run(plan, _context())

    assert "sanit" not in str(error.value).lower()
```

Write `_interrupting_step` as a planned step whose operation raises `KeyboardInterrupt` — follow the file's existing way of injecting a failing operation and make this one raise `KeyboardInterrupt` instead of `DdevSyncError`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/engine/test_executor.py -k interrupt -v`
Expected: FAIL — `SyncInterrupted` does not exist, and the interrupt escapes.

- [ ] **Step 3: Add the error and catch it**

In `src/ddev_sync/errors.py`:

```python
class SyncInterrupted(DdevSyncError):
    """The user interrupted a sync part-way through.

    Carries what had already completed, because the state a half-finished
    sync leaves behind is the only thing worth saying at that moment.
    """
```

In `Executor.run`, wrap the step loop. `outcomes` is already in scope, which is the whole point:

```python
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
            raise SyncInterrupted(_interrupted_message(outcomes)) from interrupt
```

and beside the class:

```python
def _interrupted_message(outcomes: Sequence[StepOutcome]) -> str:
    """What an interrupt left behind, in the order someone needs to read it.

    The last completed step first, because that is the question. Then the
    state of the local database, but only when it is genuinely at risk: an
    interrupt after the import and before sanitising leaves production data
    on the machine, and a warning that fires when it did not would teach
    people to ignore it.
    """
    completed = [outcome.step.kind for outcome in outcomes if outcome.status == "ok"]
    where = f"after {completed[-1].value}" if completed else "before the first step finished"
    message = f"Interrupted {where}."
    if StepKind.DB_IMPORT in completed and StepKind.SANITIZE not in completed:
        message += (
            " The local database has been replaced with production data and was not"
            " sanitised - re-run with --steps sanitize, or treat this copy as production"
            " data until you have."
        )
    return message
```

Two details to get right rather than approximate. `post_sync` hooks run in `_run_post_sync` **after** the loop, so an interrupt skips them — if that matters for what your message claims, say only what is true. And a hook carries its parent's `kind` (see `PlannedStep`'s docstring), so an interrupted `post_db_import` hook would report `db.import` as completed; decide whether that is the right thing to say and note your reasoning in the report.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/engine/test_executor.py -k interrupt -v`
Expected: PASS, all three.

- [ ] **Step 5: Write the failing test for the exit code**

In `tests/cli/test_main.py`:

```python
def test_an_interrupt_exits_130_without_a_traceback(monkeypatch) -> None:
    # 130 is the conventional code for a process ended by SIGINT, and the
    # traceback it used to print told a user nothing they could act on.
    def interrupt(*_args: object, **_kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("ddev_sync.cli.main.cli.main", interrupt)
    assert main(["doctor"]) == 130
```

Adjust the monkeypatch target to whatever seam this file already uses for driving `main()`; the point is that a `KeyboardInterrupt` escaping the command must not reach the interpreter.

- [ ] **Step 6: Run it, watch it fail, then handle both cases in `main`**

Run: `uv run pytest tests/cli/test_main.py -k interrupt -v`
Expected: FAIL — the `KeyboardInterrupt` propagates.

In `main` (`src/ddev_sync/cli/main.py:426`), add two handlers **before** the existing `except DdevSyncError`, since `SyncInterrupted` is one:

```python
    except SyncInterrupted as error:
        click.echo(str(error), err=True)
        return 130
    except KeyboardInterrupt:
        # An interrupt outside a running sync - during the step picker, or
        # while the configuration is still being read. Nothing has been
        # changed, so there is nothing to report beyond the fact itself.
        click.echo("Interrupted.", err=True)
        return 130
```

Order matters: `SyncInterrupted` subclasses `DdevSyncError`, so a handler placed after it would return 1 and the exit code would be silently wrong.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS at 100 % coverage.

- [ ] **Step 8: Prove it by hand**

Start a real dry run against a target and press Ctrl-C. A dry run is safe — it changes nothing — and what you are checking is that no traceback appears. Paste what you saw. If the dry run finishes too quickly to interrupt, say so and interrupt at the step picker instead, which exercises the second handler.

- [ ] **Step 9: Commit**

```bash
git add src/ddev_sync/errors.py src/ddev_sync/engine/executor.py src/ddev_sync/cli/main.py tests/
git commit -F - <<'MSG'
fix(cli): end an interrupted sync with a sentence, not a traceback

Ctrl-C escaped to the interpreter and printed a stack trace, which tells a
user nothing they can act on. It now exits 130 and says which step last
finished - and, when the import completed but sanitising did not, that the
local database holds production data that was not sanitised. That is the one
thing worth knowing at that moment.

The executor already accumulated outcomes as it went; the interrupt simply
flew past the list and discarded it. Catching it where that list is already
in scope needed no progress tracking of its own.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 4: Say so in the documentation

**Files:**
- Modify: in `/Users/noidee/_dev/noix-docs` (branch `ddev-sync`): `src/content/docs/ddev-sync/reference/commands.md`, `src/content/docs/ddev-sync/guide/first-sync.md`

- [ ] **Step 1: Document the three behaviours**

In `reference/commands.md`: that `--steps` accepts only what the project has work for and names the rest as an error; that `help` exists as a command as well as a flag; and the exit code 130 beside the existing exit-code table.

In `guide/first-sync.md`'s "When something goes wrong": what an interrupted sync leaves behind, and that the message names the last completed step.

Read both pages first and match their voice — they explain *why* and state limits plainly rather than reassuring.

- [ ] **Step 2: Build the docs**

Run: `npm run build` in `/Users/noidee/_dev/noix-docs`
Expected: completes, link validator reports all internal links valid.

- [ ] **Step 3: Commit, in that repository**

```bash
cd /Users/noidee/_dev/noix-docs
git add src/content/docs/ddev-sync
git commit -F - <<'MSG'
docs(ddev-sync): steps you can pick, `help`, and what an interrupt leaves

`--steps` now takes only what a project has work for, and names the rest
rather than accepting a selection that would do nothing. `help` answers as a
command. And an interrupted sync exits 130 saying where it stopped, which
the troubleshooting section now explains - including that stopping between
the import and sanitising leaves production data on the machine.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Verification checklist

- [ ] The picker on a TYPO3 project offers `db`, `files`, `sanitize`, `setup` and **not** `s3`
- [ ] `--steps s3` on that project fails, naming what is available
- [ ] `--steps nosuchstep` still says the name is unknown, in different words
- [ ] `--yes` selects only available groups
- [ ] `ddev sync help` prints the same text as `ddev sync --help`
- [ ] Ctrl-C prints no traceback, exits 130, names the last completed step
- [ ] An interrupt between `db.import` and `sanitize` says the database holds unsanitised data; one before `db.import` does not
- [ ] `uv run pytest` at 100 % line and branch coverage; ruff, format and mypy clean
- [ ] `npm run build` clean in `noix-docs`
- [ ] Linear history, every commit Conventional with the footer, nothing pushed

## Not in this plan

- **The fourth report, that a missing target must abort.** Measured and already true.
- **`ddev sync init`**, which the "no targets" message still advises and which does not exist. It is the next plan, and §1 of `2026-10-05-ddev-sync-findings-from-the-matrix.md` specifies it.
- **Making `post_sync` hooks run after an interrupt.** They are cleanup, and an argument exists that cleanup should survive Ctrl-C — but running more code after someone has asked for it to stop is its own decision, and this plan only reports what did not happen.
