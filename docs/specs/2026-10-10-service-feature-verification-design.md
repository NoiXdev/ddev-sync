# Verifying the features that need a service — design

`profiles/VERIFIED.md`'s feature table has five filled rows and three empty
ones. This fills two of them and deletes the third's feature, because that
feature cannot work.

## Why

The previous pass (`docs/specs/2026-10-08-feature-verification-design.md`)
verified the five features needing nothing but the framework, and deferred
three — `horizon`, `symfony-search`, `typo3-solr` — on the grounds that each
needs a service of its own and "the mechanism this spec introduces has not yet
been shown to work on a single feature" (its decision 5). The mechanism is now
shown, on `mxfeatlaravel` (four features) and `mxfeattypo3` (one), so the
precondition it named is met.

Two of the three are ordinary work: add a service, build a cell, observe an
effect. The third is not — see decision 1.

## What is already true

- `FeatureCheck` (`tests/matrix/cells.py`) carries `appears`, `disappears` and
  `row_marker`. `row_marker` is a `FlushMarker` — `table`, `insert`, `where` —
  read through `Stack.sql`. **Every probe the matrix has is SQL.**
- `tests/matrix/docker-compose.yaml` is one file, parameterised by
  `${MATRIX_CELL}`, with four services: `web`, `local-db`, `remote-db`,
  `remote`. `Stack.up` runs `compose up -d --build --wait`, so **every cell
  starts every service**.
- `tests/matrix/test_feature_registry.py` now enforces, rather than states, two
  cell-name rules: the `mx` + letters-and-digits charset, and that no cell name
  contains another's — because `.github/workflows/matrix.yaml` runs one job per
  cell with `-k "$CELL"` and `-k` matches substrings.
- The web image (`tests/matrix/web/Dockerfile`) builds `pdo_mysql`, `mysqli`,
  `intl`, `zip`, `gd`, and carries composer (pinned `composer:2.10.3`), wp-cli,
  node 22 and npm.
- `profiles/VERIFIED.md`'s header: a verified row "claims only what its run
  showed". Nothing below may be written into a row ahead of its run.

## Measured for this design, 2026-10-10

Each of these changed a decision below. None is taken from memory.

**`typo3-solr`'s declared command does not exist, and `ext-solr` registers no
CLI command at all.** Against `TYPO3-Solr/ext-solr@main`: `initializesolrconnections`
has **zero** occurrences anywhere in the repository; there is no
`Configuration/Commands.php`, which is TYPO3's mechanism for registering
console commands; `Configuration/Services.yaml` carries no `console.command`
tag; and `Classes/` has no `Command` directory (the five files matching
"Command" are `GarbageCollector`, `RecordMonitor`, `SolrWriteService`,
`SiteRepository`, `FrontendAwareEnvironment`, none a command class).

**The web image cannot install Horizon today.** `laravel/horizon` v5.50.0
lists `ext-pcntl` and `ext-posix` in `require`, not in `suggest`, so composer
refuses the package without them. Measured in an image built from this tree's
Dockerfile on `php:8.3-cli`: `posix` is present, **`pcntl` is not**
(`php -r 'function_exists("pcntl_fork")'` is false). Horizon's Redis driver is
a `suggest`: either `ext-redis` or `predis/predis ^1.1|^2.0|^3.0`. The image
has no `redis` extension.

**The Laravel cells' `.env` would make `horizon:clear` a no-op.**
`mxfeatlaravel`'s recipe writes a hand-made `.env` with `APP_KEY`, `APP_URL`
and the `DB_*` keys, and **no `APP_ENV`**. Laravel defaults `APP_ENV` to
`production`. Horizon's `ClearCommand` uses `ConfirmableTrait`, whose
`confirmToProceed()` refuses in `production` without `--force`; the engine runs
commands non-interactively through `docker exec`, so the command would return 1
and clear nothing — and the feature carries `allow_failure: true`, so the sync
would report it as fine. The real `laravel/laravel` skeleton's `.env.example`
sets `APP_ENV=local`, `QUEUE_CONNECTION=database` and `REDIS_CLIENT=phpredis`.
So the cell's omission is the cell diverging from a real project, not a defect
in the feature.

**What `horizon:clear` resolves and reports.** Read from
`laravel/horizon`'s `src/Console/ClearCommand.php`: with no `--queue` it takes
`queue.connections.{connection}.queue`, defaulting to `'default'`; it calls
`JobRepository::purge` and `QueueManager::connection(...)->clear($queue)`, then
prints `Cleared N jobs from the [default] queue.` and returns 0.

**The Redis key is prefixed, and a probe reading the obvious one would see
nothing.** Read from `Illuminate\Queue\RedisQueue`: `getQueue` builds
`'queues:' . $queue`, and `clear` runs a Lua script over **four** keys — the
queue plus `:delayed`, `:reserved` and `:notify`. The cluster-safe
`queues:{default}` form applies only to a cluster connection, which a single
Redis service is not. But `laravel/laravel` v13's `config/database.php` sets
the Redis prefix to `Str::slug(env('APP_NAME', 'laravel')) . '-database-'`, and
no cell's `.env` sets `APP_NAME`, so the key a job actually lands under is
**`laravel-database-queues:default`**. A `redis_marker` naming
`queues:default` would find nothing at plant time — loudly, since the marker
asserts presence before the sync, but it would cost a cell run to discover.

