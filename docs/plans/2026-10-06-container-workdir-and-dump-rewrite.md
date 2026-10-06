# Container Workdir Translation and Dump-Rewrite Failure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every web-container command work when the engine runs from a host checkout, and turn a failed dump rewrite into an error that says the local database is already empty.

**Architecture:** Two independent single-file fixes. The first translates the project-root prefix of a workdir to DDEV's fixed container mount point inside `web_command`, so all seven call sites keep passing host paths unchanged. The second wraps one call in `DbImportOperation.execute` so an `OSError` from the rewrite becomes an `OperationError` that states what the sync already did.

**Tech Stack:** Python 3.12, uv, pytest + pytest-cov, ruff, mypy strict.

## Global Constraints

- Tests: `uv run pytest`. Coverage gate is **100 % line and branch** (`--cov-fail-under=100`, `branch = true`); `# pragma: no cover` is forbidden anywhere in the project.
- `uv run ruff check . && uv run ruff format --check .` and `uv run mypy src tests` must pass. Ruff line length is 100.
- `subprocess` may only be imported in `src/ddev_sync/transport/runner.py`; a test parses the AST of every other module to enforce this. Neither task needs it.
- Everything code-side is English: code, comments, docstrings, test names, commit messages.
- Conventional Commits. Every commit message ends with the footer
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Comments explain *why*, not *what*. The existing comments in both files are the house style — match their density and tone.
- Do not restructure, rename or re-order anything beyond what a task asks for.

---

### Task 1: Translate the workdir to the container's mount point

**Context the task needs:** DDEV always mounts the project root at `/var/www/html` in the web container; this is not configurable (verified on DDEV 1.24.x: the generated compose sets `target: /var/www/html` and `DDEV_APPROOT: /var/www/html`). The sidecar's own compose (`docker-compose.ddev-sync.yaml`) mounts the same project at the same path, which is why `web_command` could pass `workdir` straight through and why every existing test in `tests/transport/test_container.py` constructs its project with `root=Path("/var/www/html")` — in the sidecar the translation this task adds is an identity.

The engine also runs from a host checkout (`uv run ddev-sync ...` during development, and the `features/` and docs flows describe it). There `project.root` is a host path such as `/Users/me/code/acme`, which does not exist inside the container. Measured against a real project:

```
$ docker exec -w /Users/noidee/_dev/t3-13-ssg ddev-t3-13-ssg-web true
OCI runtime exec failed: ... chdir to cwd ("/Users/noidee/_dev/t3-13-ssg") set in config.json failed: no such file or directory
$ docker exec -w /var/www/html ddev-t3-13-ssg-web true
$
```

So **no** web-container command can run from a host checkout: every setup command, every WP/TYPO3 sanitize operation, and the `web_ok` preflight probe fail. The preflight failure is the already-noticed empty `FAIL  web container ... did not answer:` row — docker writes that `chdir` error to **stdout**, so the `stderr` the row interpolates is empty.

The fix is a translation, not a constant: the caller keeps passing the path it has, and `web_command` maps the `project.root` prefix onto the mount point. All seven call sites pass a path that is guaranteed to be under `project.root` — `project.root` itself (`transport/deployer.py:61`, `operations/catalog.py:229,269,329`, `operations/preflight.py:217`), `project.docroot_path` (`operations/catalog.py:114`, which is `root / docroot`), and `operations/exec.py:54`, whose `_resolve_workdir` returns either `project.root` or `context.local(...)`, and `ExecutionContext.local` already refuses a path that escapes the root.

**Files:**
- Modify: `src/ddev_sync/transport/container.py`
- Test: `tests/transport/test_container.py`

**Interfaces:**
- Consumes: `DdevProject.root`, `DdevProject.web_container` from `ddev_sync.ddev.project`.
- Produces: `web_command(project, argv, *, workdir)` keeps its exact signature. Callers are unchanged — do not touch any call site.

- [ ] **Step 1: Write the failing tests**

Add to `tests/transport/test_container.py`. Note the existing module-level
helper `def project(root: str = "/var/www/html") -> DdevProject` — pass a
host-shaped root to it rather than building a second helper.

