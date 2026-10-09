# Verifying features in the matrix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the matrix a way to prove what a *feature* claims, and use it on the five features that need no service of their own — `node-build`, `filament`, `livewire`, `inertia`, `typo3-processedfiles`.

**Architecture:** One dataclass (`FeatureCheck`) and one defaulted field (`Cell.features`) carry the assertions, so a feature cell stays "a registry entry plus recipe files, not a new test" — the premise `cells.py`'s own docstring states. Three new bodies in `test_matrix.py` read that field: one proves the feature was active in the run, one checks the paths its commands must create and remove, one plants and re-reads a database row. Two new cells (`mxfeatlaravel`, `mxfeattypo3`) declare the checks; the six existing cells are untouched and keep asserting exactly what they assert today.

**Tech Stack:** Python 3.12, pytest, Docker Compose, composer, npm/Vite, MariaDB 11.

**Spec:** `docs/specs/2026-10-08-feature-verification-design.md`. Read it. It carries the measurements and the reasoning behind every decision below; this plan carries the code.

## Global Constraints

- Tests: `uv run pytest`. The default run excludes the `matrix` and `integration` markers (`addopts = … -m 'not integration and not matrix'`). Coverage gate is **100 % line and branch** on `ddev_sync` only (`--cov=ddev_sync`), so tests under `tests/` are not measured; `# pragma: no cover` is **forbidden** (`exclude_lines` is deliberately empty).
- `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests tools` must all be clean **at the committed state**. Ruff line length is 100; mypy is strict. Run each with its exit code intact — never `… 2>&1 | tail -1 && …`, which reports `tail`'s status and walks past a failing check.
- `subprocess` may only be imported in `src/ddev_sync/transport/runner.py`; an AST-parsing test (`tests/test_subprocess_is_isolated.py`) enforces it. Its `SOURCE_ROOT` is `src/ddev_sync`, so it never scans `tests/` — which is why `tests/matrix/stack.py` may shell out. **No task in this plan adds a `subprocess` import anywhere under `src/`.**
- **No engine code changes.** Nothing under `src/ddev_sync/` is modified by this plan. If a cell finds a defect in a feature definition, the fix is a change to `features/*.yaml` and the cell that found it is the regression test — report it rather than working around it.
- **Cell names are `mx` + lowercase letters and digits only.** No hyphen (`pytest -k` parses its argument as an expression), no underscore (the name becomes the network alias `ddev-<name>-db`, a hostname). See the comment above `Cell.name`.
- **And no cell name may contain another cell's name.** `.github/workflows/matrix.yaml` runs one job per cell with `-k "$CELL"`, and `-k` matches substrings of the test id: measured on this registry, `-k mxtypo312` collects 25 tests (one cell) while `-k mxtypo31` collects 50 (both TYPO3 cells). The first draft of this plan named the new cells `mxlaravelfeat` and `mxtypo3feat`; `mxlaravelfeat` contains `mxlaravel`, so CI's existing `mxlaravel` job would have installed and run **both** Laravel cells, doubled its runtime, been able to fail for the other cell's reasons, and then emitted the stock-skeleton `laravel` row from a job that had also run a Filament install — the very provenance decision 1 exists to protect. Hence `mxfeatlaravel` and `mxfeattypo3`: neither contains, nor is contained by, any existing name, while `-k mxfeat` still selects both new cells as a group. Task 4 adds this reason to the comment above `Cell.name`.
- **A feature cell declares the same profile-shaped fields as its profile sibling.** `Cell`'s docstring defines `admin_user=None` as "the profile has no such rule", and `truncated_tables=()` likewise. A typo3@13 cell that set `admin_user=None` would make that documented meaning false and would print a lying skip reason. So `mxfeatlaravel` copies `mxlaravel`'s field values and `mxfeattypo3` copies `mxtypo313`'s; only `features=(…)` is new. The duplicated profile evidence costs nothing (the same sync runs either way) and is never *claimed*: the rows these cells emit are feature rows (Task 6).
- **An `appears` path must be absent before the sync, and the install recipe is what makes it absent** (spec decision 4). Forgetting it is silent: the assertion still passes and proves nothing.
- **A `disappears` path must be *present* before the sync, and the install recipe is what makes it present — and must check that it did.** The mirror of decision 4, and strictly weaker in the test: `appears` has a guard in `test_matrix.py` (the path must not also be in the cell's `remote/` tree, or the files step would create it either way), but a `disappears` path that was never created passes vacuously, and no post-hoc assertion can tell. The recipe's own `test -d` is the only thing that can. Found by Task 3's reviewer.
- Comments explain *why*, not *what*, and must be **true of the code beside them**. This project treats a comment that states something the adjacent code does not do as a defect in its own right. Read this plan's wording critically and report anything that does not hold rather than copying it.
- Conventional Commits. Every message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Write the message to a file and use `git commit -F <file>`; never nest a heredoc inside another.
- Git history is linear, fast-forward only. **Do not push.**
- TDD where a test can exist before the code. For the two cell tasks the *cell run* is the test, and the task is done when that run is green — not when the files are written.

## Two refinements of the spec's wording

Both are narrowings, found while reading the code the spec describes. They are listed here so a reviewer does not read them as drift.

1. **The spec's `FeatureCheck` docstring said the run must report the feature "as detected".** `typo3-processedfiles` has **no detect block, on purpose** (see the comment at the top of `features/typo3-processedfiles.yaml`: "whether you care is a judgement, not a property of the project"), so it can only ever be *requested*. `cli/render.py::render_features` prints requested and detected features in the same block, with the reason text differing (`from <source>` versus `detected: <what matched>`). The assertion is therefore **"active in this run"**, read off that block, and the docstring in the code says so.

2. **The spec says "`node` and `npm` join the web image".** The exact lines are measured in Task 2: the `php:*-cli` images are now Debian 13 (trixie), and the node binaries are copied from `node:22-bookworm-slim` — older glibc, which runs on a newer one, not the reverse.

## What has already been measured

Do not re-measure these; do not contradict them without measuring.

| Fact | Where it came from |
|---|---|
| `filament/filament` v5.10.1, `livewire/livewire` v4.4.7, `inertiajs/inertia-laravel` v3.5.1 are the current releases | packagist, 2026-10-09 |
| `filament:optimize` prints `Caching components` and `Caching Blade icons`, and exactly two paths appear: `bootstrap/cache/filament/panels/admin.php` and `bootstrap/cache/blade-icons.php` — after `filament:install --panels`, because without a panel there is nothing to cache | spec, measured 2026-10-08 in the matrix web image |
| `livewire:publish --assets` copies `vendor/livewire/livewire/dist` into `public/vendor/livewire/` — eleven files | spec, as corrected by the `mxfeatlaravel` run (b5c98dd); the 2026-10-08 probe said thirteen and was wrong |
| **`filament:upgrade` is not settled.** It printed "Successfully upgraded!" and no new path appeared, but its likely outputs are assets under `public/` that `filament:install` had already created and the probe did not remove. Nothing may be asserted for it | spec, measured 2026-10-08 |
| Filament cannot be installed on a bare `php:8.3-cli`: its dependency tree needs `ext-intl` (`filament/filament`'s own `require` block does not name it — it is transitive). The matrix web image already builds `intl` for TYPO3's sake | spec, 2026-10-08; the `require` block checked on packagist 2026-10-09 |
| `laravel/laravel` v13.11.0 ships `"build": "vite build"` and **none** of `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml` (all three 404 at that tag). `detect_package_manager` keys on the lockfile and nothing else; `require_package_manager` raises without one; `${PM_INSTALL}` resolves to `npm ci`, which also refuses to run without one | checked 2026-10-09 |
| `sys_file_processedfile` is defined in `typo3/sysext/core/ext_tables.sql` at line 120 of v13.4.2, and **every one of its columns is auto-increment, nullable, or carries a DEFAULT** — so a two-column INSERT is valid under MariaDB's strict mode | fetched and read 2026-10-09 |
| `php:8.3-cli` and `php:8.2-cli` are both Debian 13 (trixie); node 22.23.3 + npm 10.9.9 copied from `node:22-bookworm-slim` run on both | built and run 2026-10-09 |
| Neither `profiles/laravel.yaml` nor `profiles/typo3.yaml` sets `delete:` on a `files.paths` entry, and `operations/files.py` defaults it to `False` — so the `files` step never removes an extraneous local path | read 2026-10-09 |
| `${DDEV_DOCROOT}` resolves to the raw `docroot` value (`"public"` here) or `"."` when empty, validated by `_validated_docroot` | `operations/placeholders.py:222` |
| `tests/matrix/docker-compose.yaml` is fully parameterised by `${MATRIX_CELL}` (project name, image tag, container name, db alias) and the web service already sets `HOME=/tmp` | read 2026-10-09 |

---

## File Structure

| Path | Responsibility |
|---|---|
| `tests/matrix/cells.py` | **Modify.** `FeatureCheck` beside `FlushMarker`; `features: tuple[FeatureCheck, ...] = ()` on `Cell`; the two new registry entries. |
| `tests/matrix/test_feature_registry.py` | **Create.** One unmarked guard: every `FeatureCheck.feature` names a file in `features/`. Runs in the ordinary unit suite — `pytestmark = pytest.mark.matrix` lives in `test_matrix.py` alone and `tests/matrix/conftest.py` has no autouse fixtures, so nothing here starts a stack. |
| `tests/matrix/web/Dockerfile` | **Modify.** node and npm, beside composer and wp-cli. |
| `tests/matrix/test_matrix.py` | **Modify.** `_active_features` helper and three new test bodies, inserted after `test_the_drupal_admin_holds_the_administrator_role` and before `test_an_unset_image_is_built_from_this_tree`. |
| `tests/matrix/cells/mxfeatlaravel/` | **Create.** `install.sh`, `ddev-sync.yaml`, `seed.sql`, `keep-data.txt`, `remote/storage/app/public/matrix-upload.txt`. |
| `tests/matrix/cells/mxfeattypo3/` | **Create.** `install.sh`, `ddev-sync.yaml`, `seed.sql`, `remote/public/fileadmin/matrix-upload.txt`, `remote/public/uploads/matrix-upload.txt`. |
| `tools/apply_verified_rows.py` | **Modify.** `emit_row` emits one row per feature for a feature cell, and **no** profile row. |
| `tests/test_apply_verified_rows.py` | **Modify.** Two new cases for that split. |
| `profiles/VERIFIED.md` | **Modify.** The five feature rows, from the two green runs. |

Nothing under `src/ddev_sync/` changes. `.github/workflows/matrix.yaml` changes nothing either: it reads the cell list from `CELLS` and prints whatever `--emit` returns.

---

### Task 1: `FeatureCheck` in the registry, and the guard that costs nothing

**Files:**
- Modify: `tests/matrix/cells.py`
- Create: `tests/matrix/test_feature_registry.py`

**Interfaces:**
- Consumes: `FlushMarker` (already in `tests/matrix/cells.py`).
- Produces: `FeatureCheck(feature: str, appears: tuple[str, ...] = (), disappears: tuple[str, ...] = (), row_marker: FlushMarker | None = None)`, frozen dataclass, exported from `tests.matrix.cells`; and `Cell.features: tuple[FeatureCheck, ...] = ()`. Tasks 3–6 all use these names.

- [ ] **Step 1: Write the failing test**

Create `tests/matrix/test_feature_registry.py`:

```python
"""The one thing about a feature cell that is worth knowing before it runs.

Unmarked on purpose, so it runs in the ordinary unit suite: a mistyped
feature name would otherwise cost a three-minute cell run to discover, or
worse, silently assert about a feature that does not exist. The `matrix`
marker is applied by `pytestmark` in `test_matrix.py` alone and
`tests/matrix/conftest.py` declares no autouse fixtures, so nothing here
brings a stack up.
"""

from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT, FeatureRegistry
from tests.matrix.cells import CELLS


def test_every_feature_check_names_a_feature_that_exists() -> None:
    known = set(FeatureRegistry(DEFAULT_FEATURE_ROOT).available())
    # The registry read must not be the thing that is empty: an empty `known`
    # would make the loop below assert nothing at all.
    assert "node-build" in known, f"the feature directory was not read: {sorted(known)}"
    for cell in CELLS:
        for check in cell.features:
            assert check.feature in known, (
                f"{cell.name} declares a check for feature {check.feature!r}, "
                f"which is not one of {sorted(known)}"
            )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/matrix/test_feature_registry.py -v --no-cov`
Expected: FAIL — `AttributeError: 'Cell' object has no attribute 'features'`.

- [ ] **Step 3: Add `FeatureCheck` and the field**

In `tests/matrix/cells.py`, after the `FlushMarker` dataclass and before `Cell`:

```python
@dataclass(frozen=True)
class FeatureCheck:
    """What a cell proves about one feature.

    `feature` is the name `features/<name>.yaml` declares, and naming it is
    itself an assertion: the run must report that feature as active. A check
    with nothing else set therefore still proves something, which is exactly
    `inertia`'s case - it declares no commands at all.

    "Active", not "detected": `typo3-processedfiles` has no detect block by
    design (see the comment at the top of its file), so it is activated by a
    `features:` key instead. `cli/render.py::render_features` prints both
    kinds in one block, which is what the assertion reads.

    `appears` are paths that must exist after the sync and must have been
    removed by the install recipe before it - otherwise the assertion passes
    against a path the install left behind and proves nothing. `disappears`
    are paths that must be gone after the sync. `row_marker` reuses
    `FlushMarker`: a row the cell plants which the sync must have removed.
    """

    feature: str
    appears: tuple[str, ...] = ()
    disappears: tuple[str, ...] = ()
    row_marker: FlushMarker | None = None
```

In `Cell`, add the field last, after `remote_packages`:

```python
    features: tuple[FeatureCheck, ...] = ()
```

and add this paragraph to `Cell`'s docstring, after the `remote_packages` paragraph:

```
    `features` is what this cell proves about the *features* the sync
    activated, as opposed to the profile - see `FeatureCheck`. Defaulted, so
    the six profile cells state nothing about features and keep asserting
    exactly what they asserted before this field existed.
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/matrix/test_feature_registry.py -v --no-cov`
Expected: PASS (1 test; the loop body never runs yet — no cell declares a feature).

- [ ] **Step 5: Prove the guard is load-bearing**

A guard that no mutation can fail is not a guard. Temporarily append a bad check to the last cell in `CELLS` — add `features=(FeatureCheck(feature="no-such-feature"),)` to the `mxdrupal` entry — and run:

Run: `uv run pytest tests/matrix/test_feature_registry.py -v --no-cov`
Expected: FAIL, naming `mxdrupal` and `'no-such-feature'`.

Then revert that edit (`git checkout -- tests/matrix/cells.py` would also throw away Step 3 — remove the four added characters by hand, or stash the mutation) and re-run to confirm PASS.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
```

All four must pass, each with its own exit code.

```bash
cat > /tmp/ddev-sync-commit-1.txt <<'MSG'
test(matrix): let a cell say what it proves about a feature

The registry is profile-shaped throughout - required_tables, admin_user,
password_column, flush_marker - and had no field and no concept for a
feature, so the eight features in features/ had no way to earn a row in
VERIFIED.md's second table. FeatureCheck is that field: a feature name,
the paths its commands must create and remove, and optionally a database
row to plant, declared in the registry like every other assertion rather
than written into a test body.

The guard beside it is unmarked, so it runs in the ordinary unit suite: a
mistyped feature name would otherwise cost a three-minute cell run to
find, or silently assert about a feature that does not exist.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/cells.py tests/matrix/test_feature_registry.py
git commit -F /tmp/ddev-sync-commit-1.txt
```

---

### Task 2: node and npm in the matrix web image

**Files:**
- Modify: `tests/matrix/web/Dockerfile`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `node` and `npm` on the web container's PATH. Task 4's install recipe and the `node-build` feature's own `${PM_INSTALL}` / `${PM_RUN}` both depend on it.

**Why the image and not a recipe:** the engine reaches these binaries by `docker exec`, and an exec'd process sees the *image's* PATH, not whatever an install recipe exported. That is the same reason composer and wp-cli are in this file, and the Dockerfile's own comments already say it for wp-cli.

- [ ] **Step 1: Add the binaries**

Append to `tests/matrix/web/Dockerfile`:

```dockerfile
# node and npm, for the same reason as wp-cli above: the engine runs
# `${PM_INSTALL}` / `${PM_RUN} build` (features/node-build.yaml) by `docker
# exec`, which sees this image's PATH and not an install recipe's exports. A
# real DDEV web container ships node too.
#
# Copied from the official node image rather than installed from apt, because
# Debian's `npm` package trails: the Laravel skeleton's package.json pins
# `vite ^8`, which refuses to run on anything below node 20.19 / 22.12.
#
# `bookworm`, while `php:8.3-cli` and `php:8.2-cli` are both trixie: a binary
# built against an older glibc runs on a newer one, and the reverse does not,
# so the older base is the safe one to copy from whichever way the php images
# move next. Measured: node v22.23.3 and npm 10.9.9 run on both php bases.
#
# npm is a package under /usr/local/lib/node_modules, not a binary, so the
# node image's own symlinks are recreated here rather than copied.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
```

- [ ] **Step 2: Build it and run the binaries**

```bash
docker build --build-arg PHP_IMAGE=php:8.3-cli -t ddev-sync-web-probe tests/matrix/web
docker run --rm ddev-sync-web-probe sh -c 'node -v; npm -v; php -v | head -1; composer --version; wp --allow-root --version'
```

Expected: `v22.x`, `10.x`, PHP 8.3.x, a composer version, a wp-cli version — the last three proving nothing that was already in the image was displaced.

- [ ] **Step 3: Build it on the other php base too**

The image is shared by every cell, and `mxtypo312` names `php:8.2-cli`.

```bash
docker build --build-arg PHP_IMAGE=php:8.2-cli -t ddev-sync-web-probe82 tests/matrix/web
docker run --rm ddev-sync-web-probe82 sh -c 'node -v; npm -v; php -v | head -1'
```

Expected: the same node and npm versions, PHP 8.2.x.

- [ ] **Step 4: Clean up the probe images**

```bash
docker image rm -f ddev-sync-web-probe ddev-sync-web-probe82
```

- [ ] **Step 5: Commit**

```bash
cat > /tmp/ddev-sync-commit-2.txt <<'MSG'
test(matrix): put node and npm in the web image

node-build is one of the five features the matrix is about to verify, and
its commands run in the web container by docker exec - which sees the
image's PATH, not an install recipe's exports. That is why composer and
wp-cli are in this file, and node belongs there for the same reason.

Copied from node:22-bookworm-slim rather than installed from apt: the
Laravel skeleton pins vite ^8, which refuses to run below node 20.19.
Bookworm while the php images are trixie, because an older glibc's
binaries run on a newer glibc and not the other way round; measured on
both php:8.3-cli and php:8.2-cli.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/web/Dockerfile
git commit -F /tmp/ddev-sync-commit-2.txt
```

---

### Task 3: The three assertions

**Files:**
- Modify: `tests/matrix/test_matrix.py`

**Interfaces:**
- Consumes: `FeatureCheck` and `Cell.features` from Task 1. `Stack.engine`, `Stack.sql`, `cell_dir`, `work_dir` (all already imported in this file).
- Produces: `test_every_feature_the_cell_names_was_active_in_the_run`, `test_each_feature_created_and_removed_the_paths_it_claims`, `test_the_feature_rule_removed_the_planted_row`. Tasks 4 and 5 are verified by these.

**Ordering, which this file already depends on:** `test_a_whole_sync_succeeds` (second in the file) is what runs the `db,files,sanitize,setup` sync; the bodies after it observe its effects. The three new tests go near the end, after `test_the_drupal_admin_holds_the_administrator_role` and before `test_an_unset_image_is_built_from_this_tree`, so they observe a completed sync.

Until Task 4 lands, all three skip for all six cells. That is the honest state: an assertion over an empty tuple asserts nothing, and the cells are what make it assert something.

- [ ] **Step 1: Add the helper**

Insert after the `PROFILE_HEADER` constant near the top of the file (after line 33):

```python
# `cli/render.py::_FEATURES_LABEL`, and the indent its continuation lines use.
# The block is "  Features  <name>  <reason>" followed by further names at the
# same column, and `render_plan` puts a blank line after it.
FEATURES_LABEL = "  Features  "
```

and this function after `_admin_rows`:

```python
def _active_features(plan: str) -> set[str]:
    """The feature names a dry run's Features block lists.

    Read off the rendered block rather than asked of the resolver, because the
    block is what a developer sees: a feature that resolved but was not
    reported is not usefully active. Both activation paths land here - a
    detected feature and one named by a `features:` key differ only in the
    reason text beside the name (`features/resolver.py`), which is why the
    name is all this reads.
    """
    names: set[str] = set()
    inside = False
    for line in plan.splitlines():
        if line.startswith(FEATURES_LABEL):
            inside = True
        elif not (inside and line.startswith(" " * len(FEATURES_LABEL))):
            # The block is contiguous and followed by a blank line, so the
            # first line that is neither the label nor a continuation ends it.
            if inside:
                break
            continue
        # "(detection disabled by ...)" lands here as one long "name", which
        # matches no feature and so fails the assertion with the reason in it
        # rather than passing quietly.
        names.add(line[len(FEATURES_LABEL) :].split("  ")[0].strip())
    return names - {""}
```

- [ ] **Step 2: Write the three failing tests**

Insert all three after `test_the_drupal_admin_holds_the_administrator_role`:

```python
def test_every_feature_the_cell_names_was_active_in_the_run(stack: Stack, cell: Cell) -> None:
    # The claim: each feature this cell asserts about was actually activated.
    # A feature whose commands work while it is never activated is as broken
    # as one whose commands do not exist - it simply never runs, and every
    # other assertion in this file would still pass. Only a dry run prints the
    # block (`render_plan`), and it resolves features exactly as a real run
    # does; `test_a_whole_sync_succeeds` relies on the same equivalence for
    # the profile label.
    if not cell.features:
        pytest.skip(f"{cell.name} asserts nothing about features")
    plan = stack.engine("--target", "production", "--steps", "db,sanitize,setup", "--dry-run")
    assert plan.returncode == 0, f"stdout:\n{plan.stdout}\n\nstderr:\n{plan.stderr}"
    active = _active_features(plan.stdout)
    expected = {check.feature for check in cell.features}
    assert expected <= active, (
        f"{cell.name} asserts about {sorted(expected - active)}, which this run did not "
        f"activate; it activated {sorted(active)}\n{plan.stdout}"
    )


def test_each_feature_created_and_removed_the_paths_it_claims(stack: Stack, cell: Cell) -> None:
    # The claim: every path a feature's commands must create exists after the
    # sync, and every path they must remove is gone. Asserted on effects
    # because almost every feature command carries `allow_failure`, which the
    # engine reports as OK whether or not it worked - the same reason the
    # profile cells assert `storage:link` and `migrate --force` by effect.
    paths = [(check, path, True) for check in cell.features for path in check.appears]
    paths += [(check, path, False) for check in cell.features for path in check.disappears]
    if not paths:
        pytest.skip(f"{cell.name} asserts no feature paths")
    remote = cell_dir(cell.name) / "remote"
    for check, path, wanted in paths:
        if wanted:
            # Attribution: the `files` step pulls the cell's whole `remote/`
            # tree, so a path that is also on the remote would appear whether
            # or not the feature ran. The install recipe's removal is the
            # other half of this precondition (see `FeatureCheck.appears`) and
            # cannot be checked from here - a recipe that forgot it leaves
            # this assertion passing vacuously.
            assert not (remote / path).exists(), (
                f"{cell.name} claims {check.feature} creates {path}, but the cell's remote "
                "tree carries it too, so the files step would create it either way"
            )
        assert (work_dir(cell.name) / path).exists() == wanted, (
            f"{path} should {'exist' if wanted else 'be gone'} after the sync: "
            f"{check.feature}'s command did not take effect"
        )


def test_the_feature_rule_removed_the_planted_row(stack: Stack, cell: Cell) -> None:
    # The claim: a feature's sanitize rule really empties its table. Same
    # device as `test_the_flush_cache_rule_removed_the_planted_cache_row`: a
    # row is planted in a table no other rule touches, and only this rule can
    # remove it. The remote is never given the row, so the import cannot be
    # what removes it either.
    markers = [check.row_marker for check in cell.features if check.row_marker is not None]
    if not markers:
        pytest.skip(f"{cell.name} asserts no feature sanitize rule")
    for marker in markers:
        count = f"SELECT COUNT(*) FROM `{marker.table}` WHERE {marker.where}"
        stack.sql(marker.insert)
        assert stack.sql(count) == "1", f"the row was not planted into {marker.table}"
        result = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
        assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        assert stack.sql(count) == "0", (
            f"the row survived in {marker.table}: the feature's rule did not take effect"
        )
```

- [ ] **Step 3: Verify collection and that they skip**

Run: `uv run pytest -m matrix -k "feature and mxlaravel" --no-cov --collect-only -q`
Expected: the three new names appear in the collected list, for the `mxlaravel` cell.

(An earlier draft of this step said `-k "mxlaravel and not feat"`, which cannot
work: all three new test names contain "feature", so `and not feat` deselects
exactly the tests the step is meant to show. Task 3's implementer caught it.)

Run: `uv run pytest -m matrix -k "nothingmatchesthis" --no-cov -q`
Expected: exit 5 ("no tests ran") — proving the selection expression works and nothing started a stack.

- [ ] **Step 4: Run the gates**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
```

- [ ] **Step 5: Commit**

```bash
cat > /tmp/ddev-sync-commit-3.txt <<'MSG'
test(matrix): assert what a cell claims about its features

Three bodies, one per kind of evidence a feature can leave: that it was
active at all (a feature that never activates is as broken as one whose
commands do not exist, and every other assertion here would still pass),
that the paths its commands create and remove are as claimed, and that a
planted database row is gone.

All three are keyed on the registry, so they skip for the six profile
cells and say nothing until a cell declares a FeatureCheck. Asserted on
effects throughout, because nearly every feature command carries
allow_failure and the engine reports those as OK whether or not they
worked.

The appears check also refuses a path the cell's own remote tree carries:
the files step would then create it whether or not the feature ran.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/test_matrix.py
git commit -F /tmp/ddev-sync-commit-3.txt
```

---

### Task 4: `mxfeatlaravel` — four features on one Laravel

**Files:**
- Create: `tests/matrix/cells/mxfeatlaravel/install.sh`
- Create: `tests/matrix/cells/mxfeatlaravel/ddev-sync.yaml`
- Create: `tests/matrix/cells/mxfeatlaravel/seed.sql`
- Create: `tests/matrix/cells/mxfeatlaravel/keep-data.txt`
- Create: `tests/matrix/cells/mxfeatlaravel/remote/storage/app/public/matrix-upload.txt`
- Modify: `tests/matrix/cells.py` (one `Cell` entry)

**Interfaces:**
- Consumes: `FeatureCheck` and `Cell.features` (Task 1); node and npm in the web image (Task 2); the three assertions (Task 3).
- Produces: the cell name `mxfeatlaravel`, which Task 6 emits rows for.

**Why a new cell and not `mxlaravel` with packages added:** the filled laravel row says "against a fresh `laravel/laravel` skeleton, profile unchanged". Installing Filament and Livewire into that cell would make the sentence false, and the row would have to be weakened to buy three minutes of runtime (spec decision 1).

- [ ] **Step 1: Write the install recipe**

Create `tests/matrix/cells/mxfeatlaravel/install.sh`:

```sh
# A real Laravel with four features installed the way a project installs
# them. The profile side of this cell is `mxlaravel`'s claim, not this one's -
# what is new here is that `filament`, `livewire`, `inertia` and `node-build`
# are all activatable afterwards, and that every path their commands have to
# create is deleted again before the sync runs. Without that deletion an
# `appears` assertion passes against a path the install left behind, and
# proves nothing (tests/matrix/cells.py::FeatureCheck).
composer create-project --no-interaction --prefer-dist laravel/laravel .

cat > .env <<'ENV'
APP_KEY=base64:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_URL=https://mxfeatlaravel.ddev.site
DB_CONNECTION=mysql
DB_HOST=ddev-mxfeatlaravel-db
DB_PORT=3306
DB_DATABASE=db
DB_USERNAME=db
DB_PASSWORD=db
ENV

php artisan migrate --force --no-interaction

# The same deliberately unapplied migration `mxlaravel` leaves behind. This
# cell runs the laravel profile too, and
# `test_artisan_migrate_applied_the_pending_migration` is keyed on the
# profile, not on the cell name - so the premise has to hold here as well.
php artisan make:migration create_matrix_marker_table --create=matrix_marker --no-interaction

# The three packages whose features this cell proves, unconstrained like the
# skeleton above: the VERIFIED.md row records whatever composer resolved on
# the day. All three are detected by `composer_package` (features/*.yaml), so
# installing them is what makes the features activate.
composer require --no-interaction \
  filament/filament livewire/livewire inertiajs/inertia-laravel

# A panel, because `filament:optimize` caches panels and there is nothing to
# cache without one. A real Filament project runs this once at install time.
php artisan filament:install --panels --no-interaction

# node-build cannot be activated at all without a lockfile:
# `features/package_manager.py::detect_package_manager` keys on
# package-lock.json / yarn.lock / pnpm-lock.yaml and nothing else, and
# `laravel/laravel` ships none of the three (checked at v13.11.0: all three
# paths 404). This one install writes package-lock.json - which `npm ci`, the
# command `${PM_INSTALL}` resolves to, also refuses to run without - and
# populates node_modules.
npm install --no-audit --no-fund

# Every path the sync's own feature commands must create, removed again:
#   public/build                     <- ${PM_RUN} build (node-build)
#   public/vendor/livewire           <- livewire:publish --assets
#   bootstrap/cache/filament         <- filament:optimize, "Caching components"
#   bootstrap/cache/blade-icons.php  <- filament:optimize, "Caching Blade icons"
# The npm install above may itself have written public/build, and
# `filament:install` writes the two caches, so this is a removal and not just
# a precaution.
rm -rf public/build public/vendor/livewire \
       bootstrap/cache/filament bootstrap/cache/blade-icons.php

# The lockfile, on the other hand, must survive: without it the feature is
# not activated and the whole node-build claim silently becomes a skip.
test -f package-lock.json || {
  echo "npm install left no package-lock.json: node-build cannot be activated" >&2
  exit 1
}

# Printed last, because conftest shows the tail of the output: these lines are
# the whole environment the VERIFIED.md rows will claim to have been verified
# against - the three feature packages included.
composer show laravel/framework | grep -E '^(name|versions) '
composer show filament/filament | grep -E '^(name|versions) '
composer show livewire/livewire | grep -E '^(name|versions) '
composer show inertiajs/inertia-laravel | grep -E '^(name|versions) '
php artisan --version
node -v
npm -v
php -v | head -1
composer --version
```

- [ ] **Step 2: Write the three remaining recipe files**

`tests/matrix/cells/mxfeatlaravel/ddev-sync.yaml` — identical to `mxlaravel`'s, with **no** `features:` key, because a `features:` key switches detection off for good (`features/resolver.py`) and all four of this cell's features are meant to be *detected*:

```yaml
# `remote_credentials: explicit` keeps the cell from depending on a .env on
# the remote: this cell is about the features' commands, not about credential
# discovery, which the unit suite already covers exhaustively.
#
# No `features:` key on purpose: writing one switches detection off for the
# whole project (features/resolver.py), and all four features this cell
# proves are meant to be found by detection.
version: 1
targets:
  production:
    host: remote
    user: deploy
    port: 2222
    path: /srv/app/
    stage: production
database:
  remote_credentials: explicit
  credentials:
    name: acme_prod
    user: acme
    password: s3cret
    host: remote-db
    port: "3306"
```

`tests/matrix/cells/mxfeatlaravel/seed.sql` — the same rows `mxlaravel` seeds, for the same reasons (this cell declares the same profile-shaped fields, so the same bodies read them):

```sql
-- Production-shaped data: addresses that look real and bcrypt hashes that
-- are real, so the assertions prove they are gone afterwards rather than
-- proving one placeholder replaced another. Identical to mxlaravel's seed:
-- this cell declares the same profile-shaped registry fields, so the same
-- test bodies read it. What this cell adds is the feature evidence; the
-- profile claim stays mxlaravel's.
INSERT INTO users (name, email, email_verified_at, password, created_at, updated_at) VALUES
  ('Alice Hofer', 'alice.hofer@acme-production.example', NOW(),
   '$2y$10$IkEaQ7LXlNOhZ8Jl3XJqTuU2aGQSdG5RhNRYvHXhPGnsVrFjJpL1S', NOW(), NOW()),
  ('Bruno Kalb', 'bruno.kalb@acme-production.example', NOW(),
   '$2y$10$9cIt3CxGHvHp6f8mJq0KTeWnBz2Rk5y1PqDqTnLrE7uZbVjQmXo2W', NOW(), NOW());

-- One row in each table a tolerated truncate rule empties. A truncate that
-- never ran, or was skipped because the table "did not exist", would leave
-- these behind; with no rows seeded it would pass unnoticed. These tables
-- must stay out of keep-data.txt, whose whole purpose is the opposite.
INSERT INTO sessions (id, user_id, ip_address, user_agent, payload, last_activity) VALUES
  ('matrixsessionid0000000000000000000000000001', 1, '203.0.113.7', 'Mozilla/5.0', 'eyJkYXRhIjoiZmFrZSJ9', 1790000000);
INSERT INTO jobs (queue, payload, attempts, reserved_at, available_at, created_at) VALUES
  ('default', '{"job":"SendInvoice"}', 0, NULL, 1790000000, 1790000000);
INSERT INTO failed_jobs (uuid, connection, queue, payload, exception, failed_at) VALUES
  ('6f0b5c8e-1a2b-4c3d-9e4f-a1b2c3d4e5f6', 'database', 'default', '{"job":"SendInvoice"}', 'RuntimeException: boom', NOW());
```

`tests/matrix/cells/mxfeatlaravel/keep-data.txt`:

```
migrations
```

`tests/matrix/cells/mxfeatlaravel/remote/storage/app/public/matrix-upload.txt` — the laravel profile maps `storage/app/public/`, and the files step fails outright (rsync exit 23) when the remote path is missing:

```
a file production serves from storage/app/public
```

- [ ] **Step 3: Record the naming rule this cell is the first to need**

The comment above `Cell.name` in `tests/matrix/cells.py` gives three reasons the
names are `mx` + letters and digits. This cell adds a fourth, which the first
draft of this plan got wrong and CI would have paid for. Append a bullet to that
comment list, in its existing voice:

```python
    # - no name may contain another cell's name: `.github/workflows/matrix.yaml`
    #   runs one job per cell with `-k "$CELL"`, and `-k` matches a substring of
    #   the test id - `-k mxtypo312` collects one cell's 25 tests while
    #   `-k mxtypo31` collects both TYPO3 cells' 50. A feature cell called
    #   `mxlaravelfeat` would therefore have been swept into the `mxlaravel`
    #   job, which emits the stock-skeleton `laravel` row; hence `mxfeat*`.
```

- [ ] **Step 4: Register the cell**

In `tests/matrix/cells.py`, add this entry to `CELLS` immediately after the `mxlaravel` entry:

```python
    Cell(
        name="mxfeatlaravel",
        # A second laravel cell, not a change to `mxlaravel`: that cell's row
        # says "against a fresh laravel/laravel skeleton, profile unchanged",
        # and installing Filament and Livewire into it would make the sentence
        # false to save three minutes of runtime.
        profile="laravel",
        project_type="laravel",
        docroot="public",
        php_image="php:8.3-cli",
        # The profile-shaped fields are `mxlaravel`'s, verbatim: `Cell`'s
        # docstring defines `None`/`()` as "the profile has no such rule", so
        # emptying them here would make that meaning false and print a lying
        # skip reason. The duplicated profile evidence costs nothing (the same
        # sync runs either way) and is never claimed - the rows this cell
        # emits are feature rows (tools/apply_verified_rows.py).
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
        features=(
            FeatureCheck(
                feature="node-build",
                # The directory, not a manifest path: where inside it the
                # manifest lands is the toolchain's choice, so a pinned
                # filename would break on a version bump without telling
                # anyone anything true. This build wrote
                # `public/build/manifest.json` and no `public/build/.vite/`
                # at all (vite 8.3.4, laravel-vite-plugin 3.2.0).
                appears=("public/build",),
            ),
            FeatureCheck(
                feature="filament",
                # `filament:optimize`'s two steps, "Caching components" and
                # "Caching Blade icons", and the two paths that appear -
                # measured 2026-10-08 against filament/filament v5.10.1.
                # `filament:upgrade` is deliberately absent: it reported
                # success and left no new path, but the probe had not removed
                # its likely targets under `public/`, so their absence proves
                # nothing. See the VERIFIED.md row.
                appears=(
                    "bootstrap/cache/filament/panels",
                    "bootstrap/cache/blade-icons.php",
                ),
            ),
            FeatureCheck(
                feature="livewire",
                # `livewire:publish --assets` copies
                # vendor/livewire/livewire/dist into here - eleven files,
                # the whole of that directory, measured against
                # livewire/livewire v4.4.7.
                appears=("public/vendor/livewire",),
            ),
            # Detection only. The feature declares no commands at all
            # ("Detected for reporting; the build itself is node-build's
            # job"), so that it is reported is the whole of its claim.
            FeatureCheck(feature="inertia"),
        ),
    ),
```

- [ ] **Step 5: Run the cell**

```bash
uv run pytest -m matrix -k mxfeatlaravel --no-cov -v -s
```

Expected: every test either passes or skips, nothing fails. Keep the output: the `[mxfeatlaravel] installed:` block names the versions Task 6 records.

If a path assertion fails, read it as a finding first (spec decision 6): the feature's command may genuinely leave nothing behind, or leave it somewhere else. **Do not invent an assertion to make a feature look proven**, and do not relax one to make the cell green. Report what the run showed.

If the run is slow on macOS, note that `node_modules` lives in the bind-mounted project directory; that is a host-filesystem cost, not a defect.

- [ ] **Step 6: Run the gates**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
```

The unit suite now exercises Task 1's guard loop for the first time, against four real feature names.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/ddev-sync-commit-4.txt <<'MSG'
test(matrix): verify four Laravel features against a real install

node-build, filament, livewire and inertia had no run-time evidence at
all. This cell gives them some: a real laravel/laravel with the three
packages installed, every path their commands must create deleted again
before the sync, and the sync as the only thing that can bring them back.

A second laravel cell rather than packages added to mxlaravel, whose row
says "against a fresh laravel/laravel skeleton, profile unchanged" - a
sentence worth more than the three minutes it costs to keep true.

The recipe runs `npm install` once before anything else needs it:
detect_package_manager keys on a lockfile and nothing else, the skeleton
ships none, and `npm ci` refuses to run without one - so without that
install node-build is never even activated.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/cells.py tests/matrix/cells/mxfeatlaravel
git commit -F /tmp/ddev-sync-commit-4.txt
```

---

### Task 5: `mxfeattypo3` — one feature on TYPO3 13

**Files:**
- Create: `tests/matrix/cells/mxfeattypo3/install.sh`
- Create: `tests/matrix/cells/mxfeattypo3/ddev-sync.yaml`
- Create: `tests/matrix/cells/mxfeattypo3/seed.sql`
- Create: `tests/matrix/cells/mxfeattypo3/remote/public/fileadmin/matrix-upload.txt`
- Create: `tests/matrix/cells/mxfeattypo3/remote/public/uploads/matrix-upload.txt`
- Modify: `tests/matrix/cells.py` (one `Cell` entry)

**Interfaces:**
- Consumes: `FeatureCheck`, `FlushMarker` and `TYPO3_FLUSH_MARKER` from `tests/matrix/cells.py`; the three assertions (Task 3).
- Produces: the cell name `mxfeattypo3`, which Task 6 emits a row for.

**What is worth more than it looks:** `typo3-processedfiles` is the only shipped feature that interpolates a placeholder into an `rm -rf`. `${DDEV_DOCROOT}` reaches that `run:` string through `operations/placeholders.py::_validated_docroot`, which exists because a docroot of `../../etc` would otherwise turn the command into `rm -rf ../../etc/fileadmin/_processed_`. No run has ever exercised it.

- [ ] **Step 1: Write the install recipe**

Create `tests/matrix/cells/mxfeattypo3/install.sh` — `mxtypo313`'s recipe with the host name changed and one directory planted:

```sh
# A real TYPO3 13, built exactly like `mxtypo313` - this cell differs from it
# only in what it asserts, and the recipe has to keep that cell's premises
# true because several test bodies here are keyed on the profile or the
# project type, not on the cell name. `:^13` is explicit because composer's
# default for this package is the newest major, which is 14.
composer create-project --no-interaction --prefer-dist 'typo3/cms-base-distribution:^13' .

# TYPO3's non-interactive installer reads these instead of prompting.
TYPO3_DB_DRIVER=mysqli \
TYPO3_DB_USERNAME=db \
TYPO3_DB_PASSWORD=db \
TYPO3_DB_PORT=3306 \
TYPO3_DB_HOST=ddev-mxfeattypo3-db \
TYPO3_DB_DBNAME=db \
TYPO3_SETUP_ADMIN_EMAIL=admin@example.invalid \
TYPO3_SETUP_ADMIN_USERNAME=installer \
TYPO3_SETUP_ADMIN_PASSWORD=Password.1234 \
TYPO3_PROJECT_NAME=matrix \
TYPO3_SERVER_TYPE=other \
  vendor/bin/typo3 setup --force --no-interaction

# A column the core schema expects, removed after the installer created it,
# so `test_the_typo3_schema_step_restored_the_missing_column` - which is keyed
# on `project_type == "typo3"` and therefore runs for this cell too - has its
# premise. `--skip-ssl`: local-db runs with no certificate, while this image's
# client requires a verified TLS connection by default.
mariadb --skip-ssl --host=ddev-mxfeattypo3-db --user=db --password=db db \
  -e 'ALTER TABLE tt_content DROP COLUMN header_link'

# The stale processing folder a real TYPO3 accumulates, planted here rather
# than in the cell's `remote/` tree for two reasons: a file under `remote/` is
# asserted to have *arrived* by `test_the_files_step_pulled_the_remote_tree`,
# which the feature's own `rm -rf` would then falsify; and the files step
# pulls `public/fileadmin/` without `--delete` (no profile sets `delete:`, and
# `operations/files.py` defaults it to False), so nothing between here and the
# setup step can remove it. The feature's command is the only candidate.
mkdir -p public/fileadmin/_processed_/0/a
printf 'a stale processed image\n' > public/fileadmin/_processed_/0/a/csm_matrix_abcdef.png

# And checked, because a `disappears` assertion has no attribution guard of
# its own: `FeatureCheck.appears` is protected by the test refusing a path the
# cell's remote tree also carries, but a `disappears` path that was never
# created passes vacuously - the sync would "have removed" something absent.
# This is the only place that can catch it.
test -d public/fileadmin/_processed_ || {
  echo "install.sh did not plant public/fileadmin/_processed_;" \
       "the cell's disappears-assertion would be vacuous" >&2
  exit 1
}

composer show typo3/cms-core | grep -E '^(name|versions) '
php -v | head -1
composer --version
```

- [ ] **Step 2: Write the remaining recipe files**

`tests/matrix/cells/mxfeattypo3/ddev-sync.yaml` — `mxtypo313`'s, plus the `features:` key this feature needs:

```yaml
# `remote_credentials: explicit` keeps the cell from depending on a .env on
# the remote: this cell is about the feature's commands, not about credential
# discovery, which the unit suite already covers exhaustively.
version: 1
targets:
  production:
    host: remote
    user: deploy
    port: 2222
    path: /srv/app/
    stage: production
database:
  remote_credentials: explicit
  credentials:
    name: acme_prod
    user: acme
    password: s3cret
    host: remote-db
    port: "3306"
# Requested, not detected: `typo3-processedfiles` has no detect block on
# purpose - "whether you care is a judgement, not a property of the project"
# (features/typo3-processedfiles.yaml) - so a `features:` key is the only way
# it can be active. Writing one also switches detection off for this project
# (features/resolver.py), which costs nothing here: this is the only feature
# the cell is about.
features:
  - typo3-processedfiles
```

`tests/matrix/cells/mxfeattypo3/seed.sql` — a copy of `mxtypo313`'s file. Read that file and copy it verbatim, then replace its first comment block with:

```sql
-- A copy of mxtypo313's seed, because this cell declares the same
-- profile-shaped registry fields and the same test bodies read it. What this
-- cell adds is the feature evidence; the typo3@13 profile claim stays
-- mxtypo313's. be_users carries the addresses and hashes the sanitize rules
-- rewrite; the rows below are all the remote holds, so "a hash that is not
-- the profile's reset value" can only mean a rule did not reach them.
```

Keep everything else in the file byte-identical, including the rows in
`be_sessions`, `fe_sessions`, `sys_log` and `fe_users` and their comments —
`truncated_tables` on this cell names the same three tables, so those rows
carry the same premise.

**`sys_file_processedfile` gets no row here**: the row is planted by
`test_the_feature_rule_removed_the_planted_row` into the *local* database
after the import, which is what makes its disappearance attributable to the
rule rather than to the import.

`tests/matrix/cells/mxfeattypo3/remote/public/fileadmin/matrix-upload.txt`:

```
a file production serves from fileadmin
```

`tests/matrix/cells/mxfeattypo3/remote/public/uploads/matrix-upload.txt`:

```
a file production serves from uploads
```

(The typo3 profile maps both `public/fileadmin/` and `public/uploads/`, and the files step fails with rsync exit 23 when a remote path is missing.)

- [ ] **Step 3: Register the cell**

In `tests/matrix/cells.py`, add this entry to `CELLS` immediately after the `mxtypo313` entry:

```python
    Cell(
        name="mxfeattypo3",
        # A second typo3@13 cell for the same reason `mxfeatlaravel` is a
        # second laravel cell: the typo3@13 row rests on a stock install, and
        # this one is not stock.
        profile="typo3@13",
        project_type="typo3",
        docroot="public",
        php_image="php:8.3-cli",
        # `mxtypo313`'s profile-shaped fields, verbatim - see the note on
        # `mxfeatlaravel` for why they are not emptied.
        required_tables=("be_sessions", "sys_log", "be_users"),
        admin_user="ddev",
        password_column=("be_users", "password"),
        production_url=None,
        truncated_tables=("be_sessions", "sys_log", "fe_sessions"),
        identity_column="username",
        seeded_users=("editor.hofer", "editor.kalb"),
        flush_marker=TYPO3_FLUSH_MARKER,
        features=(
            FeatureCheck(
                feature="typo3-processedfiles",
                # The feature contributes both a sanitize rule and a setup
                # command, so both get evidence.
                #
                # The rule is `TRUNCATE TABLE sys_file_processedfile` with no
                # `tolerate_missing_table`, and that is correct: the table is
                # defined in typo3/cms-core's own ext_tables.sql (line 120 at
                # v13.4.2, checked), so a core install always has it. Every
                # column there is auto-increment, nullable or carries a
                # DEFAULT, which is why this two-column INSERT is valid under
                # MariaDB's strict mode.
                row_marker=FlushMarker(
                    table="sys_file_processedfile",
                    insert=(
                        "INSERT INTO sys_file_processedfile (identifier, task_type) "
                        "VALUES ('matrix_marker', 'Image.CropScaleMask')"
                    ),
                    where="identifier = 'matrix_marker'",
                ),
                # The setup command is
                # `rm -rf ${DDEV_DOCROOT}/fileadmin/_processed_`, and this is
                # the only shipped feature that interpolates a placeholder
                # into an `rm -rf` - the reason
                # `operations/placeholders.py::_validated_docroot` exists.
                # Until this cell, no run had exercised it.
                disappears=("public/fileadmin/_processed_",),
            ),
        ),
    ),
```

- [ ] **Step 4: Run the cell**

```bash
uv run pytest -m matrix -k mxfeattypo3 --no-cov -v -s
```

Expected: every test either passes or skips, nothing fails. Keep the output: the `[mxfeattypo3] installed:` block names the TYPO3 and PHP versions Task 6 records.

Same rule as Task 4: a failing assertion is a finding to report, not an assertion to soften.

- [ ] **Step 5: Run the gates**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
```

- [ ] **Step 6: Commit**

```bash
cat > /tmp/ddev-sync-commit-5.txt <<'MSG'
test(matrix): verify typo3-processedfiles against a real TYPO3 13

The feature contributes a sanitize rule and a setup command, and neither
had ever run. Both get evidence here: a row planted in
sys_file_processedfile after the import, which only the rule can remove,
and a stale processing folder planted by the install, which only the
setup command's `rm -rf` can.

The second one is worth more than it looks. This is the only shipped
feature that interpolates ${DDEV_DOCROOT} into an `rm -rf`, which is why
operations/placeholders.py validates that value at all - and no run had
exercised the path until now.

A second typo3@13 cell rather than additions to mxtypo313, whose row
rests on a stock install.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/cells.py tests/matrix/cells/mxfeattypo3
git commit -F /tmp/ddev-sync-commit-5.txt
```

---

### Task 6: The rows

**Files:**
- Modify: `tools/apply_verified_rows.py`
- Modify: `tests/test_apply_verified_rows.py`
- Modify: `profiles/VERIFIED.md`

**Interfaces:**
- Consumes: `Cell.features` and `FeatureCheck.feature` (Task 1); the two green runs (Tasks 4 and 5).
- Produces: nothing later depends on.

**The defect this task exists to prevent:** `emit_row` keys its row by `cell.profile`. `mxfeatlaravel.profile` is `"laravel"`, so as the tool stands today a green feature cell would emit a row that `--apply` writes into the **profile** `laravel` row — replacing a run that verified the profile's credentials, env step, sanitize rules and setup commands with one that asserts almost none of it. The feature table's rows are keyed by feature name, which is what a feature cell has to emit.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_apply_verified_rows.py`:

```python
def test_a_feature_cell_emits_one_row_per_feature_and_no_profile_row() -> None:
    # The silent failure this guards: `mxfeatlaravel` runs the laravel
    # profile, so a row keyed by the profile would be applied to the
    # *profile* laravel row - replacing a run that verified that profile's
    # credentials, env step, sanitize rules and setup commands with one that
    # asserts almost none of it.
    rows = emit_row("mxfeatlaravel", image="ddev-sync:ci", run="r").splitlines()
    keys = [row.strip().strip("|").split("|")[0].strip() for row in rows]
    assert keys == ["node-build", "filament", "livewire", "inertia"]
    assert "laravel" not in keys
    assert all("ddev-sync:ci on php:8.3-cli" in row for row in rows)


def test_a_profile_cell_still_emits_exactly_one_row() -> None:
    assert emit_row("mxlaravel", image="i", run="r").count("\n") == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_apply_verified_rows.py -v --no-cov`
Expected: `test_a_feature_cell_emits_one_row_per_feature_and_no_profile_row` FAILS with `keys == ["laravel"]`; the second one passes already.

- [ ] **Step 3: Make `emit_row` emit the right keys**

Replace the body of `emit_row` in `tools/apply_verified_rows.py` (keep the `KeyError` guard as it is) so the tail reads:

```python
    cell = _BY_NAME[name]
    today = datetime.now(UTC).date().isoformat()
    # A profile cell fills one row, keyed by its **profile** label, not its
    # own name (`typo3@13`, while the cell is `mxtypo313`): the cell name is
    # the input, the profile is the output, and swapping them fills no rows
    # and fails silently - hence the test using the one pair where the two
    # strings differ.
    #
    # A feature cell fills one row per feature it checks, keyed by the
    # feature name, and *no* profile row. `mxfeatlaravel` runs the laravel
    # profile, so a profile-keyed row would be applied to the profile
    # `laravel` row and replace a run that verified that profile's
    # credentials, env step, sanitize rules and setup commands with one that
    # asserts almost none of it.
    keys = [check.feature for check in cell.features] or [cell.profile]
    return "\n".join(f"| {key} | {image} on {cell.php_image} | {today} | {run} |" for key in keys)
```

and update the docstring's first line to `"""The Markdown row or rows recording that `name`'s cell passed."""`.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_apply_verified_rows.py -v --no-cov`
Expected: PASS, all cases.

The workflow needs no change: `.github/workflows/matrix.yaml` prints whatever `--emit` returns inside a code fence, and `--apply` already skips non-row lines and reads each row independently.

- [ ] **Step 5: Retire the two registry comments that only held until now**

Task 4's implementer refused to commit this plan's claim that "the rows this cell
emits are feature rows", because it was false until the step above. In its place
it committed, in the `mxfeatlaravel` entry of `tests/matrix/cells.py`, a sentence
recording the behaviour you have just changed:

> Note that `tools/apply_verified_rows.py` still keys every emitted row by
> `profile`, so `--emit mxfeatlaravel` yields a `laravel` row today -
> applying one would overwrite `mxlaravel`'s evidence with this cell's.

That is now wrong, and leaving it would be exactly the defect this repository
treats as real. Replace those sentences with the plain fact: the rows this cell
fills are `VERIFIED.md`'s feature rows, one per `FeatureCheck`, and no profile
row is emitted for it. Check the `mxfeattypo3` entry for the same wording and
fix it too if Task 5 carried it over. **Verify by running
`uv run python -m tools.apply_verified_rows --emit mxfeatlaravel` and reading
its output** — do not take this plan's word for what the tool now does.

- [ ] **Step 6: Apply the evidence cells**

From the repository root, using the run URL or local timestamp as `--run`:

```bash
uv run python -m tools.apply_verified_rows --emit mxfeatlaravel \
  --image ddev-sync:dev --run "local pytest -m matrix run <ISO timestamp>" > /tmp/ddev-sync-rows.md
uv run python -m tools.apply_verified_rows --emit mxfeattypo3 \
  --image ddev-sync:dev --run "local pytest -m matrix run <ISO timestamp>" >> /tmp/ddev-sync-rows.md
uv run python -m tools.apply_verified_rows --apply /tmp/ddev-sync-rows.md
git diff profiles/VERIFIED.md
```

Then replace the *Verified against* cell of each of the five rows by hand with the versions the run printed — `--emit` yields only `<image> on <php image>`, and the six filled profile rows say more than that. Take the versions from the `[mxfeatlaravel] installed:` and `[mxfeattypo3] installed:` blocks of Tasks 4 and 5, in the shape the laravel row uses:

`laravel/framework <v>, filament/filament <v>, livewire/livewire <v>, inertiajs/inertia-laravel <v>, node <v> with npm <v>, PHP <v> (php:8.3-cli), ddev-sync:dev built from <short sha>`

and for the TYPO3 row:

`typo3/cms-core <v> (stock cms-base-distribution ^13), PHP <v> (php:8.3-cli), ddev-sync:dev built from <short sha>`

- [ ] **Step 7: Write the five Notes cells**

Replace each row's *Notes* cell. These say exactly what the runs showed and no more — the rule the file states in its own header. Substitute `<v>` from the run; change nothing else.

**`node-build`:**

> Verified against a `laravel/laravel` skeleton (`mxfeatlaravel` cell), npm <v> on node <v>. **Proven by effect:** detection fires on this project's real `package.json` (`file_exists: package.json` and `npm_script: build`, both required), and `${PM_RUN} build` writes `public/build/` — the install deletes that directory first and the cell's remote tree does not carry it, so only the build can have created it. The directory is asserted, not a manifest path, so the claim does not depend on where inside it the manifest lands: this build wrote `public/build/manifest.json` and no `public/build/.vite/` at all (vite 8.3.4, laravel-vite-plugin 3.2.0), which is what disproved the spec's earlier justification that Vite 5 had moved the manifest there. **Found, and the recipe works around it rather than the engine:** `detect_package_manager` keys on a lockfile and nothing else, and `laravel/laravel` ships none of `package-lock.json` / `yarn.lock` / `pnpm-lock.yaml` (all three 404 at v13.11.0), so the feature cannot be activated on a fresh skeleton at all until something runs an install — the cell's recipe runs `npm install` once for exactly that reason, and `${PM_INSTALL}` (`npm ci`) then has the lockfile it also requires. **Not run:** yarn and pnpm, which the unit suite covers but no install does; a non-default `script` setting; a project whose build writes somewhere other than `public/build/`.

**`filament`:**

> Verified against filament/filament <v> on a `laravel/laravel` skeleton with a panel installed (`mxfeatlaravel` cell). **Proven by effect:** `filament:optimize` — the install deletes `bootstrap/cache/filament/` and `bootstrap/cache/blade-icons.php`, and both are back after the sync. Needed as effect evidence rather than exit status because the command is `allow_failure`, which the engine reports as OK whether or not it worked. **`filament:upgrade` was exercised, effect not observed:** it runs and reports success, but its likely outputs are assets published under `public/` which `filament:install --panels` had already created and nothing removed, so their presence afterwards is not attributable to it. **Precondition worth recording:** a panel has to exist or there is nothing to cache, so the cell runs `filament:install --panels` as a real project would; and Filament's dependency tree needs `ext-intl`, which a bare `php:*-cli` lacks — the matrix web image builds it for TYPO3's sake. **Not run:** Filament 3 and 4; a project with several panels; `filament:upgrade`'s own effect.

**`livewire`:**

> Verified against livewire/livewire <v> on a `laravel/laravel` skeleton (`mxfeatlaravel` cell). **Proven by effect:** `livewire:publish --assets` — the install deletes `public/vendor/livewire/` and the directory is back after the sync. Effect rather than exit status because the command is `allow_failure`. **Not run:** Livewire 2 and 3; the contents of the published directory are not asserted file by file, only that it was published.

**`inertia`:**

> Verified against inertiajs/inertia-laravel <v> on a `laravel/laravel` skeleton (`mxfeatlaravel` cell). The feature declares no commands — "Detected for reporting; the build itself is node-build's job" — so being reported is the whole of its claim, and that is what was checked: the dry run's `Features` block names it, detected from the real `composer.json`. A feature whose detect block never fires is as broken as one whose commands do not exist, which is why this is asserted at all rather than left as "nothing to verify".

**`typo3-processedfiles`:**

> Verified against typo3/cms-core <v> (stock `cms-base-distribution ^13`, `mxfeattypo3` cell), activated by a `features:` key because the feature has no detect block by design. **Proven by effect, both halves:** the `clear-processed-files` rule — a row planted in `sys_file_processedfile` after the import is gone after a `sanitize` run, and the remote never carried it, so the import cannot be what removed it; and the setup command `rm -rf ${DDEV_DOCROOT}/fileadmin/_processed_` — the install plants `public/fileadmin/_processed_/0/a/csm_matrix_abcdef.png` and the directory is gone after the sync, with the `files` step ruled out because no profile sets `delete:` on a `files.paths` entry. The second is the first run of the only shipped feature that interpolates `${DDEV_DOCROOT}` into an `rm -rf`, the reason `operations/placeholders.py::_validated_docroot` exists, and the accept branch of that guard is what `docroot: "public"` exercised. The rule carries no `tolerate_missing_table`, which is correct: the table is in `typo3/sysext/core/ext_tables.sql` itself (line 123 in the v13.4.35 this cell installed). **Two limits on the `rm -rf` evidence, both stated because the run does not close them.** The plan renderer prints `${DDEV_DOCROOT}` unexpanded, so no captured output shows the resolved path; what the run shows is that the planted directory is gone and that exactly one command in the whole setup step *names* that path. And naming is weaker than causing: `extension:setup` and `cache:flush` run in the same step, and neither was shown unable to clear `fileadmin/_processed_` — TYPO3's own "clear processed files" is a separate Maintenance-module job, which makes the attribution very likely but not demonstrated. **Not run:** a docroot other than `public`; a `sys_file_storage` with a non-default `processingfolder`, which the feature's own comment already records as out of its reach; TYPO3 9–12 and 14.

- [ ] **Step 8: Run the gates**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
```

- [ ] **Step 9: Commit**

```bash
cat > /tmp/ddev-sync-commit-6.txt <<'MSG'
docs(profiles): record what the two feature cells verified

emit_row keyed every row by the cell's profile, which for a feature cell
is the wrong table: mxfeatlaravel runs the laravel profile, so its green
run would have been applied to the *profile* laravel row - replacing a
run that verified that profile's credentials, env step, sanitize rules
and setup commands with one that asserts almost none of it. A feature
cell now emits one row per feature and no profile row.

Five of the feature table's eight rows are filled from the two runs.
filament:upgrade is recorded as exercised with no effect observed: it
reports success, but its likely outputs were already on disk and nothing
removed them first, so nothing about it is attributable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tools/apply_verified_rows.py tests/test_apply_verified_rows.py profiles/VERIFIED.md
git commit -F /tmp/ddev-sync-commit-6.txt
```

---

## Out of scope

Named so a reviewer does not read their absence as an omission (spec decision 5 and "Out of scope"):

- `horizon` (needs Redis), `symfony-search` (Elasticsearch) and `typo3-solr` (Solr). Each is a new service in the matrix stack, and the mechanism this plan introduces has not yet been shown to work on a single feature. They get their own spec once it has.
- `typo3-solr`'s doubtful command. Its VERIFIED.md note stands: `solr:initializesolrconnections` could not be located in the package, and the feature may need correcting or removing. **Do not touch it here.**
- Any change to the six existing cells, their recipes, or their rows.
- Any change under `src/ddev_sync/`.
- A feature cell for a framework the matrix does not already install.

## Final check before handing the branch on

- [ ] `uv run pytest` green; `ruff check`, `ruff format --check`, `mypy src tests tools` clean.
- [ ] `uv run pytest -m matrix -k "mxfeatlaravel or mxfeattypo3" --no-cov -v` green in one run, both cells together.
- [ ] `git log --oneline` shows six commits, linear, no merges, each message ending with the `Co-Authored-By` line.
- [ ] `git status` clean.
- [ ] Nothing pushed.