**Elasticsearch's floor, and what it actually costs.**
`friendsofsymfony/elastica-bundle` v7.2.0 requires `ruflin/elastica ^8.0 ||
^9.0`. Elastica 9.0.0 wraps `elasticsearch/elasticsearch ^9.0` and 8.x wraps
`^8.4.1`, so composer resolving to the newest means an **Elasticsearch 9**
server — the first JVM service in this matrix.

It is cheaper than that makes it sound. Measured: `docker.elastic.co/elasticsearch/elasticsearch:9.0.0`
with `discovery.type=single-node`, `xpack.security.enabled=false` and
`ES_JAVA_OPTS=-Xms512m -Xmx512m` answered on its HTTP port **11 seconds** after
`docker run`, reporting cluster status `green` on a single node. That is the
same order as the MariaDB services already in the stack, so decision 2's case
rests on the image size and the 512 MB of heap, **not** on a slow start — an
earlier draft of this design implied minutes, and the measurement does not
support it.

## Decisions

1. **`typo3-solr` is deleted, not verified.** Its one command is fictional
   (measured above), so there is nothing a cell could observe. `allow_failure`
   kept it from aborting syncs, which is why it survived this long. Its
   `VERIFIED.md` row becomes the record of *why* the feature is gone rather
   than an empty row: a deleted feature with no trace invites someone to add it
   back from the same guess. Rebuilding a Solr index after an import is a real
   need, and nothing here says otherwise — only that this was not the mechanism
   for it.

2. **Services start only where they are used.** Redis and Elasticsearch join
   the compose file behind `profiles:`, and `Cell` gains
   `services: tuple[str, ...] = ()`, which `Stack` turns into `--profile`
   arguments. Defaulted, so the eight existing cells declare nothing and start
   exactly what they start today. The alternative — putting both services in
   the shared file unconditionally — would make six cells that touch neither
   pay for an Elasticsearch start on every nightly run.

3. **Two concrete probe fields, not one abstraction.** `redis_marker` and
   `index_marker`, each shaped like the evidence it carries, beside the
   existing `row_marker`. Two instances are not enough to see the right
   abstraction, and `flush_marker`/`row_marker` are the precedent: a named
   concrete field per kind of evidence. A third service is the moment to
   generalise, not before.

4. **`pcntl` joins the shared web image**, because without it `composer require
   laravel/horizon` fails outright. Like node and npm before it, this must be
   shown inert for the cells that do not use it rather than assumed to be.

5. **Two new cells, `mxfeathorizon` and `mxfeatsymfony`.** Horizon could have
   gone into `mxfeatlaravel`, which is already a Laravel with features — but
   that cell's four rows state the environment they were measured against, and
   adding Horizon and Redis makes that statement stale until the cell re-runs
   and all four rows are rewritten. A new cell leaves them alone and keeps
   Redis out of their runs, which is the point of decision 2. Both names were
   checked against the non-containment rule, which a test now enforces.

6. **Both features in one spec, against the author's recommendation.** The two
   halves cost very differently: `horizon` is a small service and a probe that
   mirrors `row_marker`, while `symfony-search` needs a JVM service, a third
   probe kind over HTTP, and app code scaffolded into the cell — an entity and
   `fos_elastica` configuration, which no existing recipe does. Splitting was
   recommended on the same reasoning the previous spec's decision 5 used. The
   owner chose one pass. Recorded because it is a real risk: if the
   Elasticsearch half turns out to be a dead end, the finding is reported and
   the halves are split then, rather than the whole spec being lost.

## Design

### The registry

```python
@dataclass(frozen=True)
class RedisMarker:
    """A queue entry a cell plants so a feature's Redis command can be seen working."""

    key: str
    push: str          # a command run in the web container that plants one entry
    expected_before: int = 1


@dataclass(frozen=True)
class IndexMarker:
    """What a cell expects of a search index before and after the sync.

    `expected_before` is 0 because an index the sync is about to build is
    either absent or empty beforehand - the mirror of `RedisMarker`, whose
    planted entry must be *present* before. `minimum_after` is a floor rather
    than an exact count: the document count follows from the cell's seed, and
    pinning it here would make the marker break whenever the seed grows.
    """

    index: str
    expected_before: int = 0
    minimum_after: int = 1
```

`FeatureCheck` gains `redis_marker: RedisMarker | None = None` and
`index_marker: IndexMarker | None = None`. `Cell` gains
`services: tuple[str, ...] = ()`.

### The stack

`Stack` gains two readers in the shape of `sql`:

- `redis(*args)` → `compose exec -T redis redis-cli …`, raising on a failed
  call so an error cannot read as "no entries".
- `es(path)` → an HTTP GET against the Elasticsearch service from inside the
  stack, raising likewise.

