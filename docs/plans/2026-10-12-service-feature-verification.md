# Verifying the service-features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill `profiles/VERIFIED.md`'s last two fillable feature rows — `horizon` and `symfony-search` — and delete `typo3-solr`, whose one command does not exist.

**Architecture:** Two services join `tests/matrix/docker-compose.yaml` behind `profiles:`, selected per cell by a new `Cell.services` field, so the eight existing cells start exactly what they start today. `FeatureCheck` gains two concrete marker fields — `redis_marker` and `index_marker` — beside the existing `row_marker`, each read by a new `Stack` method in the shape of `Stack.sql`. Two new cells declare them.

**Tech Stack:** Python 3.12, pytest, Docker Compose, composer, Redis 7, Elasticsearch 9, MariaDB 11.

**Spec:** `docs/specs/2026-10-10-service-feature-verification-design.md`. Read it. It carries the measurements and the reasoning; this plan carries the code.

**One deliberate divergence from the spec's sketch:** it names the Elasticsearch reader `Stack.es(path)`, a generic HTTP getter. This plan narrows it to `Stack.es_count(index) -> int`, because a count is the only thing either marker needs and a generic getter would push the 404-reads-as-zero decision out to every caller. Where the two disagree, the plan is the later thought.

## Global Constraints

- Tests: `uv run pytest`. Default run excludes the `matrix` and `integration` markers. Coverage is **100 % line and branch** on `ddev_sync` only (`--cov=ddev_sync`); `tests/` and `tools/` are not measured. `# pragma: no cover` is forbidden repo-wide.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src tests tools` must be clean **at the committed state**. Ruff line length 100, rules `E,F,I,UP,B,SIM,RUF,ANN`; mypy strict. Run each **separately** so its own exit code is visible — **never** `cmd 2>&1 | tail -1 && next`; a pipeline's status is `tail`'s, and that trap has cost this project three times.
- **`addopts` carries `-q`**, which cancels a single `-v`. A matrix run needs `-vv -rA` for per-test pass/skip lines and skip reasons.
- **A cell run deletes its installed project and remote tree unless `DDEV_SYNC_MATRIX_KEEP=1` is set.** Set it on every cell run in this plan; later tasks and reviewers read those trees.
- **Nothing under `src/ddev_sync/` may change** except `features/typo3-solr.yaml`'s deletion (Task 1) — and `features/` is not under `src/`. If a cell reveals a defect in a feature definition, report it rather than editing.
- **Do not touch the six profile cells (`mxlaravel`, `mxtypo313`, `mxtypo312`, `mxsymfony`, `mxwordpress`, `mxdrupal`) or the two existing feature cells (`mxfeatlaravel`, `mxfeattypo3`)**, their recipes, or their `VERIFIED.md` rows.
- **Cell names:** `mx` + letters and digits, and **no cell name may contain another's** — `.github/workflows/matrix.yaml` runs `-k "$CELL"` per cell and `-k` matches substrings. `tests/matrix/test_feature_registry.py` enforces both.
- Comments explain *why*, not *what*, and must be **true of the code beside them**. This repository treats a false comment as a defect in its own right: the previous branch spent **seven** fix rounds across three tasks on that one failure mode, and several of those rounds repaired one false sentence while introducing another. Do not write a sentence asserting what another file always, never, or every-time does unless you measured it. If a comment this plan dictates does not match what you observe, **report the mismatch instead of committing it** — that happened four times on the previous branch and was right every time.
- Conventional Commits. Footer exactly `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Write each message to a file and `git commit -F <file>`; never nest a heredoc inside another.
- **Do not push.** No branches, no `git clean` (it destroys the git-ignored SDD workspace).

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has its test assigned to the task that owns the code.

1. **A `Cell.services` entry naming a compose profile that does not exist** must fail in the ordinary suite, not at `compose up`. A typo should cost a collection error, not a cell run. (Task 3)
2. **`Stack.redis` and `Stack.es` must raise on a failed call, never return something that reads as "zero".** `Stack.sql` already raises for exactly this reason: several assertions read an empty result as "no rows", and a query that errored must not pass for that. (Task 4)
3. **`Stack.es_count` reading an index that does not exist yet** must read as **absent, count 0**, not as an error, or `IndexMarker.expected_before = 0` can never be asserted. This is **not** a defensive branch: measured, `fos:elastica:populate` resets and recreates its index, and before it has ever run `_cat/indices` is empty — so an absent index is the ordinary pre-sync state and the 404 is on the main path. (Task 4)
4. **A cell declaring `services=()` must start exactly the four services it starts today.** Six profile cells and two feature cells depend on this; decision 2 exists for it. (Task 3)
5. **`pcntl` in the shared web image must change no existing cell's plan.** The previous branch set the pattern for proving this: it grepped every recipe to show no other cell touches npm, so `node-build` could not activate elsewhere. The same evidence is owed here. (Task 2)

---

## File Structure

| Path | Responsibility |
|---|---|
| `features/typo3-solr.yaml` | **Delete.** Its one command does not exist. |
| `tests/features/test_typo3_symfony_features.py` | **Modify.** Four references to the deleted feature. |
| `profiles/VERIFIED.md` | **Modify.** `typo3-solr`'s row becomes the removal record; `horizon` and `symfony-search` are filled from their runs. |
| `tests/matrix/web/Dockerfile` | **Modify.** `pcntl`, without which `composer require laravel/horizon` fails. |
| `tests/matrix/docker-compose.yaml` | **Modify.** `redis` and `elasticsearch`, each behind a `profiles:` entry of its own name. |
| `tests/matrix/stack.py` | **Modify.** `up` passes `--profile` per `cell.services` entry; `redis()` and `es()` readers. |
| `tests/matrix/cells.py` | **Modify.** `RedisMarker`, `IndexMarker`, two `FeatureCheck` fields, `Cell.services`, two cell entries. |
| `tests/matrix/test_matrix.py` | **Modify.** Two test bodies reading the new markers. |
| `tests/matrix/test_feature_registry.py` | **Modify.** An unmarked guard that every `Cell.services` entry names a real compose profile. |
| `tests/matrix/cells/mxfeathorizon/` | **Create.** `install.sh`, `ddev-sync.yaml`, `seed.sql`, `keep-data.txt`, `remote/storage/app/public/matrix-upload.txt`. |
| `tests/matrix/cells/mxfeatsymfony/` | **Create.** `install.sh`, `ddev-sync.yaml`, `seed.sql`, `remote/public/uploads/matrix-upload.txt`, plus the `fos_elastica` config and entity as recipe files. |

`tools/apply_verified_rows.py` needs no change: `emit_row` already emits one row per `FeatureCheck` for a cell that declares features, and no profile row for it.

---

### Task 1: Delete `typo3-solr`

**Files:**
- Delete: `features/typo3-solr.yaml`
- Modify: `tests/features/test_typo3_symfony_features.py`
- Modify: `profiles/VERIFIED.md` (the `typo3-solr` row only)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. Later tasks do not reference this feature.

**Why deletion and not verification** (spec decision 1, measured): against `TYPO3-Solr/ext-solr@main`, `initializesolrconnections` has **zero** occurrences anywhere in the repository; there is no `Configuration/Commands.php`, TYPO3's mechanism for registering console commands; `Configuration/Services.yaml` carries no `console.command` tag; and `Classes/` has no `Command` directory. The feature's only command is fictional, and `allow_failure: true` is why no sync ever reported it.

- [ ] **Step 1: Confirm the measurement before acting on it**

Do not delete a shipped feature on this plan's word. Re-establish all four points yourself:

```bash
gh api 'search/code?q=repo:TYPO3-Solr/ext-solr+initializesolrconnections' --jq '.total_count'
gh api repos/TYPO3-Solr/ext-solr/contents/Configuration --jq '.[].name' | grep -c Commands.php || echo "kein Commands.php"
curl -sfL https://raw.githubusercontent.com/TYPO3-Solr/ext-solr/main/Configuration/Services.yaml | grep -c "console.command" || echo "kein console.command"
gh api repos/TYPO3-Solr/ext-solr/contents/Classes --jq '.[].name' | grep -c '^Command$' || echo "kein Command-Verzeichnis"
```

Expected: `0`, then three "kein …" lines. **If any of these disagrees, stop and report it** — the feature may be fixable, which changes the task entirely.

