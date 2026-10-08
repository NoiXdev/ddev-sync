# `ddev sync init`, part B2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that a target's database credentials actually resolve — in `doctor` for every profile, in a real sync before the dump, and in `init` with an offer to fix what is wrong.

**Architecture:** The proof goes into `PreflightOperation`, not into the wizard, so it protects every run rather than the one where somebody used `init`; `doctor` grows a row and `init` inherits it by already ending with `doctor`. Two supporting changes make a fix expressible: `database.env_file` separates the remote credentials file from the local one `env.file` has always also meant, and the local layer moves into the directory the add-on already gitignores. `init` then asks only what a wizard can answer — where the file is on the server, and failing that, the credentials themselves, probed before they are written.

**Tech Stack:** Python 3.12, click, questionary, PyYAML, pydantic, uv, pytest.

**Spec:** `docs/specs/2026-10-08-sync-init-part-b2-design.md`. Read it. It carries the measurements and the reasoning; this plan carries the code.

## Global Constraints

- Tests: `uv run pytest`. Coverage gate is **100 % line and branch** (`--cov-fail-under=100`, `branch = true`); `# pragma: no cover` is **forbidden** — `exclude_lines` is deliberately empty, so a pragma cannot be used to reach the number. **Write no unreachable branch**; the fix is to restructure, not to add a pragma. On the B1 branch, four of eight tasks hit a branch their brief's tests did not reach and closed it with a test — expect the same.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools` must all be clean **at the committed state**. Ruff line length is 100; mypy is strict.
- `subprocess` may only be imported in `src/ddev_sync/transport/runner.py`; an AST-parsing test (`tests/test_subprocess_is_isolated.py`) enforces it. Every remote command in this plan goes through `context.runner.run(ssh_command(...))`.
- **`PreflightOperation.check` is documented never to raise.** Everything it calls must be wrapped. `doctor` renders its whole checklist only after `check()` returns, so one escaping exception costs the user every other row.
- **No credential value may reach the terminal.** `read_remote_credentials` registers the password in `context.secrets`, but a detail string is rendered directly by `render_doctor`, which does not scrub. Build detail strings from the non-secret fields only.
- **`init` writes the project file and at most one local layer — never the global file**, which the sidecar mounts read-only. This replaces parts A and B1's "exactly one file per run", which B2 retires deliberately (spec decision 5). Each write keeps its own proof and its own restore.
- Everything code-side is English: code, comments, docstrings, test names, commit messages. Every string `init` prints is English too; there is no i18n layer.
- Comments explain *why*, not *what*, and must be **true of the code beside them**. This project treats a comment that states something the adjacent code does not do as a defect in its own right, and the B1 branch's final review found three such defects in prose the branch itself had falsified. Read this plan's wording critically and report anything that does not hold rather than copying it.
- Conventional Commits. Every message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Write it to a file and use `git commit -F <file>`; never nest a heredoc inside another.
- Git history is linear, fast-forward only. **Do not push.**
- TDD: the failing test first, every time. The reviewer looks for RED/GREEN evidence.

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has its test assigned to the task that owns the code.

1. **A `files`-only sync must not be stopped for missing database credentials.** `execute()` refuses on a bad credentials probe, but a run that syncs no database never needs them, and refusing would break a working workflow for a configuration that is fine. (Task 2)
2. **The credentials detail must never carry the password.** `render_doctor` writes the detail straight to the terminal with no scrubbing, and the probe's own error strings interpolate remote stderr. Both the success detail and the failure detail have to be safe. (Task 1)
3. **`check()` must not raise through the new probe.** It is documented never to raise and `doctor` renders nothing until it returns, so an `OperationError` escaping the credentials probe costs the user the ssh, path, disk and sanitize rows as well. (Task 1)
4. **Both local-layer files present at once.** The loader must refuse and name both; just as important, `init` must never *create* that state by writing the new path beside an existing old one. A command cannot be allowed to produce the error it then refuses to resolve. (Tasks 4 and 6)
5. **Credentials that do not work must never be written.** The whole point of the override branch is that the probe runs first. A write-then-check order would leave a wrong password on disk whenever the probe fails. (Task 6)

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/ddev_sync/operations/preflight.py` | **Modify.** `PreflightReport` gains `credentials_ok`/`credentials_detail`; `check()` gains `_check_credentials`; `execute()` refuses when the database is selected; `_first_missing_table_problem` names `database.wp_table_prefix` for a `wp_cli` source. |
| `src/ddev_sync/cli/render.py` | **Modify.** One row, between `disk space` and `sanitize tables`. |
| `src/ddev_sync/cli/commands.py` | **Modify.** `doctor`'s exit code accounts for the new row. |
| `src/ddev_sync/engine/plan.py` | **Modify.** `db_selected` beside the existing `sanitize_selected` in the preflight step's params. |
| `src/ddev_sync/operations/base.py` | **Modify.** `ExecutionContext.setting` — the raw dotted read `config` and `config_list` each already contain a copy of. |
| `src/ddev_sync/operations/database.py` | **Modify.** `_env_candidates` reads `database.env_file` first; the candidate list becomes importable for `init`. |
| `src/ddev_sync/profiles/schema.py` | **Modify.** Validate `database.env_file`. |
| `src/ddev_sync/config/loader.py` | **Modify.** `ConfigPaths.legacy_local_config`, and the both-exist refusal. |
| `src/ddev_sync/cli/prompts.py` | **Modify.** Three questions: the remote env file, the credentials source, the five credential values. |
| `src/ddev_sync/cli/init.py` | **Modify.** The post-write question flow and the second write target. |
| `ddev-sync/.gitignore` | **Modify.** `/local.yaml`. |
| `README.md`, `ddev-sync/ddev-sync.example.yaml` | **Modify.** The new local path and the ignore advice that becomes false. |

---

### Task 1: The credentials row

**Files:**
- Modify: `src/ddev_sync/operations/preflight.py` (`PreflightReport`, `check`)
- Modify: `src/ddev_sync/cli/render.py` (`render_doctor`)
- Modify: `src/ddev_sync/cli/commands.py` (`doctor_command`'s return)
- Test: `tests/operations/test_preflight.py`, `tests/cli/test_render.py`, `tests/cli/test_commands.py`

**Interfaces:**
- Consumes: `read_remote_credentials` and `OperationError` (already imported in this module's neighbourhood — `preflight.py` already imports `RemoteCredentials` for `_show_tables_argv`).
- Produces: `PreflightReport.credentials_ok: bool | None`, `PreflightReport.credentials_detail: str`. Task 2 reads the same probe; Tasks 5 and 6 react to a `False` verdict.

**Context the task needs:** `_check_sanitize_tables` in the same file is the exact pattern to follow — it guards on connectivity, catches `OperationError`, and returns `(verdict, detail)` without ever raising. Read it and its docstring before writing anything.

`read_remote_credentials` is memoised on `context.cache`, so where a sanitize-table probe already runs this costs nothing: `_remote_tables` asks for the same credentials. The new cost is one ssh round trip in `doctor` for an `env` or `wp_cli` profile with no required table. `explicit` costs nothing at all — `_from_config` reads the merged configuration and makes no network call.

**The detail string is rendered unscrubbed.** `render_doctor` writes it straight to the terminal. Build the success detail from `user`, `host`, `port` and `name` only — never `password`. For the failure detail, pass the error through the existing `_clip` helper, as `_one_line_reason` does for ssh.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_credentials_row_reports_the_database_a_sync_would_dump(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    _script_env_file(runner, "DATABASE_NAME=app\nDATABASE_USER=deploy\n"
                             "DATABASE_PASSWORD=s3cret\nDATABASE_HOST=db.internal\n"
                             "DATABASE_PORT=3306\n")
    report = PreflightOperation().check(_context(runner, tmp_path))
    assert report.credentials_ok is True
    assert "deploy@db.internal:3306/app" in report.credentials_detail


def test_the_credentials_detail_never_carries_the_password(tmp_path: Path) -> None:
    # Review Focus 2: `render_doctor` writes this string to the terminal with
    # no scrubbing of its own.
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    _script_env_file(runner, "DATABASE_NAME=app\nDATABASE_USER=deploy\n"
                             "DATABASE_PASSWORD=s3cret\nDATABASE_HOST=db.internal\n"
                             "DATABASE_PORT=3306\n")
    report = PreflightOperation().check(_context(runner, tmp_path))
    assert "s3cret" not in report.credentials_detail


def test_a_credentials_failure_is_reported_not_raised(tmp_path: Path) -> None:
    # Review Focus 3: `check()` is documented never to raise, and `doctor`
    # renders nothing until it returns.
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    _script_env_file_missing(runner)
    report = PreflightOperation().check(_context(runner, tmp_path))
    assert report.credentials_ok is False
    assert "no environment file found" in report.credentials_detail
    # The other rows still arrived, which is the point of not raising.
    assert report.ssh_ok is True


def test_credentials_are_not_probed_when_ssh_failed(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _script_ssh_failure(runner)
    report = PreflightOperation().check(_context(runner, tmp_path))
    assert report.credentials_ok is None
    assert report.credentials_detail == ""


def test_an_explicit_source_needs_no_round_trip(tmp_path: Path) -> None:
    # `_from_config` reads the merged configuration; a `generic` project with
    # credentials set is green without touching the network.
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    report = PreflightOperation().check(
        _context(
            runner,
            tmp_path,
            database={
                "remote_credentials": "explicit",
                "credentials": {
                    "name": "app", "user": "deploy", "password": "s3cret",
                    "host": "db.internal", "port": "3306",
                },
            },
        )
    )
    assert report.credentials_ok is True
    assert "s3cret" not in report.credentials_detail


def test_an_explicit_source_with_nothing_set_is_red(tmp_path: Path) -> None:
    # The measured blind spot: `profiles/generic.yaml` defaults to `explicit`
    # and nothing writes `database.credentials`, and with `sanitize.rules: []`
    # no other probe ever reads them.
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    report = PreflightOperation().check(
        _context(runner, tmp_path, database={"remote_credentials": "explicit"})
    )
    assert report.credentials_ok is False
    assert "database.credentials" in report.credentials_detail
```