```python
def test_a_host_project_root_is_translated_to_the_container_mount_point() -> None:
    # Running from a host checkout, `project.root` is a host path that does
    # not exist in the container: `docker exec -w /Users/me/code/acme` fails
    # with "chdir to cwd ... failed", and docker writes that to stdout, so it
    # used to surface as a blank preflight row.
    argv = web_command(
        project(root="/Users/me/code/acme"),
        ["vendor/bin/typo3", "cache:flush"],
        workdir=Path("/Users/me/code/acme"),
    )
    assert argv[4:6] == ["-w", "/var/www/html"]


def test_a_subdirectory_of_the_project_keeps_its_offset_under_the_mount_point() -> None:
    argv = web_command(
        project(root="/Users/me/code/acme"),
        ["sh", "-c", "npm ci"],
        workdir=Path("/Users/me/code/acme/public/theme"),
    )
    assert argv[4:6] == ["-w", "/var/www/html/public/theme"]


def test_a_workdir_outside_the_project_is_rejected_rather_than_silently_mapped() -> None:
    # Every call site passes `project.root`, `project.docroot_path` or a path
    # `ExecutionContext.local` has already confined to the root, so this is a
    # programming error - and guessing a mapping for it would run a command in
    # whatever the container happens to have at that path.
    with pytest.raises(ValueError, match="is not inside the project"):
        web_command(
            project(root="/Users/me/code/acme"),
            ["true"],
            workdir=Path("/etc"),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/transport/test_container.py -v`

Expected: the two translation tests FAIL with the host path in the argv
(`assert ['-w', '/Users/me/code/acme'] == ['-w', '/var/www/html']`), and the
rejection test FAILS with `DID NOT RAISE ValueError`.

- [ ] **Step 3: Implement the translation**

In `src/ddev_sync/transport/container.py`, add the constant below the
imports:

```python
# DDEV mounts the project root here in every container it starts, and the
# path is not configurable - `docroot` and `composer_root` are relative to
# it. The sidecar's own compose mounts the project at the same path, which
# is why passing a workdir through unchanged worked there and only broke
# when the engine ran from a host checkout.
CONTAINER_PROJECT_ROOT = Path("/var/www/html")
```

Replace `web_command` with:

```python
def web_command(project: DdevProject, argv: Sequence[str], *, workdir: Path) -> list[str]:
    """Wrap `argv` so it runs in `project`'s web container, in `workdir`.

    `workdir` is a path on whichever side the engine is running - the host
    when it runs from a checkout, the sidecar's own mount when it runs as
    the add-on - and is translated onto `CONTAINER_PROJECT_ROOT`, which is
    where the web container has the project. In the sidecar that is an
    identity; from a host checkout it is what makes the command run at all,
    because `docker exec -w /Users/...` fails to chdir.

    The translated path is passed as docker's own `-w` rather than spliced
    into a `cd ... && ...` string: the command keeps the exact text the
    profile wrote, and there is no second layer of shell quoting to get
    wrong.
    """
    if not argv:
        raise ValueError("a web command needs at least one argument")
    return [
        "docker",
        "exec",
        "-u",
        _current_user(),
        "-w",
        str(_container_workdir(project, workdir)),
        project.web_container,
        *argv,
    ]


def _container_workdir(project: DdevProject, workdir: Path) -> Path:
    """Map `workdir` from this side of the mount onto the container's."""
    try:
        offset = workdir.relative_to(project.root)
    except ValueError as error:
        raise ValueError(
            f"{workdir} is not inside the project at {project.root}, "
            "so it has no place in the web container"
        ) from error
    return CONTAINER_PROJECT_ROOT / offset
```

Keep the existing `if not argv` check ahead of the translation: an empty
argv is a usage error worth reporting before anything else is computed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/transport/test_container.py -v`
Expected: PASS, including the four pre-existing tests — their project root is
`/var/www/html`, so the translation is an identity for them and their
asserted argv is unchanged.

- [ ] **Step 5: Run the full suite and the linters**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all pass, coverage still 100 %. If any other test broke, it was
relying on the untranslated path — report it rather than changing the
assertion to match.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/transport/container.py tests/transport/test_container.py
git commit -F - <<'EOF'
fix(transport): translate the workdir onto the container's mount point

`web_command` passed `workdir` through unchanged, which held only while
the engine ran in the sidecar, where the project is mounted at the same
path as in the web container. From a host checkout `project.root` is a
host path, so `docker exec -w` failed to chdir and no web-container
command could run - including the `web_ok` preflight probe, whose row
looked blank because docker writes that error to stdout.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Say the database is already empty when the dump rewrite fails

**Context the task needs:** `DbImportOperation.execute` in
`src/ddev_sync/operations/database.py` runs `self._recreate(...)` (which drops
and recreates the local database, and owns the destructive confirmation),
then `strip_definers(dump)`, then `self._import(...)`. That order is
deliberate and documented in a comment above it — do not change it.

`strip_definers` writes the whole dump to `dump.sql.tmp` and then
`Path.replace`s it over the original, so it needs as much free space again as
the dump occupies. On a full disk it raises a bare `OSError`, which escapes
as a traceback at the single worst moment: the local database has just been
dropped and recreated empty, and nothing says so. `dump.sql` itself is
untouched in both failure modes — the write loop fails before the `replace`,
and the `replace` is atomic — so the message may state that.

**Files:**
- Modify: `src/ddev_sync/operations/database.py`
- Test: `tests/operations/test_db_import.py`

**Interfaces:**
- Consumes: `OperationError`, already imported in `operations/database.py`
  via `from ddev_sync.operations.base import ... OperationError` — check how,
  and do not add a second import.
- Produces: nothing new; `strip_definers`'s own signature and behaviour are
  unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/operations/test_db_import.py`, using that file's existing