- [ ] **Step 2: Find every reference**

```bash
grep -rn "typo3-solr\|typo3_solr" features profiles src tests tools
```

Expected, before the change: `features/typo3-solr.yaml:1`, `profiles/VERIFIED.md:51`, and three sites in `tests/features/test_typo3_symfony_features.py`.

- [ ] **Step 3: Delete the feature file and run the suite to see what breaks**

```bash
git rm features/typo3-solr.yaml
uv run pytest 2>&1 | tail -20
```

Expected: FAIL. `tests/features/test_typo3_symfony_features.py` loads the feature in `test_typo3_solr_is_scoped_to_typo3_and_requeues_the_index`, in `test_symfony_search_reindex_commands_all_tolerate_failure`'s loop, and in `test_composer_package_detections_are_pinned`. Record the real failures — they are this step's evidence that the grep found every consumer.

- [ ] **Step 4: Remove the three test sites**

- Delete `test_typo3_solr_is_scoped_to_typo3_and_requeues_the_index` entirely.
- In `test_symfony_search_reindex_commands_all_tolerate_failure`, narrow the loop to `("symfony-search",)` and change its docstring comment so it no longer speaks of "a Solr or Elastica reindex" — it is Elastica only now.
- In `test_composer_package_detections_are_pinned`, drop the `typo3-solr` assertion and keep the `symfony-search` one.

Do not leave a skipped or renamed placeholder behind: a deleted feature's tests are deleted, and `tests/features/test_all_features.py` enumerates from the filesystem (`FeatureRegistry.available()`), so it adjusts itself.

- [ ] **Step 5: Rewrite the `VERIFIED.md` row as the removal record**

Replace the `typo3-solr` row's *Notes* cell. The row stays in the table — an empty row invites someone to re-add the feature from the same guess, and a missing row loses the finding. The *Verified against* / *Date* / *By* cells stay empty, because nothing was verified:

> **Removed, not verified.** `features/typo3-solr.yaml` is deleted as of this change. Its one declared command, `vendor/bin/typo3 solr:initializesolrconnections`, does not exist, and `apache-solr-for-typo3/solr` registers no CLI command at all. Four checks against `TYPO3-Solr/ext-solr@main`, 2026-10-10: `initializesolrconnections` has zero occurrences anywhere in the repository; there is no `Configuration/Commands.php`, which is TYPO3's mechanism for registering console commands; `Configuration/Services.yaml` carries no `console.command` tag; and `Classes/` has no `Command` directory. `allow_failure: true` is why no sync ever reported this. **Rebuilding a Solr index after an import remains a real need** — ext-solr drives connection setup from TypoScript and site configuration, and whatever the right mechanism is (a scheduler task, an index-queue operation), it is not this command. Reinstating the feature means establishing that mechanism first.

- [ ] **Step 6: Run the gates**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

Each separately. The suite must be green and the count lower than before by the deleted test.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/ddev-sync-commit-1.txt <<'MSG'
fix(features): delete typo3-solr, whose one command does not exist

`vendor/bin/typo3 solr:initializesolrconnections` is not a command
apache-solr-for-typo3/solr provides, and that extension registers no CLI
command at all. Checked four ways against its own repository:
`initializesolrconnections` has zero occurrences anywhere in it, there is
no Configuration/Commands.php, Configuration/Services.yaml carries no
console.command tag, and Classes/ has no Command directory.

`allow_failure: true` is why this shipped unnoticed - the step could
never fail a sync, so nothing ever reported that it did nothing. The
VERIFIED.md row stays as the record of why the feature is gone, because
an empty row invites the same guess back.

