# Profile env values a project may keep — design

**Status:** approved in chat 2026-10-13, awaiting written review.

## Purpose

`profiles/laravel.yaml` pins three keys that are not the sync's business:
`QUEUE_CONNECTION: sync`, `SESSION_DRIVER: file` and `CACHE_STORE: file`.
Every sync that includes the `db` group rewrites them in the project's `.env`,
so a real Horizon project silently loses its queue — jobs run inline — and a
project using Redis for cache or sessions loses that on every sync, with
nothing said in the output.

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
- **Features can already contribute `env`.**
  `src/ddev_sync/features/schema.py:30` lists `env` in `CONTRIBUTION_KEYS`, and
  `src/ddev_sync/config/merge.py` recurses into mappings with
  `DEFAULT_LIST_MODE = "extend"`, so a later layer overrides an earlier one per
  key rather than replacing the mapping. The `mxfeathorizon` kept tree shows
  both halves of that: `QUEUE_CONNECTION=redis` from the cell's override and
  `CACHE_STORE=file` from the profile, in the same file.
- **DDEV's Redis add-on sets no environment variable.**
  `ddev/ddev-redis@main`'s `docker-compose.redis.yaml` declares
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

> Put a key in `set` when a foreign value could reach outside the machine, or
> could contradict what the sync has just done. Put it in `default` when it is
> a local preference whose wrong value stays on the machine.

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
| `REDIS_HOST` (new) | `set` | `ddev-${DDEV_SITENAME}-redis`, the same template the profile already uses for `DB_HOST` |
| `QUEUE_CONNECTION`, `SESSION_DRIVER`, `CACHE_STORE` | `default` | Local preference, and the measured harm |
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
- `profiles/laravel.yaml` — the three keys move from `set` (lines 25-27) to a
  new `default` block, and `REDIS_HOST` joins `set`.
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
  fixes it, and the failure is a loud one at the first dispatch. This is the
  trade the design chooses deliberately: the sync stops holding an opinion it
  cannot justify about every project.
- **An empty-value `.env`.** `QUEUE_CONNECTION=` keeps its empty value, where
  `patch_keys` previously replaced it. Laravel resolves that to an unknown
  connection and errors. Decision 3 accepts this: a written line is a decision,
  and a sync that silently fills blanks is the behaviour being removed.
- **`REDIS_HOST` for a project without Redis.** The key is appended naming a
  host that does not exist. It is inert — nothing reads it unless a driver
  does — but it is a key ddev-sync did not previously write, visible in the
  confirmation diff.