`make_context`, `with_dump` and `COMPLETE_DUMP` helpers — all three are
already defined at the top of the file. Force the `OSError` by making the
temporary path unopenable: a directory already sitting at `dump.sql.tmp`
makes `temporary.open("wb")` in `strip_definers` raise `IsADirectoryError`,
which is an `OSError` — the same class the real disk-full failure raises,
without having to fill a disk.

```python
def test_a_failed_dump_rewrite_says_the_local_database_is_already_empty(
    tmp_path: Path,
) -> None:
    # The drop runs before the rewrite (see `execute`'s own comment), so an
    # OSError here leaves an empty database behind - the one thing the user
    # has to know, and the one thing a bare traceback did not say.
    runner = FakeCommandRunner()
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysqladmin"], stdout="")
    context = make_context(runner, tmp_path)
    dump = with_dump(context)
    (context.downloads_dir / f"{DUMP_FILENAME}.tmp").mkdir()

    with pytest.raises(OperationError) as error:
        DbImportOperation().execute(context, {})

    message = str(error.value)
    assert "has already been dropped" in message
    assert str(dump) in message
    # The drop really did run first, which is what makes that claim true -
    # and `mysql` never ran, so nothing was imported.
    assert [call[0] for call in runner.calls] == ["mysqladmin", "mysqladmin"]
    assert "drop" in runner.calls[0]
```

`with_dump` writes `COMPLETE_DUMP`, which already carries the trailer
`dump_is_complete` looks for, so the test reaches `strip_definers` rather
than failing the completeness check ahead of it.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/operations/test_db_import.py -k dump_rewrite -v`
Expected: FAIL with `IsADirectoryError` escaping instead of
`OperationError`.

- [ ] **Step 3: Implement the wrapper**

In `DbImportOperation.execute`, replace the bare `strip_definers(dump)` call
with:

```python
        try:
            strip_definers(dump)
        except OSError as error:
            # The drop above has already happened, so this is not a failure
            # that left everything as it was - and the rewrite needs as much
            # free space again as the dump occupies, which makes a full disk
            # its likeliest cause. `dump.sql` is unchanged either way: the
            # write loop fails before the rename, and the rename is atomic.
            raise OperationError(
                f"could not rewrite {dump} to remove DEFINER clauses: {error}. "
                f"The local database {database!r} has already been dropped and is "
                "empty. The dump itself is unchanged, so fixing the cause (the "
                "rewrite needs as much free space again as the dump) and running "
                "the sync again will import it."
            ) from error
```

Leave the comment that explains the `_recreate`-before-rewrite order exactly
as it is.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/operations/test_db_import.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and the linters**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all pass, coverage still 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/operations/database.py tests/operations/test_db_import.py
git commit -F - <<'EOF'
fix(db): report a failed dump rewrite as the empty database it leaves

The rewrite that strips DEFINER clauses runs after the drop, and needs as
much free space again as the dump occupies, so a full disk raised a bare
OSError at the point where the local database had just been emptied -
with nothing in the traceback saying so. It is now an OperationError that
names the dropped database, states the dump is unchanged, and says what
re-running will do.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

## Out of scope

- `strip_definers` leaves `dump.sql.tmp` behind when it fails. The next run
  opens it `"wb"` and truncates it, so there is no correctness problem, only
  disk use after a disk-full error. Cleaning it up is a separate change with
  its own hard-to-reach branch; it belongs in the open-items ledger, not here.
- The seven `web_command` call sites. Task 1 keeps the signature so none of
  them changes.