Rebuilding a Solr index after an import is still a real need. ext-solr
drives connection setup from TypoScript and site configuration, so
whatever the right mechanism is, it is not a console command - and
establishing it is a precondition for reinstating any such feature.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add -A features profiles/VERIFIED.md tests/features/test_typo3_symfony_features.py
git commit -F /tmp/ddev-sync-commit-1.txt
```

---

### Task 2: `pcntl` in the matrix web image

**Files:**
- Modify: `tests/matrix/web/Dockerfile`

**Interfaces:**
- Consumes: nothing.
- Produces: `pcntl` on the web container's PHP, which Task 5's `composer require laravel/horizon` needs to resolve at all.

**Why:** measured — `laravel/horizon` v5.50.0 lists `ext-pcntl` and `ext-posix` in `require`, not `suggest`, so composer refuses the package without them. In an image built from this tree on `php:8.3-cli`, `posix` is present and **`pcntl` is not**.

- [ ] **Step 1: Reproduce the gap**

```bash
docker build --build-arg PHP_IMAGE=php:8.3-cli -t ddev-sync-web-probe tests/matrix/web
docker run --rm ddev-sync-web-probe php -r 'var_dump(extension_loaded("pcntl"), extension_loaded("posix"));'
```

Expected: `false` then `true`. If `pcntl` is already true, stop and report — the Dockerfile or the base image changed and this task is moot.

- [ ] **Step 2: Add the extension**

In `tests/matrix/web/Dockerfile`, add `pcntl` to the existing `docker-php-ext-install` list, which today reads `docker-php-ext-install -j"$(nproc)" pdo_mysql mysqli intl zip gd`. It needs no `-dev` package, unlike `gd` and `intl`, so nothing is added to the `apt-get install` line.

Extend the comment block above that `RUN` so it says why — in the file's existing voice, and claiming only this:

```
# `pcntl` is here for laravel/horizon, which lists it (and `posix`) in
# `require` rather than `suggest`, so composer refuses the package without
# it. Measured on php:8.3-cli: `posix` is already present, `pcntl` is not.
```

- [ ] **Step 3: Verify both PHP bases**

```bash
docker build --build-arg PHP_IMAGE=php:8.3-cli -t ddev-sync-web-probe tests/matrix/web
docker run --rm ddev-sync-web-probe sh -c 'php -r "var_dump(extension_loaded(\"pcntl\"));"; php -v | head -1; composer --version; node -v; wp --allow-root --version'
docker build --build-arg PHP_IMAGE=php:8.2-cli -t ddev-sync-web-probe82 tests/matrix/web
docker run --rm ddev-sync-web-probe82 php -r 'var_dump(extension_loaded("pcntl"));'
```

Expected: `true` on both. The 8.3 probe's extra lines prove nothing already in the image was displaced — `mxtypo312` names `php:8.2-cli`, so both bases matter.

- [ ] **Step 4: Prove it inert for the cells that do not use it (Review Focus 5)**

`pcntl` is in the shared image, so it reaches all ten cells. Show that no existing cell's plan changes:

```bash
grep -rln "horizon\|pcntl" tests/matrix/cells/*/install.sh tests/matrix/cells/*/ddev-sync.yaml || echo "kein bestehendes Rezept nennt horizon oder pcntl"
grep -n "pcntl" features/*.yaml profiles/*.yaml || echo "kein Feature und kein Profil hängt an pcntl"
```

Expected: both lines. No feature detects on a PHP extension (`features/detect.py`'s predicates are `composer_package`, `npm_package`, `npm_script`, `file_exists` — read it and confirm), so an added extension cannot activate anything anywhere. Put the reasoning and the output in your report.

- [ ] **Step 5: Clean up and run the gates**

```bash
docker image rm -f ddev-sync-web-probe ddev-sync-web-probe82
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

- [ ] **Step 6: Commit**

```bash
cat > /tmp/ddev-sync-commit-2.txt <<'MSG'
test(matrix): build pcntl in the web image, for Horizon

laravel/horizon lists ext-pcntl and ext-posix in `require`, not in
`suggest`, so composer refuses the package without them. Measured on this
image: posix is already there, pcntl is not, so `composer require
laravel/horizon` fails at the platform check before anything runs.

Inert for every other cell: no existing recipe names horizon or pcntl, no
feature or profile depends on a PHP extension, and feature detection has
no predicate that can look at one - its four are composer_package,
npm_package, npm_script and file_exists. An added extension therefore
cannot activate anything anywhere.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/web/Dockerfile
git commit -F /tmp/ddev-sync-commit-2.txt
```

---

### Task 3: Services only where a cell asks for them

**Files:**
- Modify: `tests/matrix/docker-compose.yaml`
- Modify: `tests/matrix/stack.py`
- Modify: `tests/matrix/cells.py` (the `Cell.services` field only)
- Modify: `tests/matrix/test_feature_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Cell.services: tuple[str, ...] = ()`; compose profiles named `redis` and `elasticsearch`, each matching its service name; `Stack.up` passing `--profile <name>` per entry. Tasks 4–6 rely on all three.

- [ ] **Step 1: Add the field**

In `tests/matrix/cells.py`, add to `Cell` after `features`:

```python
    services: tuple[str, ...] = ()
```

and this paragraph to `Cell`'s docstring, after the `features` paragraph:

```
    `services` names the compose profiles this cell needs beyond the four
    services every cell starts. Each new service sits behind a `profiles:`
    entry of its own name, so one string selects the profile and names the
    container to exec into. Defaulted, so the cells that came before this
    field start exactly what they started then - which matters, because
    Elasticsearch is 512 MB of heap that six of them have no use for.
```

- [ ] **Step 2: Write the failing guard (Review Focus 1)**

Append to `tests/matrix/test_feature_registry.py`:

```python
def test_every_declared_service_is_a_compose_profile() -> None:
    """A mistyped service name must cost a collection error, not a cell run.

    `compose --profile nosuch up` starts the four default services and says
    nothing about the name it did not recognise, so the cell would come up
    without the service it asked for and fail somewhere downstream.
    """
    compose = (HERE / "docker-compose.yaml").read_text(encoding="utf-8")
    declared = {name for cell in CELLS for name in cell.services}
    for name in sorted(declared):
        assert f"- {name}\n" in compose, (
            f"cell service {name!r} is not a compose profile in docker-compose.yaml; "
            "each service's `profiles:` entry carries the service's own name"
        )
```

`HERE` comes from `tests.matrix.paths`; add it to that module's import if it is not already there.

- [ ] **Step 3: Run it to verify it passes vacuously, then prove it load-bearing**

Run: `uv run pytest tests/matrix/test_feature_registry.py -v --no-cov`
Expected: PASS — no cell declares a service yet, so the loop body does not run.

Now mutate: add `services=("nosuch",)` to the `mxdrupal` entry in `cells.py`, re-run, and confirm it FAILS naming `'nosuch'`. Revert that one argument by hand — `git checkout` would also discard Step 1. Record both outputs.

- [ ] **Step 4: Add the two services**

In `tests/matrix/docker-compose.yaml`, after the `remote` service, add:

```yaml
  # Behind a profile, like `elasticsearch` below: six of the cells in this
  # matrix have no use for either, and a profile is how compose is told not to
  # start a service by default. `Cell.services` names the profile, and
  # `stack.py::up` turns each name into a `--profile` argument.
  redis:
    image: redis:7-alpine
    profiles:
      - redis
    networks:
      default:
        aliases:
          - ddev-${MATRIX_CELL}-redis
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 5s
      retries: 30

  # `discovery.type=single-node` and security off, because this is a throwaway
  # test instance with no cluster to join and no credential worth having; the
  # heap is pinned low for the same reason. Measured 2026-10-10 with
  # elasticsearch:9.0.0: this configuration answered on its HTTP port 11
  # seconds after `docker run` and reported cluster status green.
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:9.0.0
    profiles:
      - elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    networks:
      default:
        aliases:
          - ddev-${MATRIX_CELL}-es
    healthcheck:
      test: ["CMD-SHELL", "curl -sf localhost:9200/_cluster/health || exit 1"]
      interval: 2s
      timeout: 5s
      retries: 60
```

- [ ] **Step 5: Teach `up` to pass the profiles**

In `tests/matrix/stack.py`, change `up`:

```python
def up(cell: Cell, image: str) -> Stack:
    """Start `cell`'s stack and wait for every service to report healthy."""
    stack = Stack(cell=cell, image=image)
    stack.down()
    # `--profile` goes before the subcommand, which is where compose takes it.
    # `down` needs them too, or a profiled service outlives the stack it
    # belongs to and the next run finds its port taken.
    profiles = [argument for name in cell.services for argument in ("--profile", name)]
    result = stack.compose(*profiles, "up", "-d", "--build", "--wait")
    if result.returncode != 0:
        stack.down()
        raise RuntimeError(f"{cell.name}: the stack did not come up:\n{result.stderr}")
    return stack
```

and `down`, so teardown reaches the profiled services too:

```python
    def down(self) -> None:
        """Stop and remove every container, the network and the volumes."""
        profiles = [argument for name in self.cell.services for argument in ("--profile", name)]
        self.compose(*profiles, "down", "-v", "--remove-orphans")
```

- [ ] **Step 6: Verify the four-service cells are unchanged (Review Focus 4)**

Without Docker, `compose config` resolves the file and lists what would start:

```bash
cd tests/matrix
for cell in mxlaravel mxfeatlaravel; do
  echo "--- $cell"
  MATRIX_CELL=$cell MATRIX_PHP_IMAGE=php:8.3-cli MATRIX_PROJECT_DIR=/tmp/x \
  MATRIX_KEYS_DIR=/tmp/x MATRIX_REMOTE_DIR=/tmp/x MATRIX_REMOTE_PACKAGES=x \
  MATRIX_UID=501 MATRIX_GID=20 docker compose -f docker-compose.yaml config --services | sort | tr '\n' ' '
  echo
done
```

Expected for both: `local-db remote remote-db web` and nothing else — `config --services` omits profiled services that no `--profile` selected. Then confirm the profiled ones appear when asked:

```bash
MATRIX_CELL=mxfeathorizon MATRIX_PHP_IMAGE=php:8.3-cli MATRIX_PROJECT_DIR=/tmp/x \
MATRIX_KEYS_DIR=/tmp/x MATRIX_REMOTE_DIR=/tmp/x MATRIX_REMOTE_PACKAGES=x \
MATRIX_UID=501 MATRIX_GID=20 docker compose -f docker-compose.yaml --profile redis config --services | sort | tr '\n' ' '
```

Expected: `local-db redis remote remote-db web`.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

```bash
cat > /tmp/ddev-sync-commit-3.txt <<'MSG'
test(matrix): let a cell name the services it needs

Redis and Elasticsearch join the stack behind compose profiles, and
`Cell.services` names which a cell wants. Each service's profile carries
the service's own name, so one string both selects the profile and names
the container to exec into.

Defaulted to (), so the eight cells that predate the field start exactly
the four services they started before - checked with `compose config
--services`, which lists only those for them. That is the point: six of
them have no use for either service, and Elasticsearch is 512 MB of heap
and a 700 MB image to start for nothing.

`down` passes the profiles too. Without that a profiled service outlives
the stack it belongs to, and the next run of the same cell finds its port
taken.

The guard beside it is unmarked, so it runs in the ordinary suite: a
mistyped service name would otherwise start the four defaults silently -
compose says nothing about a profile it does not recognise - and fail
somewhere downstream in a cell run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/docker-compose.yaml tests/matrix/stack.py tests/matrix/cells.py tests/matrix/test_feature_registry.py
git commit -F /tmp/ddev-sync-commit-3.txt
```

---

### Task 4: The two markers and the two readers

**Files:**
- Modify: `tests/matrix/cells.py`
- Modify: `tests/matrix/stack.py`
- Modify: `tests/matrix/test_matrix.py`

**Interfaces:**
- Consumes: `Cell.services` (Task 3).
- Produces: `RedisMarker(key, push, expected_before=1)`, `IndexMarker(index, expected_before=0, minimum_after=1)`, `FeatureCheck.redis_marker`, `FeatureCheck.index_marker`, `Stack.redis(*args) -> str`, `Stack.es_count(index) -> int`. Tasks 5 and 6 declare the markers.

- [ ] **Step 1: Add the two dataclasses**

In `tests/matrix/cells.py`, after `FlushMarker` and before `FeatureCheck`:

```python
@dataclass(frozen=True)
class RedisMarker:
    """A queue entry a cell plants so a feature's Redis command can be seen working.

    `key` is the Redis key the entry lands under, prefix included: Laravel
    builds `queues:<name>` in `Illuminate\\Queue\\RedisQueue`, but
    `laravel/laravel`'s `config/database.php` prefixes every Redis key with
    `Str::slug(APP_NAME) . '-database-'`, so the full key is not the one the
    queue name suggests. `push` is a command run in the web container that
    plants exactly one entry, and `expected_before` is how many the test must
    see before the sync - a marker that was never planted would otherwise let
    "the queue is empty afterwards" pass without the feature doing anything.
    """

    key: str
    push: str
    expected_before: int = 1


@dataclass(frozen=True)
class IndexMarker:
    """What a cell expects of a search index before and after the sync.

    `expected_before` is 0 because an index the sync is about to build is
    either absent or empty beforehand - the mirror of `RedisMarker`, whose
    planted entry must be present. `minimum_after` is a floor rather than an
    exact count: the document count follows from the cell's seed, and pinning
    it here would break the marker whenever the seed grows.
    """

    index: str
    expected_before: int = 0
    minimum_after: int = 1
```

Add to `FeatureCheck`:

```python
    redis_marker: RedisMarker | None = None
    index_marker: IndexMarker | None = None
```

and one sentence to its docstring: that these two are `row_marker`'s equivalents for a feature whose effect lands in Redis or in a search index rather than in SQL.

- [ ] **Step 2: Add the readers**

In `tests/matrix/stack.py`, beside `sql`:

```python
    def redis(self, *args: str) -> str:
        """Run one redis-cli command against this cell's Redis.

        Raises on a failed call for the same reason `sql` does: a test reads an
        empty result as "no entries", and a call that errored must not pass
        for that.
        """
        result = self.compose("exec", "-T", "redis", "redis-cli", *args)
        if result.returncode != 0:
            raise RuntimeError(f"redis-cli {' '.join(args)} failed:\n{result.stderr}")
        return result.stdout.strip()

    def es_count(self, index: str) -> int:
        """How many documents `index` holds, with an absent index counting 0.

        Elasticsearch answers 404 for an index that does not exist, which is
        the ordinary state before the sync builds one - so absence is a count,
        not an error. Any other failure raises, as in `sql`: a count that
        silently read 0 from a broken call would make a populated index
        indistinguishable from a dead service.
        """
        result = self.compose(
            "exec",
            "-T",
            "elasticsearch",
            "curl",
            "-s",
            "-o",
            "/dev/stderr",
            "-w",
            "%{http_code}",
            f"localhost:9200/{index}/_count",
        )
        if result.returncode != 0:
            raise RuntimeError(f"counting {index} failed:\n{result.stderr}")
        status = result.stdout.strip()
        if status == "404":
            return 0
        if status != "200":
            raise RuntimeError(f"counting {index} returned HTTP {status}:\n{result.stderr}")
        return int(json.loads(result.stderr)["count"])

    def es_delete(self, index: str) -> None:
        """Remove `index` if it is there, so a later count is this run's work.

        A 404 counts as success: what matters is that the index is absent
        afterwards, not that it existed before. Any other failure raises, as
        in `sql`.
        """
        result = self.compose(
            "exec",
            "-T",
            "elasticsearch",
            "curl",
            "-s",
            "-o",
            "/dev/stderr",
            "-w",
            "%{http_code}",
            "-X",
            "DELETE",
            f"localhost:9200/{index}",
        )
        if result.returncode != 0:
            raise RuntimeError(f"deleting {index} failed:\n{result.stderr}")
        status = result.stdout.strip()
        if status not in {"200", "404"}:
            raise RuntimeError(f"deleting {index} returned HTTP {status}:\n{result.stderr}")
```

Add `import json` to the module's imports.

- [ ] **Step 3: Write the two test bodies**

In `tests/matrix/test_matrix.py`, after `test_the_feature_rule_removed_the_planted_row`:

```python
def test_the_feature_command_emptied_the_planted_queue(stack: Stack, cell: Cell) -> None:
    # The claim: a feature whose effect lands in Redis really clears it. Same
    # device as the planted cache row, in a different store: the entry is
    # planted after the import, so nothing the database step does can be what
    # removes it, and only the `setup` step's own command can.
    markers = [check.redis_marker for check in cell.features if check.redis_marker is not None]
    if not markers:
        pytest.skip(f"{cell.name} asserts no feature Redis command")
    for marker in markers:
        planted = stack.web(["sh", "-c", marker.push])
        assert planted.returncode == 0, f"stdout:\n{planted.stdout}\n\nstderr:\n{planted.stderr}"
        assert int(stack.redis("LLEN", marker.key)) == marker.expected_before, (
            f"{marker.key} does not hold {marker.expected_before} entries before the sync: "
            "the push did not land where the marker says it does"
        )
        result = stack.engine("--target", "production", "--steps", "setup", "--yes")
        assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        assert int(stack.redis("LLEN", marker.key)) == 0, (
            f"{marker.key} still holds entries: the feature's command did not take effect"
        )


def test_the_feature_command_populated_the_index(stack: Stack, cell: Cell) -> None:
    # The claim: a feature that rebuilds a search index really fills it.
    #
    # The index is deleted first, the way the Drupal role test takes its role
    # away before the run it means to attribute. By file order the whole sync
    # has already run once, and that sync's own setup step already populated
    # this index - so without the delete, "the index has documents" would be
    # the earlier run's work and the before-count could never be 0. Measured:
    # `fos:elastica:populate` resets and recreates the index itself, so
    # removing it costs the run nothing.
    markers = [check.index_marker for check in cell.features if check.index_marker is not None]
    if not markers:
        pytest.skip(f"{cell.name} asserts no feature search index")
    for marker in markers:
        stack.es_delete(marker.index)
        assert stack.es_count(marker.index) == marker.expected_before, (
            f"{marker.index} does not hold {marker.expected_before} documents after being "
            "deleted: something other than the feature is writing it"
        )
        result = stack.engine("--target", "production", "--steps", "setup", "--yes")
        assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        counted = stack.es_count(marker.index)
        assert counted >= marker.minimum_after, (
            f"{marker.index} holds {counted} documents, fewer than the {marker.minimum_after} "
            "the cell expects: the feature's command did not populate it"
        )
```

Note both run `--steps setup` only, not a whole sync: the effect under test is a setup command's, and a narrower run makes the attribution tighter.

- [ ] **Step 4: Verify collection and that both skip**

```bash
uv run pytest -m matrix -k "queue or index" --no-cov --collect-only -q | tail -3
uv run pytest -m matrix -k "nothingmatchesthis" --no-cov -q; echo "exit=$?"
```

Expected: the two new names collected for every cell; then exit 5, proving nothing started a stack.

- [ ] **Step 5: Prove `es_count`'s 404 branch without Docker (Review Focus 3)**

The 404-reads-as-zero branch is the one a cell run cannot exercise — by the time a cell runs, the index either exists or the test has already failed. Pin it in an unmarked test. Create `tests/matrix/test_stack_readers.py`:

```python
"""The two service readers' failure and absence handling, checked without a stack.

`Stack.es_count` has to tell three cases apart that all look like "no
documents" from a distance: an index that does not exist yet (0), a service
that answered something else (an error), and a call that failed outright (an
error). Only the first may count as zero, and a cell run cannot reach the
other two without being broken in some other way first.
"""

import subprocess
from dataclasses import dataclass

import pytest

from tests.matrix.cells import CELLS
from tests.matrix.stack import Stack


@dataclass
class _Reply:
    """One canned `compose exec` result."""

    returncode: int
    stdout: str
    stderr: str


def _stack(monkeypatch: pytest.MonkeyPatch, reply: _Reply) -> Stack:
    stack = Stack(cell=CELLS[0], image="unused")
    monkeypatch.setattr(
        Stack,
        "compose",
        lambda self, *args, stdin=None: subprocess.CompletedProcess(
            args=list(args), returncode=reply.returncode, stdout=reply.stdout, stderr=reply.stderr
        ),
    )
    return stack


def test_an_absent_index_counts_as_zero_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, "404", '{"error":"index_not_found_exception"}'))
    assert stack.es_count("app") == 0


def test_a_populated_index_returns_its_count(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, "200", '{"count":7}'))
    assert stack.es_count("app") == 7


def test_an_unexpected_status_raises_rather_than_counting_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(monkeypatch, _Reply(0, "503", "service unavailable"))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        stack.es_count("app")


def test_a_failed_call_raises_rather_than_counting_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(monkeypatch, _Reply(1, "", "no such service: elasticsearch"))
    with pytest.raises(RuntimeError, match="counting app failed"):
        stack.es_count("app")


def test_a_failed_redis_call_raises_rather_than_returning_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(monkeypatch, _Reply(1, "", "no such service: redis"))
    with pytest.raises(RuntimeError, match="redis-cli LLEN k failed"):
        stack.redis("LLEN", "k")


def test_deleting_an_absent_index_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # The delete exists to guarantee absence, so finding the index already
    # absent is the outcome it wanted, not a failure.
    stack = _stack(monkeypatch, _Reply(0, "404", '{"error":"index_not_found_exception"}'))
    stack.es_delete("app")


def test_a_refused_delete_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, "403", "forbidden"))
    with pytest.raises(RuntimeError, match="deleting app returned HTTP 403"):
        stack.es_delete("app")
```

This file is **unmarked**, so it runs in the ordinary suite — `pytestmark = pytest.mark.matrix` is in `test_matrix.py` alone, and importing `tests.matrix.stack` only defines things.

- [ ] **Step 6: Run the new tests, then prove each load-bearing**

Run: `uv run pytest tests/matrix/test_stack_readers.py -v --no-cov`
Expected: 5 passed.

Then mutate `es_count` and `redis` one change at a time — drop the 404 branch; return `0` instead of raising on a bad status; drop the `returncode` check in each reader — and confirm each mutation fails at least one of the five. A reader whose failure path no mutation can fail is not guarded. Record each mutation and its failure, and revert.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

```bash
cat > /tmp/ddev-sync-commit-4.txt <<'MSG'
test(matrix): assert a feature's effect in Redis and in a search index

FeatureCheck could only observe SQL: row_marker is a FlushMarker, read
through Stack.sql. Two of the features this matrix has never verified act
elsewhere - horizon:clear empties a Redis queue, fos:elastica:populate
fills a search index - so neither was observable at all.

Two concrete marker fields rather than one abstraction, matching how
flush_marker and row_marker already work: a named field per kind of
evidence. Two instances are too few to see the right generalisation.

Both readers raise on a failed call, for the reason Stack.sql does: a test
reads an empty result as "no entries", and a call that errored must not
pass for that. es_count has to tell three look-alike cases apart, and only
one of them may count as zero - an index that does not exist yet, which is
the ordinary state before the sync builds one. The unmarked tests beside
them pin that, because a cell run cannot reach the other two without being
broken some other way first.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/cells.py tests/matrix/stack.py tests/matrix/test_matrix.py tests/matrix/test_stack_readers.py
git commit -F /tmp/ddev-sync-commit-4.txt
```

---

### Task 5: `mxfeathorizon` — Horizon against a real Redis

**Files:**
- Create: `tests/matrix/cells/mxfeathorizon/install.sh`
- Create: `tests/matrix/cells/mxfeathorizon/ddev-sync.yaml`
- Create: `tests/matrix/cells/mxfeathorizon/seed.sql`
- Create: `tests/matrix/cells/mxfeathorizon/keep-data.txt`
- Create: `tests/matrix/cells/mxfeathorizon/remote/storage/app/public/matrix-upload.txt`
- Modify: `tests/matrix/cells.py` (one `Cell` entry)

**Interfaces:**
- Consumes: `Cell.services`, the `redis` compose profile (Task 3); `RedisMarker`, `Stack.redis`, `test_the_feature_command_emptied_the_planted_queue` (Task 4); `pcntl` in the image (Task 2).
- Produces: the cell name `mxfeathorizon`, which Task 7 emits a row for.

**Three measured facts the recipe turns on.** Each was established before this plan and each would break the cell silently if ignored:

- **`APP_ENV` must be `local`.** The existing Laravel cells write a hand-made `.env` with no `APP_ENV`; Laravel defaults it to `production`; Horizon's `ClearCommand` uses `ConfirmableTrait`, which refuses in `production` without `--force`; the engine runs non-interactively, so the command would return 1, clear nothing, and be reported fine by `allow_failure: true`. The real `laravel/laravel` skeleton ships `APP_ENV=local`, so setting it models a real project rather than working around the feature.
- **`QUEUE_CONNECTION` must be `redis`.** The skeleton ships `database`. Without this the queue a job lands in is not the queue Horizon clears.
- **`REDIS_CLIENT` must be `predis`** and `predis/predis` must be installed. The skeleton ships `phpredis`, and the matrix web image has no `redis` extension.

- [ ] **Step 1: Write the install recipe**

Create `tests/matrix/cells/mxfeathorizon/install.sh`:

```sh
# A real Laravel with Horizon and a real Redis behind it. This cell exists for
# one feature, `horizon`, whose command acts on Redis rather than on the
# database - the first thing in this matrix that does.
composer create-project --no-interaction --prefer-dist laravel/laravel .

# `APP_ENV=local` is load-bearing, not decoration. Laravel defaults APP_ENV to
# `production`, and `horizon:clear` runs through ConfirmableTrait, which
# refuses in production without `--force`; the engine execs non-interactively,
# so the command would return 1 and clear nothing - and the feature is
# `allow_failure: true`, so the sync would call that fine. The real
# laravel/laravel skeleton ships APP_ENV=local, so this is what a project
# looks like, not a workaround.
#
# `QUEUE_CONNECTION=redis` because the skeleton ships `database`, and a job on
# the database queue is not in the queue Horizon clears. `REDIS_CLIENT=predis`
# because the skeleton ships `phpredis` and this image has no redis extension.
cat > .env <<'ENV'
APP_ENV=local
APP_KEY=base64:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_URL=https://mxfeathorizon.ddev.site
DB_CONNECTION=mysql
DB_HOST=ddev-mxfeathorizon-db
DB_PORT=3306
DB_DATABASE=db
DB_USERNAME=db
DB_PASSWORD=db
QUEUE_CONNECTION=redis
REDIS_CLIENT=predis
REDIS_HOST=ddev-mxfeathorizon-redis
REDIS_PORT=6379
ENV

php artisan migrate --force --no-interaction

# The same deliberately unapplied migration the other Laravel cells leave: this
# cell runs the laravel profile too, and the migrate assertion is keyed on the
# profile rather than on the cell name.
php artisan make:migration create_matrix_marker_table --create=matrix_marker --no-interaction

# predis rather than the phpredis extension, which this image does not build.
# Horizon lists ext-pcntl and ext-posix in `require`, so without the pcntl this
# image now builds, this line fails at composer's platform check.
composer require --no-interaction laravel/horizon predis/predis

composer show laravel/framework | grep -E '^(name|versions) '
composer show laravel/horizon | grep -E '^(name|versions) '
composer show predis/predis | grep -E '^(name|versions) '
php artisan --version
php -r 'var_dump(extension_loaded("pcntl"));'
php -v | head -1
composer --version
```

Note what the recipe does **not** do: it does not push the job. The marker's `push` runs at assertion time, after the import, which is what makes the queue's emptiness attributable to the feature rather than to anything the recipe did.

- [ ] **Step 2: Write the three remaining recipe files**

`ddev-sync.yaml` — `mxfeatlaravel`'s, with the host names changed and no `features:` key, because `horizon` is detected from `composer.json`:

```yaml
# `remote_credentials: explicit` keeps the cell from depending on a .env on
# the remote: this cell is about one feature's command, not about credential
# discovery, which the unit suite already covers exhaustively.
#
# No `features:` key: a key switches detection off for the whole project
# (features/resolver.py), and `horizon` is meant to be found by detection,
# from the composer package the recipe installs.
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

`seed.sql` — the SQL body of `mxfeatlaravel/seed.sql`, copied verbatim, with the leading comment replaced by one saying this cell declares the same profile-shaped registry fields and so the same test bodies read it, and that the feature evidence is what this cell adds.

`keep-data.txt`:

```
migrations
```

`remote/storage/app/public/matrix-upload.txt`:

```
a file production serves from storage/app/public
```

- [ ] **Step 3: Register the cell**

In `tests/matrix/cells.py`, after the `mxfeatlaravel` entry:

```python
    Cell(
        name="mxfeathorizon",
        # A third Laravel cell rather than Horizon added to `mxfeatlaravel`:
        # that cell's four rows state the environment they were measured
        # against, and adding Horizon and Redis makes that statement stale
        # until it re-runs and all four are rewritten. A cell of its own also
        # keeps Redis out of their runs, which is what `services` is for.
        profile="laravel",
        project_type="laravel",
        docroot="public",
        php_image="php:8.3-cli",
        services=("redis",),
        # `mxlaravel`'s profile-shaped values, verbatim: `Cell`'s docstring
        # defines None/() as "the profile has no such rule", so emptying them
        # would make that meaning false and print a lying skip reason.
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
        features=(
            FeatureCheck(
                feature="horizon",
                redis_marker=RedisMarker(
                    # The prefix is Laravel's, not this cell's invention:
                    # config/database.php sets the Redis prefix to
                    # `Str::slug(APP_NAME) . '-database-'` and no .env here
                    # sets APP_NAME, so the default `laravel` is slugged.
                    # Illuminate\Queue\RedisQueue then builds `queues:default`
                    # under it. A marker naming the unprefixed key would find
                    # nothing at plant time.
                    key="laravel-database-queues:default",
                    # `queue:push` is not a thing; a closure pushed through
                    # tinker is. The job body does not matter - only that one
                    # entry lands in the queue Horizon is about to clear.
                    push=(
                        "php artisan tinker --execute="
                        "'dispatch(function () { logger(\"matrix marker\"); });'"
                    ),
                ),
            ),
        ),
    ),
```

- [ ] **Step 4: Run the cell**

```bash
DDEV_SYNC_MATRIX_KEEP=1 uv run pytest -m matrix -k mxfeathorizon --no-cov -vv -rA -s
```

Expected: every test passed or skipped, nothing failed. Keep the output — the `[mxfeathorizon] installed:` block names the versions Task 7 records, and the per-test lines are its evidence.

**If the planted entry is not where the marker says**, the assertion fails at plant time with the key named. Do not change the asserted key to whatever `KEYS *` reveals without working out *why* — `redis-cli KEYS '*'` inside the stack will show the real key, and the gap between it and the measured one is a finding worth reporting (a different `APP_NAME` default, a prefix change in a newer Laravel).

**If `horizon:clear` runs but clears nothing**, check `APP_ENV` in the installed `.env` first. That is the failure this recipe's first comment exists to prevent, and `allow_failure` makes it quiet.

Spec decision rule: a feature with no observable effect is a finding, not something to work around. Do not soften the assertion or add a recipe step that makes it pass.

- [ ] **Step 5: Run the gates and commit**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

```bash
cat > /tmp/ddev-sync-commit-5.txt <<'MSG'
test(matrix): verify horizon against a real Redis

`horizon:clear` had no run-time evidence, and could not have had any: the
matrix could only observe SQL. This cell gives it some - a real Laravel
with Horizon and predis, a Redis service started only for cells that ask
for it, and a job planted in the queue after the import so that its
absence afterwards is the feature's own work.

Three things in the .env are load-bearing and measured. APP_ENV=local,
because Laravel defaults it to production and horizon:clear refuses there
without --force - non-interactively that means exit 1, nothing cleared,
and allow_failure reporting it fine. QUEUE_CONNECTION=redis, because the
skeleton ships `database` and a job there is not in the queue Horizon
clears. REDIS_CLIENT=predis, because this image has no redis extension.

The marker names the full prefixed key. Laravel prefixes Redis keys with
Str::slug(APP_NAME) . '-database-', so the queue a job lands in is not the
one its name suggests, and a marker on the bare key would find nothing.

A third Laravel cell rather than additions to mxfeatlaravel, whose four
rows state the environment they were measured against.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/cells.py tests/matrix/cells/mxfeathorizon
git commit -F /tmp/ddev-sync-commit-5.txt
```

---

### Task 6: `mxfeatsymfony` — Elastica against a real Elasticsearch

**Files:**
- Create: `tests/matrix/cells/mxfeatsymfony/install.sh`
- Create: `tests/matrix/cells/mxfeatsymfony/ddev-sync.yaml`
- Create: `tests/matrix/cells/mxfeatsymfony/seed.sql`
- Create: `tests/matrix/cells/mxfeatsymfony/app/fos_elastica.yaml`
- Create: `tests/matrix/cells/mxfeatsymfony/app/Doc.php`
- Create: `tests/matrix/cells/mxfeatsymfony/remote/public/uploads/matrix-upload.txt`
- Modify: `tests/matrix/cells.py` (one `Cell` entry)

**Interfaces:**
- Consumes: `Cell.services`, the `elasticsearch` compose profile (Task 3); `IndexMarker`, `Stack.es_count`, `Stack.es_delete`, `test_the_feature_command_populated_the_index` (Task 4).
- Produces: the cell name `mxfeatsymfony`, which Task 7 emits a row for.

**Measured 2026-10-12**, in the matrix web image against `elasticsearch:9.0.0`
and `mariadb:11`, by standing the bundle up and iterating until
`fos:elastica:populate` exited 0. Three runs were needed; the first two failed,
and both failures are the reason this task carries code rather than
instructions:

- **Resolved:** `friendsofsymfony/elastica-bundle` **v7.2.0**, `ruflin/elastica`
  **9.0.0**, `doctrine/orm` **3.7.5**, `symfony/framework-bundle` **v7.4.20**.
- **The bundle does not register itself.** After `composer require`,
  `config/bundles.php` held only `FrameworkBundle`, `DoctrineBundle` and
  `DoctrineMigrationsBundle`. Without the entry, Symfony does not know the
  `fos_elastica` config namespace and **every** console call dies on the
  configuration file — `cache:clear`, `doctrine:schema:create`, `populate`.
  The recipe must add it.
- **The client key is `hosts`, a list — not `url`.** `url` is rejected, and the
  error names the whole schema: `api_key`, `client_config`, `client_options`,
  `cloud_id`, `connection_strategy`, `headers`, `hosts`, `http_client`,
  `http_error_codes`, `logger`, `password`, `retries`, `retry_on_conflict`,
  `timeout`, `username`.
- **The index is named exactly as configured: `app`.** `_cat/indices` reports
  `app` and `_cat/aliases` is **empty** — no date suffix and no alias, because
  that is `use_alias`'s behaviour and it is off by default. An `IndexMarker`
  can name the configured string directly.
- **`populate` resets and recreates the index**, printing `Resetting app`,
  `Populating app 3/3`, `Refreshing app`. Before it ran, `_cat/indices` was
  empty — the index does not exist at all. That is why `es_count` reads a 404
  as 0: the pre-sync state is an absent index, not an empty one, so the 404
  branch is on this task's main path rather than defensive.
- **Three seeded rows yielded three documents**, in both `app/_count` and the
  bare index.

- [ ] **Step 1: Write the two app files the bundle needs**

Create `tests/matrix/cells/mxfeatsymfony/app/fos_elastica.yaml`:

```yaml
# The minimal configuration under which `fos:elastica:populate` exits 0,
# established by iterating in the matrix web image against elasticsearch:9.0.0
# (bundle v7.2.0, ruflin/elastica 9.0.0).
#
# `hosts`, a list - not `url`, which v7 rejects outright. The host is the
# compose alias this cell's stack gives the service.
#
# `persistence` is what makes the command able to do anything: without a
# driver and a model it has no source to read documents from.
fos_elastica:
    clients:
        default: { hosts: ['http://ddev-mxfeatsymfony-es:9200'] }
    indexes:
        app:
            properties:
                title:
                    type: text
            persistence:
                driver: orm
                model: App\Entity\Doc
```

Create `tests/matrix/cells/mxfeatsymfony/app/Doc.php`:

```php
<?php

// The indexed entity. Deliberately the smallest thing with a mapped id and
// one text field: this cell proves that `fos:elastica:populate` reads a
// Doctrine source and writes documents, not that Elastica maps a rich schema.

namespace App\Entity;

use Doctrine\ORM\Mapping as ORM;

#[ORM\Entity]
#[ORM\Table(name: 'doc')]
class Doc
{
    #[ORM\Id]
    #[ORM\Column]
    public int $id = 0;

    #[ORM\Column(length: 255)]
    public string $title = '';
}
```

They are recipe files rather than heredocs inside `install.sh`, as the spec
rules: a YAML config and a PHP class with attributes and backslashes are
reviewable as files and unreadable as shell quoting. They live under `app/` so
nothing confuses them with `remote/`, which `conftest.py` copies onto the
stand-in host.

- [ ] **Step 2: Write the install recipe**

Create `tests/matrix/cells/mxfeatsymfony/install.sh`:

```sh
# A real Symfony with Doctrine and FOSElasticaBundle against a real
# Elasticsearch. Built like `mxsymfony` - same skeleton, same explicit
# `app_user` migration, same deliberately unapplied second migration - because
# this cell runs the symfony profile and the profile-keyed assertions run for
# it too. What it adds is the one feature, `symfony-search`.
composer create-project --no-interaction --prefer-dist symfony/skeleton .
composer require --no-interaction symfony/orm-pack

# Exported rather than written to `.env.local`, exactly as `mxsymfony` does:
# the stock `.env` stays untouched so the profile's env step has to patch that
# very file. Symfony's Dotenv never overrides a variable already in the
# process environment.
export DATABASE_URL='mysql://db:db@ddev-mxfeatsymfony-db:3306/db?serverVersion=mariadb-11.0.0'

mkdir -p migrations
cat > migrations/Version20260101000000.php <<'PHP'
<?php

declare(strict_types=1);

namespace DoctrineMigrations;

use Doctrine\DBAL\Schema\Schema;
use Doctrine\Migrations\AbstractMigration;

final class Version20260101000000 extends AbstractMigration
{
    public function up(Schema $schema): void
    {
        $this->addSql('CREATE TABLE app_user (
            id INT AUTO_INCREMENT NOT NULL,
            email VARCHAR(180) NOT NULL,
            roles JSON NOT NULL,
            password VARCHAR(255) NOT NULL,
            PRIMARY KEY(id)
        ) DEFAULT CHARACTER SET utf8mb4 ENGINE = InnoDB');
        $this->addSql('CREATE TABLE doc (
            id INT NOT NULL,
            title VARCHAR(255) NOT NULL,
            PRIMARY KEY(id)
        ) DEFAULT CHARACTER SET utf8mb4 ENGINE = InnoDB');
    }

    public function down(Schema $schema): void
    {
        $this->addSql('DROP TABLE app_user');
        $this->addSql('DROP TABLE doc');
    }
}
PHP

php bin/console doctrine:migrations:migrate --no-interaction

# The same deliberately unapplied migration `mxsymfony` leaves, written after
# the migrate above so the dump that seeds the remote lacks its table.
cat > migrations/Version20260102000000.php <<'PHP'
<?php

declare(strict_types=1);

namespace DoctrineMigrations;

use Doctrine\DBAL\Schema\Schema;
use Doctrine\Migrations\AbstractMigration;

final class Version20260102000000 extends AbstractMigration
{
    public function up(Schema $schema): void
    {
        $this->addSql('CREATE TABLE matrix_marker (id INT NOT NULL, PRIMARY KEY(id))');
    }

    public function down(Schema $schema): void
    {
        $this->addSql('DROP TABLE matrix_marker');
    }
}
PHP

composer require --no-interaction friendsofsymfony/elastica-bundle

# The bundle does not register itself: measured, `composer require` leaves
# config/bundles.php holding only FrameworkBundle, DoctrineBundle and
# DoctrineMigrationsBundle. Without this entry Symfony does not know the
# `fos_elastica` namespace, and every console call - cache:clear,
# doctrine:migrations:migrate, populate - dies on the config file instead.
php -r '
$f = "config/bundles.php";
$s = file_get_contents($f);
$add = "    FOS\\ElasticaBundle\\FOSElasticaBundle::class => [\"all\" => true],\n";
file_put_contents($f, preg_replace("/\n\];\s*$/", "\n" . $add . "];\n", $s));
'
grep -q FOSElasticaBundle config/bundles.php || {
  echo "install.sh failed to register FOSElasticaBundle; every console call would die on config" >&2
  exit 1
}

# The configuration and the indexed entity, from the cell's own recipe files.
# `conftest.py::_install` stages them at /tmp/matrix-app inside the container
# rather than in the project, because `composer create-project .` above
# refuses a project directory that holds anything at all - see the comment on
# the staging itself.
mkdir -p src/Entity
cp /tmp/matrix-app/fos_elastica.yaml config/packages/fos_elastica.yaml
cp /tmp/matrix-app/Doc.php src/Entity/Doc.php

# Proof the configuration loads at all, before anything depends on it: a bad
# `fos_elastica` block fails here with the option named, rather than three
# steps later with the same message attached to an unrelated command.
php bin/console cache:clear --no-interaction

# Rows for populate to index. They go in the LOCAL database only so the dump
# that seeds the remote carries the `doc` schema; the rows the sync will index
# come from seed.sql on the remote side.
php bin/console dbal:run-sql "INSERT INTO doc (id, title) VALUES (1,'alpha-local')"

# The index must not exist before the sync: `IndexMarker.expected_before` is 0
# and `es_count` reads an absent index as 0. Measured: the bundle creates
# nothing at install time and `populate` resets the index itself, so there is
# normally nothing here to remove - this is a precaution, not a removal of
# anything known to be there.
curl -s -X DELETE "http://ddev-mxfeatsymfony-es:9200/app" >/dev/null || true

composer show symfony/framework-bundle | grep -E '^(name|versions) '
composer show doctrine/orm | grep -E '^(name|versions) '
composer show friendsofsymfony/elastica-bundle | grep -E '^(name|versions) '
composer show ruflin/elastica | grep -E '^(name|versions) '
php bin/console --version
php -v | head -1
composer --version
```

**`conftest.py` copies only `remote/` and `remote-only/`**, so the recipe
cannot read `app/` from the cell directory directly — the two files have to
reach the container another way. Add the copy to `tests/matrix/conftest.py`'s
`_install`, beside the recipe read, so a cell directory's `app/` lands at
`/tmp/matrix-app` **inside the web container** before `install.sh` runs:

```python
    staged = cell_dir(cell.name) / "app"
    if staged.is_dir():
        copied = stack.compose("cp", str(staged), "web:/tmp/matrix-app")
        if copied.returncode != 0:
            raise RuntimeError(
                f"{cell.name}: staging app/ into the web container failed:\n{copied.stderr}"
            )
```

Not into the bind-mounted project, which this task's own measurement corrected:
a recipe whose first line is `composer create-project .` refuses to run if the
project directory holds anything, dot entries included, because
`Composer\Util\Filesystem::isDirEmpty` builds its Finder with
`ignoreVCS(false)->ignoreDotFiles(false)` — so staging a `.matrix-app/` into
the project fails the recipe's very first line with `Project directory
"/var/www/html/." is not empty.`

Put it immediately before `result = stack.web(["sh", "-euc", recipe])`, with a
comment saying why: application files a recipe installs are reviewable as
files, and a heredoc holding a PHP class with attributes and namespaces is
not.

- [ ] **Step 3: Write the remaining recipe files**

`tests/matrix/cells/mxfeatsymfony/ddev-sync.yaml` — `mxsymfony`'s, with the
host names changed to this cell's. Keep its `database.symfony_user_table`
setting: the symfony profile's `${SYMFONY_USER_TABLE}` placeholder resolves
from it, and without it the preflight probe looks for a `user` table this
install does not have. Read `tests/matrix/cells/mxsymfony/ddev-sync.yaml` and
copy it, changing only the two host values and the leading comment.

`tests/matrix/cells/mxfeatsymfony/seed.sql` — `mxsymfony`'s SQL body verbatim,
plus the rows `populate` will index. Replace the leading comment to say this
cell declares the same profile-shaped registry fields, so the same test bodies
read it, and that the `doc` rows are what the feature's evidence rests on:

```sql
INSERT INTO doc (id, title) VALUES
  (11, 'production alpha'),
  (12, 'production beta'),
  (13, 'production gamma');
```

Three rows, which is what `minimum_after` is set against. They are distinct
from the one row the install seeded locally, so a `doc` table that still held
the install's row would be visible as a wrong count rather than passing.

`tests/matrix/cells/mxfeatsymfony/remote/public/uploads/matrix-upload.txt` —
the symfony profile maps `public/uploads/`, and the files step fails with
rsync exit 23 when a remote path is missing:

```
a file production serves from public/uploads
```

- [ ] **Step 4: Register the cell**

In `tests/matrix/cells.py`, after the `mxfeathorizon` entry:

```python
    Cell(
        name="mxfeatsymfony",
        # The first feature cell for symfony; `mxsymfony` stays a stock
        # skeleton plus orm-pack, which is what its row says it is.
        profile="symfony",
        project_type="symfony",
        docroot="public",
        php_image="php:8.3-cli",
        services=("elasticsearch",),
        # `mxsymfony`'s profile-shaped values, verbatim - `Cell`'s docstring
        # defines None/() as "the profile has no such rule".
        required_tables=("app_user",),
        admin_user=None,
        password_column=("app_user", "password"),
        production_url=None,
        identity_column="id",
        seeded_users=("1", "2"),
        features=(
            FeatureCheck(
                feature="symfony-search",
                index_marker=IndexMarker(
                    # The configured name, verbatim. Measured against bundle
                    # v7.2.0: `_cat/indices` reports `app` and `_cat/aliases`
                    # is empty - no date suffix and no alias, because that is
                    # `use_alias`'s behaviour and it defaults to off.
                    index="app",
                    # Three rows in seed.sql, so three documents. A floor
                    # rather than an exact count, so a growing seed does not
                    # break the marker.
                    minimum_after=3,
                ),
            ),
        ),
    ),
```

- [ ] **Step 5: Run the cell**

```bash
DDEV_SYNC_MATRIX_KEEP=1 uv run pytest -m matrix -k mxfeatsymfony --no-cov -vv -rA -s
```

Expected: every test passed or skipped, nothing failed. Keep the output — the
`[mxfeatsymfony] installed:` block names the versions Task 7 records.

If `populate` exits 0 and the index stays empty, report what you observed:
a feature with no observable effect is a finding, not something to work
around, and lowering `minimum_after` to whatever the run produced is the one
response the spec rules out.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

```bash
cat > /tmp/ddev-sync-commit-6.txt <<'MSG'
test(matrix): verify symfony-search against a real Elasticsearch

`fos:elastica:populate` had no run-time evidence and could not have had
any: nothing in this matrix could see a search index. This cell gives it
some - a Symfony with FOSElasticaBundle, an Elasticsearch started only for
cells that ask for it, and an index deleted before the run that must be
populated after it.

Two things had to be measured rather than guessed, and both would have
failed a cell run. The bundle does not register itself: after `composer
require`, config/bundles.php holds only the three bundles the skeleton and
orm-pack put there, and without the entry every console call dies on the
fos_elastica config namespace. And the client key is `hosts`, a list - v7
rejects `url` outright.

The index is named exactly as configured, with no date suffix and no
alias, so the marker names the configured string. `populate` resets and
recreates the index itself, which is why the pre-sync state is an absent
index rather than an empty one - and why es_count has to read a 404 as
zero on the main path, not as a defensive branch.

The configuration and the entity are recipe files rather than heredocs: a
YAML block and a PHP class with attributes and namespaces are reviewable
as files and unreadable as shell quoting. conftest stages a cell's `app/`
directory into the project for that.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add tests/matrix/cells.py tests/matrix/conftest.py tests/matrix/cells/mxfeatsymfony
git commit -F /tmp/ddev-sync-commit-6.txt
```

---

### Task 7: The two rows

**Files:**
- Modify: `profiles/VERIFIED.md` (the `horizon` and `symfony-search` rows only)

**Interfaces:**
- Consumes: the two green runs from Tasks 5 and 6, and their version blocks.
- Produces: nothing.

- [ ] **Step 1: Emit the rows and read what the tool gives you**

```bash
uv run python -m tools.apply_verified_rows --emit mxfeathorizon --image ddev-sync:dev --run "local pytest -m matrix -k mxfeathorizon"
uv run python -m tools.apply_verified_rows --emit mxfeatsymfony --image ddev-sync:dev --run "local pytest -m matrix -k mxfeatsymfony"
```

Expected: one `| horizon |` row and one `| symfony-search |` row, and **no profile row for either cell** — `emit_row` keys by `FeatureCheck.feature` when a cell declares features. If either yields `| laravel |` or `| symfony |`, stop: applying it would overwrite a profile row's hand-written evidence.

- [ ] **Step 2: Apply them**

```bash
{ uv run python -m tools.apply_verified_rows --emit mxfeathorizon --image ddev-sync:dev --run "<run>" ; \
  uv run python -m tools.apply_verified_rows --emit mxfeatsymfony --image ddev-sync:dev --run "<run>" ; } > /tmp/rows.md
uv run python -m tools.apply_verified_rows --apply /tmp/rows.md
git diff profiles/VERIFIED.md
```

Then replace each row's *Verified against* cell by hand with the versions the runs printed — `--emit` yields only `<image> on <php image>`, and the filled rows say more than that. Match the existing rows' house style: bare version numbers (`13.35.0`), not `v`-prefixed; a `v` only on a git tag reference.

- [ ] **Step 3: Write the two Notes cells**

Each row says only what its run showed. Required content, in the file's voice:

**`horizon`** — verified against the resolved `laravel/horizon` and `predis/predis` versions on a `laravel/laravel` skeleton (`mxfeathorizon` cell), with a real Redis. **Proven by effect:** a job planted in `laravel-database-queues:default` after the import is gone after the `setup` step, and nothing else in that step touches Redis. Effect rather than exit status, because the command is `allow_failure`. **Worth recording, because it is quiet:** `horizon:clear` runs through `ConfirmableTrait` and refuses when `APP_ENV` is `production`, returning 1 without clearing — which `allow_failure` reports as fine. This cell sets `APP_ENV=local`, as the real skeleton ships, so the command is reached; a project running `APP_ENV=production` locally would get nothing from this feature and no warning. **Also recorded:** `clear()` spans four Redis keys (the queue plus `:delayed`, `:reserved`, `:notify`), and with no `--queue` the queue is `default` only — every other queue name is untouched. **Not run:** the `phpredis` driver (this cell uses predis, because the image has no extension); a non-default `APP_NAME`, which changes the key prefix; any queue other than `default`.

**`symfony-search`** — verified against the resolved bundle and `ruflin/elastica` versions and the Elasticsearch server the stack ran (`mxfeatsymfony` cell). **Proven by effect:** the index is absent or empty before the sync and holds at least the seeded documents after it. **State the configuration the run needed**, because `fos:elastica:populate` cannot succeed without it and the row is where a reader will look: the index definition, the persistence binding, and the entity. **Not run:** whatever Step 1 established as out of reach — a non-Doctrine persistence provider, multiple indexes, an Elasticsearch version other than the one that ran.

Do not write a sentence either run did not support. A row that overclaims is worse than an empty row, because the empty one is honest.

- [ ] **Step 4: Run the gates and commit**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests tools
uv run pytest
```

```bash
cat > /tmp/ddev-sync-commit-7.txt <<'MSG'
docs(profiles): record what the two service cells verified

horizon and symfony-search were the last two features with no run-time
evidence that could have any - the matrix could only observe SQL until
this branch. Both rows are filled from their own cell's run.

The horizon row carries one finding worth more than the verification: the
command refuses when APP_ENV is production and returns 1 without
clearing, which allow_failure reports as fine. A project running
APP_ENV=production locally gets nothing from this feature and no warning.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git add profiles/VERIFIED.md
git commit -F /tmp/ddev-sync-commit-7.txt
```

---

## Out of scope

- A replacement for `typo3-solr`. Establishing what TYPO3 actually needs after an import is its own question; this plan only records that the current answer was wrong.
- Any change to the six profile cells, the two existing feature cells, or their rows.
- Redis or Elasticsearch anywhere in the engine. `ddev-sync` syncs neither.
- Whether `horizon` should pass `--force`. The measurement shows the command refuses under `APP_ENV=production`; that is a question about the feature definition, and this plan records it in the row instead of changing `features/horizon.yaml`.

## Final check before handing the branch on

- [ ] `uv run pytest` green; `ruff check`, `ruff format --check`, `mypy src tests tools` clean.
- [ ] `DDEV_SYNC_MATRIX_KEEP=1 uv run pytest -m matrix -k "mxfeathorizon or mxfeatsymfony" --no-cov -vv -rA` green in one run, both cells together.
- [ ] One existing cell re-run to show nothing regressed: `uv run pytest -m matrix -k mxfeatlaravel --no-cov -vv -rA`, and its four `VERIFIED.md` rows still true of it.
- [ ] `grep -rn "typo3-solr" features profiles src tests tools` returns only `profiles/VERIFIED.md`'s removal record.
- [ ] `git log --oneline` shows seven commits, linear, no merges, each ending with the `Co-Authored-By` line.
- [ ] `git status` clean. Nothing pushed.
