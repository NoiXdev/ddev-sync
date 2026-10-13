# Profile env values a project may keep — design

**Status:** approved in chat 2026-10-13, awaiting written review.

## Purpose

`profiles/laravel.yaml` pins three keys that are not the sync's business:
`QUEUE_CONNECTION: sync`, `SESSION_DRIVER: file` and `CACHE_STORE: file`.
Every sync that includes the `db` group rewrites them in the project's `.env`,
so a real Horizon project silently loses its queue — jobs run inline — and a
project using Redis for cache or sessions loses that on every sync, with
nothing said in the output.

*What this change delivers, as built:* the queue and the session driver. The
cache half is **not** fixed and is imposed on purpose, under both its names
(`CACHE_STORE`, and `CACHE_DRIVER` for Laravel 10 and earlier), because the
sync's own `optimize:clear` flushes whatever cache is configured; see
decision 5. The paragraph above describes the problem as first found.

This design separates the values a project may keep from the values the sync
must impose, and adds the one declaration needed to express the difference.

Success is: a Laravel project that has configured `QUEUE_CONNECTION=redis`
still has it after a sync; a project that configures nothing still gets a local
setup that works; and a project whose `.env` came from production cannot reach
production's Redis from the developer's machine.

## What was measured

Everything in this section was observed on this repository at `73363a9`, in the
`mxfeathorizon` and `mxfeatlaravel` cells or in the sources named.

- **The harm.** `profiles/laravel.yaml:9-11` opens the `env` block with `file: [.env]` and
  `strategy: patch_keys`, and lines 25-27 hold `QUEUE_CONNECTION: sync`,
  `SESSION_DRIVER: file` and `CACHE_STORE: file` inside `env.set`.
  `src/ddev_sync/engine/steps.py:62` puts the ENV step in the `db` group and
  `STEP_ORDER` runs it before SETUP, so every `db` sync applies it before any
  feature command. The first `mxfeathorizon` run lost its planted job to
  exactly this: the queue the job was dispatched to was no longer the queue
  Horizon cleared, because `dispatch()` ran inline. The cell only became
  measurable after adding `env: set: QUEUE_CONNECTION: redis` to its own
  `ddev-sync.yaml`.