For `tests/cli/test_render.py`:

```python
def test_the_credentials_row_sits_between_disk_space_and_sanitize_tables() -> None:
    report = PreflightReport(
        ssh_ok=True,
        remote_path_ok=True,
        free_bytes=100 * 1024**3,
        credentials_ok=True,
        credentials_detail="env: deploy@db.internal:3306/app",
        sanitize_tables_ok=True,
        sanitize_tables_detail="1 required table present",
    )
    lines = render_doctor(report, TARGET).splitlines()
    labels = [line.split()[1] for line in lines if line.strip().startswith(("OK", "FAIL"))]
    assert labels.index("credentials") == labels.index("disk") + 1
    assert labels.index("credentials") < labels.index("sanitize")


def test_no_credentials_row_when_the_probe_did_not_run() -> None:
    report = PreflightReport(ssh_ok=False, remote_path_ok=False, free_bytes=0)
    assert "credentials" not in render_doctor(report, TARGET)
```

For `tests/cli/test_commands.py`:

```python
def test_doctor_exits_non_zero_on_failed_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda self, execution: PreflightReport(
            ssh_ok=True,
            remote_path_ok=True,
            free_bytes=100 * 1024**3,
            credentials_ok=False,
            credentials_detail="database.credentials is missing password",
        ),
    )
    assert doctor_command(_run_context(tmp_path), lambda text: None, runner=FakeCommandRunner()) == 1
```

Match each test file's own existing helper names and fixtures — `tests/operations/test_preflight.py` already has helpers for scripting the ssh probe, and `tests/cli/test_render.py` already has a `TARGET`-equivalent. The names above are illustrative of what to assert; use the file's own conventions for how to build the inputs.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/operations/test_preflight.py -k credentials -v`
Expected: FAIL with `TypeError: PreflightReport.__init__() got an unexpected keyword argument 'credentials_ok'`.

- [ ] **Step 3: Add the fields and the probe**

In `PreflightReport`, beside `sanitize_tables_ok`:

```python
    credentials_ok: bool | None = None
    credentials_detail: str = ""
```

and extend the class docstring: `credentials_ok` is `None` when the probe was not meaningfully run — connectivity already failed, so there is no remote to read from — and `True`/`False` when it ran. Say that the detail names the database a sync would dump and deliberately carries no password, because `render_doctor` writes it to the terminal unscrubbed.

Add the probe, beside `_check_sanitize_tables`:

```python
    @staticmethod
    def _check_credentials(
        context: ExecutionContext, report: PreflightReport
    ) -> tuple[bool | None, str]:
        """Whether the configured credential source actually yields credentials.

        The non-raising form, like `_check_sanitize_tables` above: `check()` is
        documented never to raise, and `doctor` renders its whole checklist
        only after `check()` returns, so an `OperationError` escaping here
        would cost the user the ssh, deploy-path and disk-space rows too.

        Guarded on connectivity for the same reason the table probe is. An
        `explicit` source could technically be judged without ssh - it reads
        the merged configuration and makes no network call - but a green
        credentials row under a red ssh row sends the reader at the wrong
        problem, and diagnosis here is sequential.

        This is the one probe that runs for *every* profile. The table probe
        returns early when no rule requires a table, which left
        `profiles/generic.yaml` - `sanitize.rules: []` and
        `database.remote_credentials: explicit` - with a green `doctor` and a
        sync that died at `db.dump`.

        The detail is built from the non-secret fields only. `render_doctor`
        writes it straight to the terminal and does no scrubbing of its own;
        `context.secrets` protects the verbose command log, not this string.
        """
        if not (report.ssh_ok and report.remote_path_ok):
            return None, ""
        source = context.config("database.remote_credentials", default="env")
        try:
            credentials = read_remote_credentials(context)
        except OperationError as error:
            return False, _clip(str(error))
        return True, (
            f"{source}: {credentials.user}@{credentials.host}:"
            f"{credentials.port}/{credentials.name}"
        )
```

Call it in `check()`, beside the others, and pass both new values into the `PreflightReport(...)` it returns:

```python
        credentials_ok, credentials_detail = self._check_credentials(context, report)
```

`read_remote_credentials` is **already imported** at `preflight.py:35`, beside `RemoteCredentials` which `_show_tables_argv` uses — add no import.

**Ordering inside `check()` matters for cost, not correctness:** run `_check_credentials` *before* `_check_sanitize_tables`, so the memoised credentials are already in `context.cache` when the table probe asks for them. Either order works; this one makes the shared read obvious to a reader.

- [ ] **Step 4: Render the row and fail the exit code on it**

In `render.py`, after the `disk space` entry and before the `sanitize tables` block:

```python
    if report.credentials_ok is not None:
        checks.append(("credentials", report.credentials_ok, report.credentials_detail))