`_env` gains nothing; `up` passes one `--profile <name>` per entry in
`cell.services` before the `up` subcommand, which is where compose requires it.

**Each new service's compose profile carries the same name as the service**, so
`services: ("redis",)` both selects the profile and names what `Stack.redis`
execs into. One name per service, or a reader has to hold two.

### The two cells

**`mxfeathorizon`** — `profile: laravel`, `services: ("redis",)`. Its recipe
builds the same skeleton as `mxfeatlaravel`, then `composer require
laravel/horizon predis/predis`, and writes a `.env` that differs from
`mxfeatlaravel`'s in three keys the measurements above demand:
`APP_ENV=local` (without it the command refuses and clears nothing),
`QUEUE_CONNECTION=redis` (without it the queue Horizon clears is not the one a
job lands in), and `REDIS_CLIENT=predis` (the image has no `redis` extension).
Evidence: a `redis_marker` keyed `laravel-database-queues:default` — the
measured key, prefix included, with a comment saying where the prefix comes
from so the string does not read as a mystery. The recipe pushes one job, the
test confirms it is there before the sync, and the sync must have removed it.

**`mxfeatsymfony`** — `profile: symfony`, `services: ("elasticsearch",)`.
Built like `mxsymfony`, plus `friendsofsymfony/elastica-bundle`, plus the app
code `fos:elastica:populate` needs to run at all. Evidence: an `index_marker`
— the index is empty or absent before the sync and holds the seeded rows after.

### What the rows will say

Three rows change. `horizon` and `symfony-search` are filled from their runs,
through `tools/apply_verified_rows.py` as the other feature cells are.
`typo3-solr`'s row records the removal and the four pieces of evidence behind
it, so the conclusion is checkable without re-deriving it.

## What the plan measured, so nothing was written from a guess

This section listed six unknowns. **All of them are now measured.** The
Elasticsearch image, version, startup environment and cost; the Redis key a
job lands under and what `horizon:clear` resolves — those moved up into
"Measured for this design". The Symfony application side, which this section
said could not be settled without standing the bundle up, was settled by
standing the bundle up: the plan's Task 6 carries the resolved versions, the
working configuration, the entity, the index name as Elasticsearch reports it,
and the document count.

Two of those findings are worth repeating here, because each would have failed
a cell run and neither appears in any documentation this design consulted:

- **FOSElasticaBundle does not register itself.** After `composer require`,
  `config/bundles.php` holds only what the skeleton and `orm-pack` put there.
  Without a hand-added entry Symfony does not know the `fos_elastica`
  namespace, so *every* console call dies on the configuration file — not just
  `populate`.
- **The client key is `hosts`, a list.** `url` — the obvious guess, and the one
  this design's own sketch implied — is rejected outright by v7, and the error
  names the whole schema.

Also measured, and it changes a test rather than a recipe: `populate` resets
and recreates its index, and before it has ever run the index does not exist.
So `Stack.es_count` reading a 404 as zero is on the main path, not a defensive
branch — and the index has to be deleted before the run a test means to
attribute, because by file order an earlier whole-sync has already populated
it once.

One item remains open, and it is small: **that `predis/predis` serves Horizon
with no `redis` extension present.** It is Horizon's own documented
alternative, so the risk is low, but the image has neither driver today and
nothing here has run with either. The plan's Task 5 installs it, and that
cell's run is what settles it.

## Testing

The cells are the test, as before. Two things sit beside them:

- **The registry guard extends to services.** `tests/matrix/test_feature_registry.py`
  already refuses a `FeatureCheck` naming a feature that does not exist; a
  `Cell.services` entry naming a compose profile that does not exist deserves
  the same unmarked, no-Docker check, for the same reason — a typo should cost
  a collection error, not a cell run.
- **The six profile cells and the two existing feature cells must be shown
  unchanged**, in two respects: they start no new service (decision 2), and
  `pcntl` in the shared image changes no plan of theirs (decision 4). The
  previous branch established the pattern for the second: it grepped every
  recipe to show no other cell touches npm, so `node-build` could not activate
  anywhere else. The same evidence is owed here.

Everything the new rows claim comes from a run. Where a feature's command has
no observable effect, that is recorded as a finding rather than worked around —
the standard the previous pass set with `filament:upgrade`.

## Out of scope

- Any change to the six profile cells, the two existing feature cells, or their
  rows.
- A replacement for `typo3-solr`. Deciding what TYPO3 actually needs after an
  import — a scheduler task, an index-queue operation — is its own question,
  and this spec only establishes that the current answer is wrong.
- Redis or Elasticsearch anywhere in the engine. `ddev-sync` does not sync
  either, and nothing here changes that.
- The `horizon:clear` behaviour on a project that genuinely runs
  `APP_ENV=production` locally. The measurement above shows the command would
  refuse there; whether the feature should pass `--force` is a question about
  the feature, and the cell models a real project instead (`APP_ENV=local`, as
  the skeleton ships). It belongs in the row's "not run", not in this design.