- **`patch_keys` appends absent keys**, not only overwrites present ones
  (`src/ddev_sync/operations/env.py:32`, whose docstring says so in as many
  words: "Replace each key's value in place; append keys that are not
  present."). Observed: the
  `mxfeathorizon` recipe writes a 13-line `.env`, and after a sync the file is
  19 lines, the six added being `APP_DEBUG`, three `MAIL_*`, `SESSION_DRIVER`
  and `CACHE_STORE`, in the profile's `env.set` order.
- **Only `laravel` pins driver choices.** `symfony` and `shopware6` also run
  `patch_keys`, but their `env.set` holds connection targets (`DATABASE_*`,
  `DATABASE_URL`, `MAILER_DSN`) plus `APP_ENV`/`APP_DEBUG` and nothing else.
  `drupal`, `typo3`, `wordpress` and `generic` run `strategy: none`.
- **The ENV step reads and writes exactly one file**: `_find_env_file`
  (`src/ddev_sync/operations/env.py:125`) returns the first candidate from
  `file:` that exists, matched against `iterdir()` entries so the probe cannot
  fold case. For `laravel` that is always `.env`; for `symfony` and
  `shopware6` it is `.env.local` when present, otherwise `.env`.
- **`EnvRewriteOperation.execute` returns early when `env.set` is empty or
  absent** (`src/ddev_sync/operations/env.py:105-106`).
- **The `env` mapping merges per key, and a feature could contribute one.** Two
  halves, resting on different things, so they are stated apart.
  *The merge is asserted:* `src/ddev_sync/config/merge.py` recurses into
  mappings with `DEFAULT_LIST_MODE = "extend"`, so a later layer overrides an
  earlier one per key rather than replacing the mapping - and the
  `mxfeathorizon` cell's own `ddev-sync.yaml` sets `MAIL_PORT: "2525"` against
  the profile's imposed `1025`, after which the written `.env` reads
  `MAIL_PORT=2525` beside the profile's own `MAIL_HOST=localhost`. The pair is
  the evidence rather than either half, because `MAIL_PORT` alone would read
  `2525` just as well had the profile stopped writing the key
  (`test_the_env_step_imposed_what_it_must_and_kept_what_it_should`, with each
  key's layer membership checked statically by
  `test_every_env_marker_is_consistent_with_its_profile_and_its_recipe`).
  *The feature half is the schema only:* `src/ddev_sync/features/schema.py:30`
  lists `env` in `CONTRIBUTION_KEYS`, so a feature may carry an `env` block -
  but no shipped feature declares one and no cell exercises it, so that a
  **feature** layer merges this way is read from the code, not run.
  The earlier evidence for the merge - `QUEUE_CONNECTION=redis` from this cell's
  override beside `CACHE_STORE=file` from the profile, read by hand in the kept
  tree - belonged to the override this change removes; the `MAIL_PORT` pair
  replaces it, and is asserted rather than read by hand.
- **DDEV's Redis add-on sets no environment variable.** Read from installed
  copies of `ddev-redis` v2.2.0, outside this repository, which nothing in the
  tree can establish; a later release may differ. Its `docker-compose.redis.yaml`
  declares
  `container_name: ddev-${DDEV_SITENAME}-redis` and
  `hostname: ${REDIS_HOSTNAME:-redis}`, exposes 6379, documents "Redis is
  available inside Docker containers with `redis:6379`" in its README, and
  carries no `environment:` or `web_environment:` block. So nothing outside the
  project's own `.env` points Laravel at it, and `REDIS_HOSTNAME` is
  user-overridable while the container name is not.
- **`horizon:clear` is independent of `queue.default`.** It resolves its
  connection from the first `horizon.defaults` supervisor, so the key this
  design stops overwriting is not the key that command reads. The harm is to
  the project, not to the feature's command.

## Decisions

### 1. A sibling mapping, `env.default`

`env.default` is a mapping beside `env.set`, with the same placeholder
expansion and the same per-key merge semantics. It is applied after `set` and
never overwrites.

```yaml
env:
  file: [.env]
  strategy: patch_keys
  set:        # always imposed
    DB_HOST: "ddev-${DDEV_SITENAME}-db"
    REDIS_HOST: "ddev-${DDEV_SITENAME}-redis"
  default:    # written only when the key is absent
    QUEUE_CONNECTION: sync
```

Rejected: a per-key annotation (`QUEUE_CONNECTION: {value: sync, only_if_absent:
true}`), because it turns a flat string mapping into a union type and breaks
every existing reader, the placeholder expansion and the schema validation. Also
rejected: a new strategy such as `patch_keys_defaults`, because a profile needs
both behaviours at once — `DB_HOST` always, `QUEUE_CONNECTION` only when
absent — and a strategy applies to the whole file.

`append_block` keeps its current meaning and ignores `default`: it writes a
block rather than reconciling keys, so "only if absent" has no place in it. A
profile that sets `default` with `strategy: append_block` is a configuration
error and is rejected with a message naming both.

### 2. Absent means absent from every candidate file that exists

The presence test reads **all** existing entries of `file:`; the write still
goes to the first. For `laravel`, with one candidate, this is identical to
testing the written file. For `symfony` and `shopware6` it is the difference
between correct and surprising: a key set in `.env` but not in `.env.local`
would otherwise look absent, and a default written into `.env.local` would
override the project's configured value through Dotenv's overlay.

### 3. An empty value counts as present

`REDIS_PASSWORD=` means "no password", not "unconfigured". A line the developer
wrote is a decision, and overwriting it is the surprise this design removes. A
default applies only when no line declares the key at all.

### 4. The rule for which mapping a key belongs in

> **Impose a key when the sync's own work makes the project's value wrong or
> dangerous. Default a key when the project's value is no more wrong after the
> sync than it was before.**

"Driver versus connection target" does not survive contact with the list:
`MAIL_MAILER: smtp` is a driver choice and must stay imposed, because a local
project holding freshly imported production addresses must not send real mail.
That is the same reason the sanitize rules exist.

### 5. The laravel profile's keys under that rule

| Key | Mapping | Why |
| --- | --- | --- |
| `APP_ENV: local` | `set` | Load-bearing for the framework's guards: `ConfirmableTrait` refuses under `production` and returns 1, which `allow_failure` reports as fine |
| `APP_URL`, `DB_CONNECTION`, `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` | `set` | The database the sync has just imported; `DB_CONNECTION: mysql` is a consequence of the import, not a choice |
| `MAIL_MAILER`, `MAIL_HOST`, `MAIL_PORT` | `set` | Mail must not leave the machine |
| `REDIS_URL`, `DB_URL`, `DATABASE_URL` (new) | `set`, empty | A non-empty URL key outranks every discrete key beside it (`ConfigurationUrlParser::parseConfiguration` array-merges the parsed URL over host, port, database, username and password; an empty value is ignored), so without these the `DB_*` and `REDIS_HOST` pins decide nothing for a project whose `.env` carries a production URL. `laravel/laravel` reads `REDIS_URL` at 10.x to 12.x, and `DATABASE_URL` at 10.x where 11.x and 12.x read `DB_URL`. Found by the review of Task 3, not by the design |
| `REDIS_HOST` (new) | `set` | `ddev-${DDEV_SITENAME}-redis`, the same template the profile already uses for `DB_HOST` |
| `QUEUE_CONNECTION`, `SESSION_DRIVER` | `default` | Nothing the sync runs acts on either; the project's value is no more wrong after the sync than before, and `QUEUE_CONNECTION` is the measured harm |
| `CACHE_STORE`, `CACHE_DRIVER` | `set` | The cache key is `CACHE_STORE` from Laravel 11 and `CACHE_DRIVER` before it (`laravel/laravel`'s `.env.example` at 10.x, 11.x, 12.x), so both are imposed. The sync's own `optimize:clear` runs `cache:clear` against the configured store: `MemcachedStore::flush()` flushes a whole server, `DynamoDbStore::flush()` throws, `RedisStore::flush()` is a bare `flushdb()`. Moved back to `set` after the key-by-key check of decision 4's reworded rule (see Risks) |
| `APP_DEBUG` | `set`, unchanged | Neither case; out of scope, so the change stays no wider than the harm it fixes |

**The `REDIS_HOST` pin and its accepted cost.** Pinning it recreates this
design's own defect for a developer whose Redis is not the DDEV service — on
the host, or behind a renamed `REDIS_HOSTNAME`. It is accepted because the two
failures differ in kind: a wrong driver ran jobs inline and invisibly, while a
wrong host either refuses the connection loudly or reaches production's Redis,
which is the case this design exists to prevent. Such a project uses a
project-level override, the same mechanism the `mxfeathorizon` cell used. The
container name is chosen over `redis` because `REDIS_HOSTNAME` is
user-overridable and `container_name` is not.

`REDIS_PORT` and `REDIS_PASSWORD` stay unpinned: 6379 is both Laravel's default
and the add-on's exposed port, and pinning a password would assert something
about infrastructure this project cannot see. A `.env` carrying production's
port or password therefore fails loudly against the local service, which is the
intended direction.

### 6. The early return has to learn about `default`

`EnvRewriteOperation.execute` returns before doing anything when `env.set` is
empty or absent. With `default` in the schema, a profile carrying only
`default` must still be applied, so the guard becomes "neither mapping has
anything".

### 7. Verification closes a gap the last branch found

The final review of `feat/service-feature-verification` established that **no
matrix test reads any `.env`**: the evidence for the env step's effect was a
hand reading of a kept tree, which leaves no trace and turns nothing red on
regression. This change is the right occasion to close that, because the
assertion it needs is exactly the one that proves the fix:

- The `mxfeathorizon` cell drops its `env: set: QUEUE_CONNECTION: redis`
  override. The cell's `install.sh` already writes `QUEUE_CONNECTION=redis`
  into `.env`, so after this change the profile must leave it alone — and the
  cell passing without the override *is* the evidence.
- A new matrix assertion reads the written `.env` after a sync and checks, per
  cell declaration: keys the profile imposes hold the imposed value, and keys
  the project set hold the project's value. That makes the laravel row's
  `env patch_keys` claim run-time evidence rather than a hand reading.
- `profiles/VERIFIED.md`'s `laravel` row (line 28) and `horizon` row (line 49)
  both describe the old behaviour, including the override and the trap. Both
  need rewriting against the new runs, and the `horizon` row's trap paragraph
  becomes a record of what was fixed rather than a warning.

## Implementation surface

Measured against the tree at `73363a9`, so a plan can start from it.

- `src/ddev_sync/operations/env.py` — read `env.default`, test presence across
  every existing candidate from `file:`, and widen the early return to "neither
  mapping has anything". `patch_keys` itself does not change: the operation
  decides which keys to hand it.
- `src/ddev_sync/profiles/schema.py` — `_validate_env` (line 36) today checks
  only that `env` is a mapping and the strategy is known; there is **no
  allow-list of sub-keys**, so `default` is already accepted. What it needs is
  the two new rejections: `default` must be a mapping, and `default` with
  `strategy: append_block` is an error naming both.
- `profiles/laravel.yaml` — `QUEUE_CONNECTION` and `SESSION_DRIVER` move from
  `set` (lines 25-26) to a new `default` block; `CACHE_STORE` stays in `set`
  (decision 5), `CACHE_DRIVER` joins it, and `REDIS_HOST` joins `set`.
- `tests/operations/test_env.py` and `tests/profiles/test_schema.py` — the unit
  coverage. Both new branches need a test apiece: the repository requires 100%
  line **and** branch coverage of `src/ddev_sync` and forbids
  `# pragma: no cover`.
- `tests/matrix/cells/mxfeathorizon/ddev-sync.yaml` — drop the
  `env: set: QUEUE_CONNECTION: redis` override, which this change makes
  unnecessary. The cell's comments and `install.sh` explain that override and
  have to change with it.
- `tests/matrix/cells.py` and `tests/matrix/test_matrix.py` — the new `.env`
  assertion and whatever a cell declares to drive it.
- `profiles/VERIFIED.md` — the `laravel` row (line 28) and the `horizon` row
  (line 49).

## Out of scope

- `APP_DEBUG`, and any other key not named in decision 5.
- `symfony` and `shopware6` key assignments. They pin no driver choices today,
  so they gain the mechanism and keep their behaviour. Whether Shopware's Redis
  and Elasticsearch keys deserve pinning is its own question.
- `horizon:clear` clearing only the `default` queue (follow-ups item 3), the
  redundant `filament:upgrade` entry (item 3b), and how a sync reports a
  tolerated failure (items 4 and 5). Each is a separate decision about what a
  feature promises.
- Reporting which keys the env step changed. Worth doing, and larger than this:
  it belongs with the `allow_failure` reporting question, where the same output
  surface is at stake.
- Any change to `strategy: none` profiles.

## Risks

- **A project relying on the overwrite.** A `.env` whose `QUEUE_CONNECTION` is
  wrong for local use — copied from production, naming `sqs` — keeps that value
  after this change instead of being corrected to `sync`. The sync no longer
  fixes it. This is the trade the design chooses deliberately: the sync stops
  holding an opinion it cannot justify about every project.

  *Correction:* an earlier version of this bullet said the failure "is a loud
  one at the first dispatch". That is false, and a review of Task 3 of this
  very change showed it. A `.env` copied from production with
  `QUEUE_CONNECTION=sqs` and live AWS keys does not fail: the first dispatch
  silently enqueues onto the production queue. `SESSION_DRIVER=dynamodb` has the
  same shape - a driver whose target is a set of credentials rather than a host, which the `REDIS_HOST`-style pin cannot cover. This is an
  accepted risk of defaulting those keys, and it is now stated in the profile
  as well.

  *Correction to decision 4:* the same review found that decision 4's rule, as
  first written ("could reach outside the machine ... a local preference whose
  wrong value stays on the machine"), was false for the three keys it was
  written to justify, so its wording was replaced. The rule now says what
  actually separates the keys - whether the sync's own work is what makes the
  value wrong. `DB_*`, `APP_URL`,
  `MAIL_*`, `APP_ENV` and `REDIS_HOST` are imposed because the sync imports a
  database, sets up the local site, imports real addresses into a machine that
  can send mail, and runs commands that consult `APP_ENV` and flush the
  configured cache (`optimize:clear` runs `cache:clear`; checked against
  laravel/framework v12.47.0, whose `RedisStore::flush()` is a bare `flushdb()`
  and whose `MemcachedStore::flush()` flushes the whole server). `REDIS_HOST`
  is imposed for that reason: a project that keeps `CACHE_STORE=redis` through
  its own `env.set` would otherwise have the sync flush production's Redis. `QUEUE_CONNECTION` and `SESSION_DRIVER`
  are defaulted because an `sqs` queue or a `dynamodb` session store was
  production's before the sync and still is, and nothing the sync runs acts on
  either.

  *That check moved a key.* Applied to `CACHE_STORE`, the rule fails:
  `optimize:clear` runs `cache:clear` against the configured store, so a kept
  `memcached` pointed off the machine is flushed by the sync itself
  (`MemcachedStore::flush` is `$this->memcached->flush()`), and a kept
  `dynamodb` makes that step throw. The sync would be destroying data on
  infrastructure it does not own. `CACHE_STORE` therefore returns to `set`, as
  decision 5's table now shows. This costs a project with a local Redis cache
  its choice on every sync, as before this change; it keeps the choice by
  setting `CACHE_STORE` (`CACHE_DRIVER` on Laravel 10 and below) in its own
  `env.set`, and `REDIS_HOST` keeps the flush local. A third review finding:
  the key was renamed in Laravel 11, so imposing `CACHE_STORE` alone left every
  Laravel 10 project exposed and the reasoning decorative; `CACHE_DRIVER` is
  imposed as well, at the cost of one inert line in a Laravel 11+ `.env`.
  Defaulting `CACHE_STORE` after all is possible in principle - `php artisan
  optimize:clear --except=cache` exists at 12.47.0 - but the fix then lives in
  the profile's setup command rather than in its `env` block, and whether the
  flag exists on Laravel 11 and below has not been checked.

  *The rule does not cover every imposed key, and the list is not a count to
  trust:* three imposed keys sit outside its wording. `APP_DEBUG` is imposed
  although the sync does not make it wrong (decision 5 keeps it out of scope).
  `APP_ENV`'s documented ground runs the other way round - the project's value
  breaks the sync's own commands, rather than the sync making the value wrong.
  And `CACHE_DRIVER` is, on Laravel 11 and later, a key nothing reads, imposed
  deliberately for the versions that do. A fourth group, the URL keys, fits the
  rule (the sync's import is what the URL would override) but was missed by
  the design and found by review: a non-empty `REDIS_URL` or `DB_URL` defeated
  the discrete pins, and a kept `QUEUE_CONNECTION=redis` made that reach
  production's Redis, which this change had made possible.
- **An empty-value `.env`.** `QUEUE_CONNECTION=` keeps its empty value, where
  `patch_keys` previously replaced it. Laravel resolves that to an unknown
  connection and errors. Decision 3 accepts this: a written line is a decision,
  and a sync that silently fills blanks is the behaviour being removed.
- **`REDIS_HOST` for a project without Redis.** The key is appended naming a
  host that does not exist. It is inert — nothing reads it unless a driver
  does — but it is a key ddev-sync did not previously write, visible in the
  confirmation diff.