```

Extend `render_doctor`'s docstring: the credentials row joins the list of rows included only when their `*_ok` is not `None`.

In `commands.py`, add to `doctor_command`'s return expression, keeping the existing `is not False` shape and its comment's reasoning:

```python
        and report.credentials_ok is not False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/operations/test_preflight.py tests/cli/test_render.py tests/cli/test_commands.py -v`
Expected: PASS, including every pre-existing test in all three files. A pre-existing test that now needs editing means the change altered behaviour it pinned — report it rather than editing it.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`
Expected: all clean, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/operations/preflight.py src/ddev_sync/cli/render.py src/ddev_sync/cli/commands.py tests/
cat > /tmp/msg <<'EOF'
feat(doctor): report whether the credentials actually resolve

The table probe reads credentials only when a sanitize rule requires a
table, so `profiles/generic.yaml` - no rules, and `explicit` credentials
nobody writes - got a green `doctor` and then died at `db.dump`. The probe
now runs for every profile, and the row names the database a sync would
dump without carrying its password.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 2: Refuse before the dump, and name the key that fixes a missing table

**Files:**
- Modify: `src/ddev_sync/engine/plan.py` (`__init__`'s selection state, `_preflight`)
- Modify: `src/ddev_sync/operations/preflight.py` (`execute`, `_first_missing_table_problem`)
- Test: `tests/engine/test_plan.py`, `tests/operations/test_preflight.py`

**Interfaces:**
- Consumes: `_check_credentials` (Task 1).
- Produces: the preflight step's params gain `db_selected: bool`. Nothing later depends on it.

**Context the task needs:** `engine/plan.py` already computes `self._sanitize_selected = StepKind.SANITIZE in steps` in `__init__` and passes it as `params={"sanitize_selected": self._sanitize_selected}` in `_preflight`. The operation reads it from its own params because `engine/executor.py` only ever passes a step's own params, and the operation never sees the Plan. Follow that shape exactly.

`StepKind.DB_DUMP` is the right marker for "this run touches the database": `SELECTION_GROUPS["db"]` is `(DB_DUMP, DB_DOWNLOAD, DB_IMPORT, ENV)`, and the dump is the step that reads credentials.

**Why the refusal is conditional.** A `files`-only sync needs no database credentials. Refusing it for lacking them would break a working workflow over a configuration that is irrelevant to it — Review Focus 1.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_preflight_step_knows_whether_the_database_is_selected() -> None:
    plan = build_plan(_context(), ["db", "files"])
    preflight = next(step for step in plan.steps if step.kind is StepKind.PREFLIGHT)
    assert preflight.params["db_selected"] is True


def test_a_files_only_run_marks_the_database_unselected() -> None:
    plan = build_plan(_context(), ["files"])
    preflight = next(step for step in plan.steps if step.kind is StepKind.PREFLIGHT)
    assert preflight.params["db_selected"] is False
```

```python
def test_execute_refuses_a_database_run_whose_credentials_do_not_resolve(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    _script_env_file_missing(runner)
    with pytest.raises(OperationError) as error:
        PreflightOperation().execute(
            _context(runner, tmp_path), {"db_selected": True, "sanitize_selected": False}
        )
    assert "no environment file found" in str(error.value)


def test_execute_lets_a_files_only_run_through_without_credentials(tmp_path: Path) -> None:
    # Review Focus 1: this run never reads the database, so a credentials
    # problem is none of its business.
    runner = FakeCommandRunner()
    _script_ok_probe(runner)
    _script_env_file_missing(runner)
    PreflightOperation().execute(
        _context(runner, tmp_path), {"db_selected": False, "sanitize_selected": False}
    )


def test_a_missing_table_names_the_prefix_override_for_a_wp_cli_source(tmp_path: Path) -> None:
    # The one sentence §8 item 3 still needed: on an env-based WordPress setup
    # the prefix is what is wrong, and `database.wp_table_prefix` is what fixes
    # it. The old message said only "override it in .ddev/ddev-sync.yaml".
    problem = _first_missing_table_problem(
        _context_with_source(tmp_path, "wp_cli"), [("anonymize-users", "mxwp_users")], {"wp_users"}
    )
    assert "database.wp_table_prefix" in problem


def test_a_missing_table_does_not_name_the_prefix_override_otherwise(tmp_path: Path) -> None:
    problem = _first_missing_table_problem(
        _context_with_source(tmp_path, "env"), [("anonymize-users", "users_field_data")], set()
    )
    assert "database.wp_table_prefix" not in problem
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_plan.py -k db_selected tests/operations/test_preflight.py -k "execute_refuses or files_only or prefix_override" -v`
Expected: FAIL — `KeyError: 'db_selected'` for the plan tests, and no refusal for the execute tests.

- [ ] **Step 3: Pass the selection through the plan**

In `engine/plan.py`'s `__init__`, beside `self._sanitize_selected`:

```python
        # Read from the operation's own params for the same reason
        # `_sanitize_selected` is: the operation never sees the Plan.
        self._db_selected = StepKind.DB_DUMP in steps
```

and in `_preflight`'s returned entry:

```python
                params={
                    "sanitize_selected": self._sanitize_selected,
                    "db_selected": self._db_selected,
                },
```

- [ ] **Step 4: Refuse in `execute`, and name the key**

In `PreflightOperation.execute`, after the free-space check and before the sanitize probe — credentials are a precondition of the table probe, so they are checked first:

```python
        if bool(params.get("db_selected", False)):
            # Reported rather than raised by `check()`; here it must stop the
            # run, because everything after this reads the database. Gated on
            # the selection: a `files`-only sync never reads credentials, and
            # refusing it over them would break a run that was going to work.
            ok, detail = self._check_credentials(context, report)
            if ok is False:
                raise OperationError(detail)
```

In `_first_missing_table_problem`, extend the "does not exist" branch. Keep the existing sentence and add one:

```python
        remedy = (
            " This project reads its credentials with 'wp_cli', so the table "
            "name came from WordPress's own `$table_prefix` - if that is what "
            "is wrong, set 'database.wp_table_prefix' in .ddev/ddev-sync.yaml "
            "to the prefix production really uses."
            if context.config("database.remote_credentials", default="env") == "wp_cli"
            else ""
        )
```

and append `remedy` to the returned message. Leave the letter-case branch alone — there the table exists and the prefix is not the problem.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/ tests/operations/test_preflight.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/engine/plan.py src/ddev_sync/operations/preflight.py tests/
cat > /tmp/msg <<'EOF'
fix(preflight): stop a database run whose credentials do not resolve

Reported by `doctor` since the previous commit; here it has to stop the run,
because every step after preflight reads the database. Gated on the database
actually being selected - a `files`-only sync needs no credentials and must
not be refused over them.

A missing sanitize table now also names `database.wp_table_prefix` when the
source is `wp_cli`, which is the key that fixes it on an env-based WordPress
install; the message used to say only "override it in .ddev/ddev-sync.yaml".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 3: `database.env_file`

**Files:**
- Modify: `src/ddev_sync/operations/base.py` (`ExecutionContext`: add `setting`, express `config`/`config_list` on it)
- Modify: `src/ddev_sync/operations/database.py` (`_env_candidates`)
- Modify: `src/ddev_sync/profiles/schema.py` (`_validate_database`)
- Test: `tests/operations/test_db_credentials.py` (or whichever file covers `_from_remote_env` — find it with `rg _env_candidates tests/`), `tests/profiles/test_schema.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ExecutionContext.setting(path: str) -> Any`; `remote_env_candidates(context) -> list[str]` — `_env_candidates` renamed and made public, returning the *configured names* (not the joined remote paths). Task 5 imports it to tell the user what was looked for and to decide whether a question is needed. `DATABASE_ENV_FILE_KEY = "database.env_file"`.

**Context the task needs:** read the spec's "`database.env_file`, and why the fallback runs this way". The short version: `env.file` has always meant two things — `operations/database.py::_env_candidates` joins it onto `ResolvedTarget.data_root` to find the credentials file **on the remote**, while `operations/env.py::_find_env_file` matches it against the project root's entries to find the file to rewrite **locally**. The deployer layout's directory difference is handled by `data_root`; the filename is not.

The fallback to `env.file` is kept deliberately. A project that set `env.file` in order to steer the remote lookup would otherwise break, and this plan does not break a working configuration to tidy a key name.

**Do not touch `operations/env.py`.** The local side keeps reading `env.file` and only `env.file`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_remote_env_file_key_wins_over_env_file(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(),
        tmp_path,
        env={"file": [".env.ddev"]},
        database={"env_file": [".env"]},
    )
    assert remote_env_candidates(context) == [".env"]


def test_env_file_is_the_fallback(tmp_path: Path) -> None:
    # Kept deliberately: a project that set `env.file` to steer the *remote*
    # lookup must keep working.
    context = make_context(FakeCommandRunner(), tmp_path, env={"file": [".env.ddev"]})
    assert remote_env_candidates(context) == [".env.ddev"]


def test_the_default_is_the_last_resort(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert remote_env_candidates(context) == [".env.local", ".env"]


def test_a_single_name_is_accepted_as_a_string(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path, database={"env_file": ".env.prod"})
    assert remote_env_candidates(context) == [".env.prod"]


def test_the_remote_lookup_follows_the_new_key(tmp_path: Path) -> None:
    # The behaviour the key exists for: the local file is named one thing and
    # the remote's is named another, and neither breaks the other.
    runner = FakeCommandRunner()
    _script_env_file(runner, "DATABASE_NAME=app\nDATABASE_USER=deploy\n"
                             "DATABASE_PASSWORD=s3cret\nDATABASE_HOST=db.internal\n"
                             "DATABASE_PORT=3306\n")
    context = make_context(
        runner, tmp_path, env={"file": [".env.ddev"]}, database={"env_file": [".env"]}
    )
    assert read_remote_credentials(context).name == "app"
    assert ".env'" in runner.commands[-1][-1] or "/.env" in runner.commands[-1][-1]
```

For the schema:

```python
def test_a_non_list_env_file_is_rejected() -> None:
    with pytest.raises(ProfileError) as error:
        validate_profile({"name": "x", "database": {"env_file": 7}})
    assert "database.env_file" in str(error.value)


def test_a_string_env_file_is_accepted() -> None:
    validate_profile({"name": "x", "database": {"env_file": ".env"}})


def test_a_list_env_file_is_accepted() -> None:
    validate_profile({"name": "x", "database": {"env_file": [".env.local", ".env"]}})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -k "remote_env_candidates or env_file" -v`
Expected: FAIL with `ImportError: cannot import name 'remote_env_candidates'`.

- [ ] **Step 3: Implement the key and the fallback**

Rename `_env_candidates` to `remote_env_candidates`, update its one call site in `_from_remote_env`, and give it the new chain:

```python
DATABASE_ENV_FILE_KEY = "database.env_file"

_DEFAULT_ENV_FILES = (".env.local", ".env")


def remote_env_candidates(context: ExecutionContext) -> list[str]:
    """The file names to look for on the remote, strongest source first.

    `database.env_file` names the file *on the target*; `env.file` is the
    local project's own, and has historically meant both - this function and
    `operations/env.py::_find_env_file` read the same key, one joining it onto
    `ResolvedTarget.data_root` and the other matching it against the project
    root's entries. The deployer layout's directory difference is handled by
    `data_root`; the filename was not, and a project whose local and remote
    files are named differently had no way to say so.

    The fallback to `env.file` is deliberate rather than tidy: a project that
    set it in order to steer *this* lookup keeps working untouched. Only a
    project that needs the two to differ has to learn the new key.

    Public, unlike the `_`-prefixed function it replaces, because
    `cli/init.py` needs to tell the user which names were looked for.
    """
    for key in (DATABASE_ENV_FILE_KEY, "env.file"):
        configured = context.setting(key)
        if isinstance(configured, str):
            return [configured]
        if isinstance(configured, list):
            return [str(entry) for entry in configured]
    return list(_DEFAULT_ENV_FILES)
```

`context.setting` does not exist yet. Add it to `ExecutionContext` in `src/ddev_sync/operations/base.py`, beside `config` and `config_list`, which already each carry their own copy of the same walk:

```python
    def setting(self, path: str) -> Any:
        """The raw value at a dotted path in the merged config, or `None`.

        `config` and `config_list` are this walk plus a coercion - a string
        with a default, and a list of mappings. A caller that needs to tell a
        string from a list, as `remote_env_candidates` does, can use neither,
        and a third private copy of the walk is how three readers of one
        default come to disagree (see `operations/env.py:146`).
        """
        node: Any = self.merged.data
        for part in path.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node
```

Then express the two existing methods in terms of it, so there is one walk rather than three:

```python
    def config(self, path: str, *, default: str) -> str:
        """Read a dotted path from the merged config, or return `default`."""
        value = self.setting(path)
        return default if value is None else str(value)

    def config_list(self, path: str) -> list[dict[str, Any]]:
        """Read a dotted path expected to hold a list of mappings."""
        value = self.setting(path)
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, dict)]
```

Both have existing tests; a behaviour change shows there immediately. **If any existing test fails, the rewrite is wrong — restore the original bodies and keep `setting` additive.**

In `profiles/schema.py`'s `_validate_database`, beside the `remote_credentials` check:

```python
    env_file = database.get("env_file")
    if env_file is not None and not isinstance(env_file, str | list):
        raise ProfileError(
            f"database.env_file must be a file name or a list of file names, got {env_file!r}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/operations/ tests/profiles/ -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/operations/database.py src/ddev_sync/profiles/schema.py tests/
cat > /tmp/msg <<'EOF'
feat(config): name the remote env file apart from the local one

`env.file` meant two things: the file to read credentials from on the target,
and the file to rewrite in the local project. Where both are `.env` nothing
showed; where they differ there was no way to say so, and `init` learned to
write the key for the local side in part B1.

`database.env_file` names the remote one and falls back to `env.file`, so no
existing configuration changes. It sits beside `database.env_map`, which
already describes the same remote file.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 4: The local layer moves

**Files:**
- Modify: `src/ddev_sync/config/loader.py` (`ConfigPaths`, `discover_config_paths`, `load_user_layers`)
- Modify: `ddev-sync/.gitignore`
- Test: `tests/config/test_loader.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ConfigPaths.legacy_local_config: Path`; `LOCAL_LABEL` now names `.ddev/ddev-sync/local.yaml` and `LEGACY_LOCAL_LABEL` names `.ddev/ddev-sync.local.yaml`. Task 6 reads both to decide which file to write.

**Context the task needs:** the add-on ships `ddev-sync/.gitignore` through `install.yaml`'s `project_files`, installed at `.ddev/ddev-sync/.gitignore`. It carries `#ddev-generated` on its first line, so it arrives with every install and update. Its current entries, `/passwd*` and `/home/`, cover that directory's contents — which is exactly why a local layer placed *inside* that directory can be ignored by it, and a sibling one level up cannot.

**Both files present is an error, not a precedence rule.** This codebase already refuses to guess when two layers define the same target (`cli/init.py` raises and names both files). Quietly preferring one copy of a credentials file over another is the same class of mistake with worse consequences.

The local layer stays the **strongest** layer either way — only the file supplying it changes.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_new_local_path_is_read(tmp_path: Path) -> None:
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    paths.local_config.parent.mkdir(parents=True, exist_ok=True)
    paths.local_config.write_text("version: 1\ndatabase:\n  credentials:\n    user: a\n")
    layers = load_user_layers(paths)
    assert [layer.label for layer in layers] == [LOCAL_LABEL]


def test_the_old_local_path_is_still_read(tmp_path: Path) -> None:
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    paths.legacy_local_config.parent.mkdir(parents=True, exist_ok=True)
    paths.legacy_local_config.write_text("version: 1\ndatabase:\n  credentials:\n    user: a\n")
    layers = load_user_layers(paths)
    assert [layer.label for layer in layers] == [LEGACY_LOCAL_LABEL]


def test_both_local_paths_at_once_is_an_error_naming_both(tmp_path: Path) -> None:
    # Review Focus 4. Guessing which copy of a credentials file wins is the
    # one outcome worse than refusing.
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    paths.local_config.parent.mkdir(parents=True, exist_ok=True)
    paths.local_config.write_text("version: 1\n")
    paths.legacy_local_config.write_text("version: 1\n")
    with pytest.raises(ConfigError) as error:
        load_user_layers(paths)
    assert LOCAL_LABEL in str(error.value)
    assert LEGACY_LOCAL_LABEL in str(error.value)


def test_the_local_layer_is_still_the_strongest(tmp_path: Path) -> None:
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    paths.project_config.parent.mkdir(parents=True, exist_ok=True)
    paths.project_config.write_text("version: 1\ndefaults:\n  keep_dump: false\n")
    paths.local_config.parent.mkdir(parents=True, exist_ok=True)
    paths.local_config.write_text("version: 1\ndefaults:\n  keep_dump: true\n")
    assert merge_layers(load_user_layers(paths)).data["defaults"]["keep_dump"] is True


def test_the_old_path_is_also_the_strongest(tmp_path: Path) -> None:
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    paths.project_config.parent.mkdir(parents=True, exist_ok=True)
    paths.project_config.write_text("version: 1\ndefaults:\n  keep_dump: false\n")
    paths.legacy_local_config.write_text("version: 1\ndefaults:\n  keep_dump: true\n")
    assert merge_layers(load_user_layers(paths)).data["defaults"]["keep_dump"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/config/test_loader.py -k local -v`
Expected: FAIL with `AttributeError: 'ConfigPaths' object has no attribute 'legacy_local_config'`.

- [ ] **Step 3: Implement both paths**

```python
LOCAL_LABEL = ".ddev/ddev-sync/local.yaml"
LEGACY_LOCAL_LABEL = ".ddev/ddev-sync.local.yaml"
```

```python
@dataclass(frozen=True)
class ConfigPaths:
    """Where the user-editable layers live for one project.

    There are two local paths, not two local layers: `local_config` is where
    the local layer lives now, inside the directory the add-on ships a
    `.gitignore` for, and `legacy_local_config` is where it used to live, one
    level up, where nothing ignored it. Whichever exists supplies the same
    single layer, in the same position.
    """

    global_config: Path
    project_config: Path
    local_config: Path
    legacy_local_config: Path
```

```python
def discover_config_paths(project_root: Path, home: Path) -> ConfigPaths:
    """Locate the user layers. Existence is not checked here."""
    return ConfigPaths(
        global_config=home / ".ddev" / "ddev-sync" / "config.yaml",
        project_config=project_root / ".ddev" / "ddev-sync.yaml",
        local_config=project_root / ".ddev" / "ddev-sync" / "local.yaml",
        legacy_local_config=project_root / ".ddev" / "ddev-sync.local.yaml",
    )
```

```python
def load_user_layers(paths: ConfigPaths) -> list[LayerDoc]:
    """Read the user layers that exist, weakest first."""
    local, local_label = _local_layer(paths)
    candidates = [
        (paths.global_config, GLOBAL_LABEL),
        (paths.project_config, PROJECT_LABEL),
    ]
    if local is not None:
        candidates.append((local, local_label))
    return [
        LayerDoc(label=label, data=_read_config_file(path, label))
        for path, label in candidates
        if path.is_file()
    ]


def _local_layer(paths: ConfigPaths) -> tuple[Path | None, str]:
    """Which file supplies the local layer, if either.

    Refusing when both exist rather than ranking them: this file carries
    credentials, and silently reading one copy while the user edits the other
    is the kind of failure that looks like the password being wrong. The
    engine already refuses to guess when two layers define the same target
    (`cli/init.py`), and this is that rule with more at stake.
    """
    new = paths.local_config.is_file()
    old = paths.legacy_local_config.is_file()
    if new and old:
        raise ConfigError(
            f"both {LOCAL_LABEL} and {LEGACY_LOCAL_LABEL} exist, and only one of them "
            f"can be the local layer. Move what you need into {LOCAL_LABEL} - the "
            "add-on ignores that path for you - and delete the other."
        )
    if old:
        return paths.legacy_local_config, LEGACY_LOCAL_LABEL
    return paths.local_config, LOCAL_LABEL
```

Note the returned path is still filtered by `is_file()` in the comprehension, so the "neither exists" case needs no special handling.

Add to `ddev-sync/.gitignore`, keeping its `#ddev-generated` first line and matching its comment style:

```
# `ddev sync init` writes the local configuration layer here - database
# credentials among other things - so it is ignored for the same reason the
# generated files above are: it belongs to this machine, not to the project.
/local.yaml
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/config/ tests/cli/ -v`
Expected: PASS. `tests/config/test_loader.py:20` constructs a `ConfigPaths` by hand and will need the new field — that is a constructor change, not a weakened assertion, and is the one acceptable edit here.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/config/loader.py ddev-sync/.gitignore tests/
cat > /tmp/msg <<'EOF'
feat(config): move the local layer where the add-on already ignores it

`.ddev/ddev-sync.local.yaml` holds database credentials and nothing ignored
it: the add-on's own `.gitignore` sits inside `.ddev/ddev-sync/` and covers
that directory, not a sibling one level up, and DDEV rewrites `.ddev/.gitignore`
on start. The layer moves into that directory and the shipped ignore gains a
line for it, so the protection arrives with every install and update.

The old path is still read. Both at once is an error naming both, because
quietly preferring one copy of a credentials file is worse than refusing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 5: `init` asks where the env file is on the server

**Files:**
- Modify: `src/ddev_sync/cli/prompts.py`
- Modify: `src/ddev_sync/cli/init.py`
- Test: `tests/cli/test_init.py`, `tests/cli/test_prompts.py`

**Interfaces:**
- Consumes: `PreflightOperation._check_credentials` (Task 1); `remote_env_candidates`, `DATABASE_ENV_FILE_KEY` (Task 3); `write_settings` and `ConfigPaths` (part B1 and Task 4).
- Produces: `choose_remote_env_file(looked_for: list[str], present: list[str]) -> str | None` and `REMOTE_ENV_NONE: str` in `prompts.py`; in `init.py`, `_fix_credentials(execution, paths, wordpress, echo) -> None` and `_offer_remote_env_file(execution, paths, wordpress, echo) -> None`. `wordpress` is threaded through from the start even though only Task 6 reads it, so that task adds a branch rather than changing three signatures.

**Context the task needs:** these questions run **after** the target has been written, because `read_remote_credentials` needs an `ExecutionContext` and that needs a resolved target. Part A's `init_command` already builds a `RunContext` via `build_context` and calls `doctor_command`; the credentials fix slots in before that call, so a fix is written and then `doctor` reports on the fixed configuration.

`cli/commands.py::_execution_context` is how a `RunContext` becomes an `ExecutionContext`; it is module-private there. Either import it or build the `ExecutionContext` the same way — read it first and match whichever the codebase makes natural.

Only a failure a question can fix gets a question. A red credentials row caused by ssh being down is not one of those; ssh has its own row and its own fix.

**Listing the remote directory.** One `ls` over the connection preflight has already proved, through `ssh_command` and `context.runner.run` — never `subprocess`. Build it to list the entries of `context.target.data_root`, filter to names starting with `.env` in Python rather than with a shell glob, so a directory with no matches does not produce an unexpanded pattern.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_writes_the_remote_env_file_the_user_names(project, project_file, tmp_path, monkeypatch) -> None:
    _laravel(project)
    runner = _runner_where_the_remote_env_is(".env.production")
    script(monkeypatch, remote_env_file=(".env.production",))
    assert invoke(project, tmp_path, runner=runner) == 0
    loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert loaded["database"]["env_file"] == [".env.production"]


def test_init_does_not_ask_when_the_credentials_already_resolve(project, tmp_path, monkeypatch) -> None:
    _laravel(project)
    runner = _runner_where_the_remote_env_is(".env")
    script(monkeypatch, remote_env_file=())  # asking would exhaust the empty queue
    assert invoke(project, tmp_path, runner=runner) == 0


def test_init_offers_the_env_files_that_are_on_the_server(project, tmp_path, monkeypatch) -> None:
    _laravel(project)
    runner = _runner_whose_remote_listing_is([".env.production", ".env.example", "composer.json"])
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        init,
        "choose_remote_env_file",
        lambda looked_for, present: seen.update(looked_for=looked_for, present=present)
        or REMOTE_ENV_NONE,
    )
    invoke(project, tmp_path, runner=runner)
    assert seen["looked_for"] == [".env.local", ".env"]
    assert seen["present"] == [".env.example", ".env.production"]


def test_init_does_not_ask_when_ssh_itself_is_down(project, tmp_path, monkeypatch) -> None:
    # A red credentials row whose cause is connectivity is not a question's
    # to fix; the ssh row already says so.
    _laravel(project)
    runner = _runner_whose_ssh_fails()
    monkeypatch.setattr(init, "choose_remote_env_file", _never_called)
    assert invoke(project, tmp_path, runner=runner) == 1


def test_init_writes_nothing_when_no_remote_file_is_chosen(project, project_file, tmp_path, monkeypatch, capsys) -> None:
    _laravel(project)
    runner = _runner_whose_remote_listing_is([])
    script(monkeypatch, remote_env_file=(REMOTE_ENV_NONE,))
    invoke(project, tmp_path, runner=runner)
    assert "database" not in yaml.safe_load(project_file.read_text(encoding="utf-8"))
```

For `tests/cli/test_prompts.py`:

```python
def test_the_remote_env_question_offers_what_is_there_plus_an_opt_out(monkeypatch) -> None:
    asked: dict[str, object] = {}
    monkeypatch.setattr(
        prompts, "_select", lambda question, choices: asked.update(q=question, c=choices) or choices[0]
    )
    assert prompts.choose_remote_env_file([".env"], [".env.production"]) == ".env.production"
    assert asked["c"] == [".env.production", REMOTE_ENV_NONE]
    assert ".env" in str(asked["q"])
```

Build the runner helpers to match `tests/cli/test_init.py`'s existing `FakeCommandRunner` usage; the file already scripts ssh probes for the `doctor` call part A makes.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/cli/test_init.py -k remote_env -v`
Expected: FAIL with `AttributeError: module 'ddev_sync.cli.init' has no attribute 'choose_remote_env_file'`.

- [ ] **Step 3: Add the prompt**

```python
REMOTE_ENV_NONE = "None of these - I will fix it another way"


def choose_remote_env_file(looked_for: list[str], present: list[str]) -> str | None:
    """Ask which file on the target holds the database credentials.

    `present` is what the target's data root really contains, so the usual
    cause of this question - the file is there under another name - is one
    keystroke away. The opt-out is last because the override branch behind it
    is the heavier answer, and most projects do not need it.
    """
    return _select(
        f"No credentials file found on the target (looked for {', '.join(looked_for)}). "
        "Which file is it?",
        [*present, REMOTE_ENV_NONE],
    )
```

- [ ] **Step 4: Wire the fix into `init`**

Add to `init.py`, called after the target and settings writes and **before** `doctor_command`:

```python
def _fix_credentials(
    execution: ExecutionContext,
    paths: ConfigPaths,
    wordpress: bool,
    echo: Callable[[str], None],
) -> None:
    """Offer to fix a credentials source that does not resolve.

    Runs after the target is written because `read_remote_credentials` needs a
    resolved target, and before `doctor` so that the checklist the user sees
    describes the configuration as fixed rather than as found.

    Only a failure a question can fix gets one. The probe is guarded on
    connectivity (`PreflightOperation._check_credentials`), so a `None`
    verdict means ssh or the deploy path is the real problem - both have their
    own `doctor` rows and neither is this function's business.
    """
    report, _ = PreflightOperation()._probe(execution)
    ok, _ = PreflightOperation()._check_credentials(execution, report)
    if ok is not False:
        return
    _offer_remote_env_file(execution, paths, wordpress, echo)


def _offer_remote_env_file(
    execution: ExecutionContext,
    paths: ConfigPaths,
    wordpress: bool,
    echo: Callable[[str], None],
) -> None:
    looked_for = remote_env_candidates(execution)
    present = _remote_env_entries(execution)
    answer = choose_remote_env_file(looked_for, present)
    if answer is None:
        raise _no_terminal()
    if answer == REMOTE_ENV_NONE:
        echo(
            "No credentials file was named. The sync will stop at the database dump, "
            "before anything is downloaded or imported."
        )
        return
    write_settings(
        path=paths.project_config, paths=paths, settings={DATABASE_ENV_FILE_KEY: [answer]}
    )


def _remote_env_entries(execution: ExecutionContext) -> list[str]:
    """The `.env*` files in the target's data root, sorted.

    Filtered in Python rather than with a shell glob: an unmatched `.env*`
    comes back as the literal pattern, which would be offered as a filename
    that does not exist. A listing that fails for any reason is an empty list
    - the question still has its opt-out, and the failure the user is being
    asked about is already on screen.
    """
    result = execution.runner.run(
        ssh_command(execution.target, ["ls", "-1a", execution.target.data_root])
    )
    if not result.ok:
        return []
    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(".env")
    )
```

Call it from `init_command`, between the settings write and `doctor_command`. `PreflightOperation()._probe` and `._check_credentials` are private; if reaching into them reads badly, add a small public helper to `preflight.py` in Task 1's style rather than duplicating the probe here — but do not re-implement the probe.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS, including every pre-existing `test_init.py` test. The existing ones build a `type: php` project whose profile is `generic`, so this question's precondition (`env` source, no file found) does not hold for them.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/cli/ tests/cli/
cat > /tmp/msg <<'EOF'
feat(cli): have `init` ask where the credentials file is on the target

The usual reason an `env` source finds nothing is that the file is there
under another name. `init` now lists what the target's data root really
holds and writes the chosen name as `database.env_file`, which steers the
remote lookup without touching the local one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 6: The override branch

**Files:**
- Modify: `src/ddev_sync/cli/prompts.py`
- Modify: `src/ddev_sync/cli/init.py` (`_offer_remote_env_file`'s opt-out path)
- Test: `tests/cli/test_init.py`, `tests/cli/test_prompts.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 3, 4 and 5.
- Produces: `choose_credentials_source(wordpress: bool) -> str | None`, `EXPLICIT_SOURCE`, `WP_CLI_SOURCE`, and `ask_credentials() -> dict[str, str] | None` in `prompts.py`.

**Context the task needs:** this is the last resort, reached only when the credentials do not resolve *and* naming a remote file did not settle it. The spec is explicit that it is the override, not the main path: the credentials normally live on the production system and the tool reads them there.

**Probe before writing — this is the task's whole point.** Build a candidate configuration in memory, run the probe against it, and write only if it passes. A write-then-check order leaves a wrong password on disk exactly when the probe fails, which is Review Focus 5.

The probe is `read_remote_credentials` against an `ExecutionContext` whose `merged` carries the answers. `ExecutionContext` is a frozen dataclass — build a new one with `dataclasses.replace` and a merged document that has the candidate layer on top, rather than mutating anything. **Clear `context.cache` for the new context**, or the memoised failure from the first probe will be returned instead of a real attempt.

**Which file to write.** Task 4 made both local paths readable and both-at-once an error. `init` must not create that error: write to `paths.legacy_local_config` when it already exists, and to `paths.local_config` otherwise. This is Review Focus 4's second half, and the spec calls it out as the one place where "the new path is the path" yields to the invariant.

`wp_cli` is offered only for a WordPress profile. It takes no values — it reads them from the remote — so choosing it writes `database.remote_credentials: wp_cli` to the **project** file, not the local one. Only `explicit` writes secrets.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_writes_probed_credentials_to_the_local_layer(project, tmp_path, monkeypatch) -> None:
    _generic(project)
    runner = _runner_where_these_credentials_work(CREDENTIALS)
    script(monkeypatch, remote_env_file=(REMOTE_ENV_NONE,), source=(EXPLICIT_SOURCE,),
           credentials=(CREDENTIALS,))
    assert invoke(project, tmp_path, runner=runner) == 0
    local = project / ".ddev" / "ddev-sync" / "local.yaml"
    assert yaml.safe_load(local.read_text(encoding="utf-8"))["database"]["credentials"] == CREDENTIALS


def test_credentials_that_fail_the_probe_are_never_written(project, tmp_path, monkeypatch, capsys) -> None:
    # Review Focus 5. The probe runs first precisely so a wrong password
    # cannot reach the disk.
    _generic(project)
    runner = _runner_where_no_credentials_work()
    script(monkeypatch, remote_env_file=(REMOTE_ENV_NONE,), source=(EXPLICIT_SOURCE,),
           credentials=(CREDENTIALS,))
    invoke(project, tmp_path, runner=runner)
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()
    assert "were not written" in capsys.readouterr().out


def test_init_appends_to_the_old_local_file_when_it_exists(project, tmp_path, monkeypatch) -> None:
    # Review Focus 4: writing the new path beside an existing old one would
    # create the both-exist state the loader refuses.
    _generic(project)
    legacy = project / ".ddev" / "ddev-sync.local.yaml"
    legacy.write_text("version: 1\ndefaults:\n  keep_dump: true\n", encoding="utf-8")
    runner = _runner_where_these_credentials_work(CREDENTIALS)
    script(monkeypatch, remote_env_file=(REMOTE_ENV_NONE,), source=(EXPLICIT_SOURCE,),
           credentials=(CREDENTIALS,))
    assert invoke(project, tmp_path, runner=runner) == 0
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()
    loaded = yaml.safe_load(legacy.read_text(encoding="utf-8"))
    assert loaded["database"]["credentials"] == CREDENTIALS
    assert loaded["defaults"]["keep_dump"] is True


def test_wp_cli_is_offered_only_for_wordpress(project, tmp_path, monkeypatch) -> None:
    _wordpress(project)
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        prompts, "_select", lambda question, choices: seen.update(c=choices) or choices[0]
    )
    prompts.choose_credentials_source(wordpress=True)
    assert WP_CLI_SOURCE in seen["c"]
    prompts.choose_credentials_source(wordpress=False)
    assert WP_CLI_SOURCE not in seen["c"]


def test_choosing_wp_cli_writes_the_project_file_not_the_local_one(project, project_file, tmp_path, monkeypatch) -> None:
    # `wp_cli` reads the values from the remote, so there is no secret to keep.
    _wordpress(project)
    runner = _runner_where_wp_cli_works()
    script(monkeypatch, remote_env_file=(REMOTE_ENV_NONE,), source=(WP_CLI_SOURCE,))
    assert invoke(project, tmp_path, runner=runner) == 0
    assert yaml.safe_load(project_file.read_text())["database"]["remote_credentials"] == "wp_cli"
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()


def test_the_credentials_question_asks_for_all_five_values(monkeypatch) -> None:
    answers = iter(["app", "deploy", "s3cret", "db.internal", "3306"])
    monkeypatch.setattr(prompts, "_text", lambda *a, **k: next(answers))
    assert prompts.ask_credentials() == {
        "name": "app", "user": "deploy", "password": "s3cret",
        "host": "db.internal", "port": "3306",
    }
```

`CREDENTIALS` is a module-level dict in the test file with those five keys.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/cli/test_init.py -k "credentials or wp_cli" -v`
Expected: FAIL with `ImportError: cannot import name 'EXPLICIT_SOURCE'`.

- [ ] **Step 3: Add the prompts**

```python
EXPLICIT_SOURCE = "Enter the database credentials by hand"
WP_CLI_SOURCE = "Read them from WordPress on the target (wp-cli)"


def choose_credentials_source(wordpress: bool) -> str | None:
    """Ask where the credentials should come from instead.

    `wp_cli` is offered only for a WordPress profile, the way the Deployer
    import is offered only to a project with a recipe for it: a question
    nobody on this project could answer is a question not worth asking.
    """
    choices = [EXPLICIT_SOURCE]
    if wordpress:
        choices.insert(0, WP_CLI_SOURCE)
    return _select("Where should the credentials come from?", choices)


_CREDENTIAL_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("name", "Database name?"),
    ("user", "Database user?"),
    ("password", "Database password?"),
    ("host", "Database host?"),
    ("port", "Database port?"),
)


def ask_credentials() -> dict[str, str] | None:
    """Ask for the five values an `explicit` source needs.

    No `questionary.password` for the password field: the value is about to be
    written to a file the user can read, the terminal is theirs, and hiding it
    would stop them checking a typo against the one thing that matters here -
    whether it is the password production really uses. The probe that follows
    is the real check.
    """
    answers: dict[str, str] = {}
    for key, question in _CREDENTIAL_QUESTIONS:
        answer = _text(question, "", _required)
        if answer is None:
            return None
        answers[key] = answer.strip()
    return answers
```

- [ ] **Step 4: Implement the branch**

Replace `_offer_remote_env_file`'s opt-out path (the `REMOTE_ENV_NONE` branch from Task 5) with a call to the override, and add:

```python
def _offer_override(
    execution: ExecutionContext,
    paths: ConfigPaths,
    wordpress: bool,
    echo: Callable[[str], None],
) -> None:
    """Offer a credentials source other than the one that failed.

    Last resort by design: the credentials normally live on the target, which
    is what the question before this one tries to reach. This is for the
    setups where nothing on the remote can be read back - Bedrock and other
    env-based WordPress installs among them.
    """
    source = choose_credentials_source(wordpress)
    if source is None:
        raise _no_terminal()
    if source == WP_CLI_SOURCE:
        # No secret: `wp_cli` reads the values from the target every run, so
        # the setting is a plain project-file key like any other.
        write_settings(
            path=paths.project_config,
            paths=paths,
            settings={"database.remote_credentials": "wp_cli"},
        )
        return
    values = ask_credentials()
    if values is None:
        raise _no_terminal()
    if not _credentials_work(execution, values):
        echo(
            "Those credentials did not work against the target, so they were not "
            "written. Check them and run 'ddev sync init' again."
        )
        return
    write_settings(
        path=_local_path(paths),
        paths=paths,
        settings={
            "database.remote_credentials": "explicit",
            "database.credentials": values,
        },
    )


def _credentials_work(execution: ExecutionContext, values: dict[str, str]) -> bool:
    """Whether `values` actually resolve, without writing them anywhere.

    A candidate layer on top of the merged configuration, probed through the
    same `read_remote_credentials` a real run uses, so what is proved here is
    what a run will do. The cache is emptied because the failure that brought
    us here is memoised in it, and a reused `ExecutionContext` would return
    that instead of trying.
    """
    candidate = LayerDoc(
        label="answers",
        data={"database": {"remote_credentials": "explicit", "credentials": values}},
    )
    probe = replace(
        execution,
        merged=merge_layers([LayerDoc("merged", execution.merged.data), candidate]),
        cache={},
    )
    try:
        read_remote_credentials(probe)
    except OperationError:
        return False
    return True


def _local_path(paths: ConfigPaths) -> Path:
    """Where the local layer should be written.

    The new path, unless the old one is already in use. Writing the new file
    beside an existing old one would produce the very state `load_user_layers`
    refuses, and a command may not create the error it then declines to
    resolve.
    """
    return paths.legacy_local_config if paths.legacy_local_config.is_file() else paths.local_config
```

`replace` comes from `dataclasses`; check that `ExecutionContext.cache` is a field it can take — if the field is not constructor-settable, clear it on the copy instead, and say so in the docstring rather than leaving the claim about the cache untrue.

Whether `wordpress` is true comes from the resolved profile's name; `init` already has the `ProjectResolution` from B1.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/cli/ tests/cli/
cat > /tmp/msg <<'EOF'
feat(cli): let `init` fall back to credentials entered by hand

The last resort, for setups where nothing on the target can be read back.
The values are probed against the target before they are written, so a
password that does not work never reaches the disk, and they go to whichever
local layer is already in use - writing the new path beside an existing old
one would create the state the loader refuses.

`wp_cli`, offered only for WordPress, writes no secret at all: it reads the
values from the target on every run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 7: Say it where users look, and record what changed

**Files:**
- Modify: `README.md` (around line 102)
- Modify: `ddev-sync/ddev-sync.example.yaml`
- Modify: `docs/specs/2026-10-08-sync-init-part-b2-design.md` (append a corrections section)
- Test: none in pytest; verify the template by parsing it, as the step below does.

**Context the task needs:** in this project documentation is held to the same standard as code — a claim not true of the code is a defect. The B1 branch's final review found three such defects in prose that branch had itself falsified, so the search below is not a formality.

`README.md` currently says the `generic` profile's credentials "belong in `.ddev/ddev-sync.local.yaml` rather than the project file — and check that your repository ignores that path, because nothing here does it for you." After Task 4 both halves are wrong: the path moved, and the add-on now does ignore it.

`ddev-sync/ddev-sync.example.yaml` documents `database.remote_credentials` and names the old path in the same breath.

- [ ] **Step 1: Correct the README**

Rewrite the `generic` paragraph: the path is `.ddev/ddev-sync/local.yaml`, and the add-on's shipped `.gitignore` covers it, so the warning becomes a statement. Mention that a project still carrying the old path keeps working and that `doctor` says it should move.

- [ ] **Step 2: Correct and extend the template**

Update the `database:` comment block to the new path, and document `database.env_file` beside `remote_credentials`, since they describe the same remote file. Keep every addition commented out.

- [ ] **Step 3: Verify the template is still inert**

Run:
```bash
uv run python -c "
import yaml, pathlib
text = pathlib.Path('ddev-sync/ddev-sync.example.yaml').read_text()
assert text.startswith('#ddev-generated'), 'the generated-file marker is gone'
assert yaml.safe_load(text) == {'version': 1}, yaml.safe_load(text)
print('ok')
"
```
Expected: `ok`. A failure means part of the addition is not commented out.

- [ ] **Step 4: Append the corrections section to the spec**

In the shape part A's and B1's specs use (read `docs/specs/2026-10-07-sync-init-part-b1-design.md`'s for the form), record every claim this plan's implementation disproved. Read the spec's Design section against what was actually built — `operations/preflight.py`, `operations/database.py`, `config/loader.py`, `cli/init.py`, `cli/prompts.py` — and say plainly what did not hold. If nothing did, say that in one line; an empty search is a result, not a gap.

Two places worth checking specifically, because they are where this plan guessed:
- The spec says `init` probes through `PreflightOperation`'s existing methods. If Task 5 had to add a public helper instead of reaching into `_probe`/`_check_credentials`, the spec describes something that does not exist.
- The spec says the candidate credentials are probed "through the same `read_remote_credentials` a real run uses". If `ExecutionContext` could not be copied with a fresh cache and the probe had to be built differently, say so.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools`
Expected: all clean, coverage 100 %. This task changes no code; if anything moves, report it.

- [ ] **Step 6: Commit**

```bash
git add README.md ddev-sync/ddev-sync.example.yaml docs/specs/2026-10-08-sync-init-part-b2-design.md
cat > /tmp/msg <<'EOF'
docs: name the local layer's new path where users look for it

The README told people to check that their repository ignores a path the
add-on now ignores for them, and named the old one. The installed template
gains `database.env_file` beside the credentials source it belongs with.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

## Notes for the reviewer

- **The credentials probe must never raise out of `check()`.** `doctor` renders its whole checklist only after `check()` returns. Verify the guard and the `except OperationError` are both there and that no path between them can throw something else.
- **No detail string may carry a password.** Check the success detail's fields and the failure detail's source. `render_doctor` does no scrubbing.
- **`init` must not create the both-local-files state.** Task 4 makes it an error; Task 6 must write to whichever file already exists.
- **Watch the comments.** Every previous plan in this repo left behind comments that stated something the code did not do, most copied from the plan's own prose. This plan's code blocks carry comments; verify each against what was actually written.
- **A `files`-only sync must still run** with unresolvable database credentials. That is a behaviour someone depends on.
- **The `config`/`config_list` rewrite in Task 3 is the one change that can break code far from this branch.** Two invariants were checked by running the rewrite before this plan was handed over, and both hold: `config` returns an explicitly-configured empty string verbatim rather than falling back to its default (only a *missing* key does that — `operations/placeholders.py::_validated_identifier`'s docstring depends on this), and `config_list` still drops non-mapping entries. Verify both survived, and that no existing test in `tests/operations/` needed editing.
