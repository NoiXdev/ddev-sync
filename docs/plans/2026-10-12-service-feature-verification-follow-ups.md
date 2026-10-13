# Service feature verification — what is left, and what the branch learned

Written when `docs/plans/2026-10-12-service-feature-verification.md` was
finished. Its working notes lived in `.superpowers/sdd/`, which the process
treats as scratch; this file is the part worth keeping.

Nothing here blocks anything. Every item was measured, reviewed, and left on
purpose — the first two because fixing them means changing a profile, which was
ruled out for this branch when the file was commissioned, and the rest because
they are statements about reach rather than defects. Items 1 and 2 have since
been acted on by `docs/specs/2026-10-13-laravel-env-defaults-design.md`; their
entries say what closed and what did not.

## Still open

Ordered by how likely each is to reach a person using the add-on, which is the
order the whole-branch review put them in. Items 10 to 12 were appended
afterwards, by the env-defaults branch, and sit outside that ordering. By that
branch's own review, item 11 is the likeliest of the three to reach a person.

### 1. A real Horizon project's queue driver is silently rewritten to `sync` — closed

**This was the next piece of work, and it is done.** It was also this branch's
most valuable finding, recorded here rather than fixed because what it needed
was a design decision and not an edit.

`profiles/laravel.yaml:25` pinned `QUEUE_CONNECTION: sync` in `env.set`, the
strategy is `patch_keys`, which replaces a key in place and appends one that is
absent (`src/ddev_sync/operations/env.py:33`), and ENV rides in the `db` group
(`src/ddev_sync/engine/steps.py:62`). So **every** sync that included `db` — the
default — rewrote that key in the project's own `.env`. The `sync` driver
executes a dispatched job inline instead of queueing it, so the project's queue
went out of use and Horizon showed an empty one. Nothing warned about it: not
the engine's output, not the README, not the profile's own comments.

How it was found: it blocked the `mxfeathorizon` cell. The cell's recipe wrote
`QUEUE_CONNECTION=redis` and the post-sync `.env` read `sync`. The cell then
modelled a project that overrides the key back
(`tests/matrix/cells/mxfeathorizon/ddev-sync.yaml`, where the interaction was
written down) and `profiles/VERIFIED.md`'s `horizon` row carries the full chain.

Why it is not fixed here: widening at the end would ship an unreviewed profile
change on the back of a verification branch, which was ruled out when this file
was commissioned. The authority is that ruling and not the spec — worth being
exact about, because the obvious citation is wrong: the spec's "Out of scope"
(`docs/specs/2026-10-10-service-feature-verification-design.md:283-296`) names
the cells, a `typo3-solr` replacement, Redis or Elasticsearch in the engine and
the `APP_ENV=production` question, and says nothing about profile files. What
the fix needs is a decision, not an edit — warn, stop pinning the key, pin it
only when no Redis queue is configured — and that is a design question of its
own.

**What closed it** (`docs/specs/2026-10-13-laravel-env-defaults-design.md`,
carried out by `docs/plans/2026-10-13-laravel-env-defaults.md`): the decision
was taken, and it was the second of the three — stop pinning the key. A sibling
mapping, `env.default`, writes a key only when no existing candidate `.env`
declares it, and `QUEUE_CONNECTION` moved there from `env.set`, so a project's
own value survives every sync.

**The evidence is the `mxfeathorizon` cell passing *without* the override it
needed to be measurable at all.**
`tests/matrix/cells/mxfeathorizon/ddev-sync.yaml` no longer carries
`env: set: QUEUE_CONNECTION: redis`; the recipe's own `QUEUE_CONNECTION=redis`
survives the sync, the dispatched job still lands in the queue `horizon:clear`
empties, and `test_the_env_step_imposed_what_it_must_and_kept_what_it_should`
(`tests/matrix/test_matrix.py`) reads the key back out of the written `.env` —
the first matrix assertion that reads a `.env` at all. Run 2026-10-13 on
`feat/env-defaults`: 11 passed, 17 skipped, 0 failed. `profiles/VERIFIED.md`'s
`horizon` row now records the defect instead of warning about it, and its
`laravel` row's env claim rests on that assertion instead of on a hand reading
of a kept tree.

One line number above is the pre-change one, kept so the finding still reads as
it was found: the key now sits at `profiles/laravel.yaml:115`, in `env.default`.
The `src/ddev_sync/operations/env.py:33` citation in the first paragraph is
**correct and stays**: line 32 is the `def`, line 33 is the docstring. An
earlier pass added a closing sentence here giving it as `:32`, on an instruction
that said so, and reported that as verified without counting the lines. It is
back to `:33`, here and in `profiles/VERIFIED.md`.

### 2. The same pin switches `CACHE_STORE` and `SESSION_DRIVER` to `file` — half closed

`profiles/laravel.yaml:26-27`, by the same mechanism and on the same path. A
project running a Redis cache or Redis sessions locally got file-backed ones
back after every sync, with no notice.

This one is **known rather than suspected, because this branch depends on it.**
The `horizon` row's attribution argument rules out `optimize:clear` →
`cache:clear` → `RedisStore::flush()` by citing the `CACHE_STORE: file` pin,
and the post-sync `.env` was read by hand to confirm it at the time (it is
asserted now; see item 1). The branch could not have made its argument without
the behaviour it is reporting here as a hazard.

*Correction, because this sentence carried a false claim about the flush:* the
parenthesis that stood here said the `flushdb()` would have emptied the planted
queue. That is false for this installation. `RedisStore::flush()` is a
`flushdb()` on the **cache** connection, and the kept `mxfeathorizon` tree's
`config/database.php` gives the `default` connection `env('REDIS_DB', '0')` and
the `cache` connection `env('REDIS_CACHE_DB', '1')`, so the flush would have
emptied database 1 while the queue sat in database 0. The pin is still worth
citing in that argument — it is why the configured store was `file` at all —
but the queue evidence does not depend on it.

It belongs with item 1 and is out for the same reason, which is the ruling
rather than the spec.

**Status (`docs/specs/2026-10-13-laravel-env-defaults-design.md`): half closed.**
`SESSION_DRIVER` is closed - it moved to `env.default`, so a Redis-backed
session survives a sync. `CACHE_STORE` is **not**: it was deliberately returned
to `env.set`, because the setup step `optimize:clear` runs `cache:clear`
against the configured store, which would flush a memcached pointed off the
machine (`MemcachedStore::flush`) and make a dynamodb cache throw. A project
running a local Redis cache still gets `file` back unless it sets
`CACHE_STORE` in its own `env.set` (`CACHE_DRIVER` on Laravel 10 and below,
which is imposed as well). The obstacle is not that a `set` versus `default`
split cannot express it: `php artisan optimize:clear --except=cache` exists at
12.47.0, and with it `CACHE_STORE` could be defaulted. The fix lives in the
profile's setup command rather than its `env` block, and it needed a version
check: whether the flag exists on Laravel 11 and below.
`docs/plans/2026-10-13-laravel-env-defaults.md` carried out the half this item
records as closed and left that check undone; it has been done since, by
reading `src/Illuminate/Foundation/Console/OptimizeClearCommand.php` in
`laravel/framework` at **tags** - read, not run.

**The flag is available from v11.38.0, not from Laravel 11 and not from 10.**
At `v10.48.0`, `v11.0.0`, `v11.20.0` and `v11.37.0` the class declares no
`except` option anywhere: no `getOptions()` entry, and nothing reads
`option('except')`. `v11.38.0` is the first tag read that declares and reads it,
through the `getOptions()` style - `['except', 'e',
InputOption::VALUE_OPTIONAL, 'The commands to skip']` - and `v12.0.0` has it
the same way, as do `v13.0.0`, `v13.10.0` and `v13.20.0`. Only by `v13.35.0` is
it declared in `$signature` as `{--e|except= : The commands to skip}`; the
move happened somewhere between those two tags and was not bisected, and it
changes how the option is declared, not whether it exists.

**Two probes of this question were wrong, and both are worth keeping, because
together they are why this item now names its refs.** The first looked only
for the option in `$signature`, which reports 11.x and 12.x as lacking the flag:
Laravel declares a command's options in two ways, and a probe for one of them is
not a measurement of whether the option exists. The second read **branch
heads** (`10.x`, `11.x`, `12.x`) and reported what it found as a floor: a
branch head is the latest patch of its line, so it says the line has the flag by
now and nothing about which release introduced it. It put the floor at 11.0 when
11.0 through 11.37 lack it. The wrong *ref kind* was measured and the result
was reported as a version floor - the same class of error one layer up.

**What that does to the fix.** The profile runs `php artisan optimize:clear` as
a plain `run:` with **no** `allow_failure` (`profiles/laravel.yaml`, the
`setup.commands` list). Passing an option a console command does not declare
makes the command fail, so editing that entry to `optimize:clear --except=cache`
would abort a sync after the database import for every project below
`v11.38.0` - Laravel 10 and below, and the 11.0 to 11.37 releases alike. The
path a first reading of this offered, a `laravel@10` profile variant on the
model of `typo3@10` through `typo3@13`, **does not work as written**: the
variant mechanism accepts a major only
(`src/ddev_sync/profiles/registry.py`'s `_VARIANT_FILENAME` is
`^(?P<name>[a-z0-9_-]+)@(?P<major>\d+)\.yaml$`), which is why the shipped
variants are whole majors, so a `laravel@10` variant would leave exactly the
11.0 to 11.37 projects aborting. Closing the `CACHE_STORE` half therefore needs
either a mechanism that can express a minor floor, or an approach that does not
depend on the flag at all. Neither is designed here; both are open, and both are
a contract change that needs its own spec rather than an edit to this profile.
This half stays open, with its facts in.

**How far the run goes on each half.** `SESSION_DRIVER`: the env test reads
`SESSION_DRIVER=file` out of the written `.env` in the `mxfeathorizon` cell,
whose recipe declares the key nowhere - which shows that the profile's value is
present and no more than that, because against a project that declares nothing
a `default` and an imposition write the same line. That the key is in
`env.default` rather than `env.set` is a static check instead,
`test_every_env_marker_is_consistent_with_its_profile_and_its_recipe`
(`tests/matrix/test_feature_registry.py`), and the mechanism itself is proven by
`QUEUE_CONNECTION` in item 1, where an imposed key would have produced `sync`.
No cell runs a Redis-backed session store, so "a Redis-backed session survives a
sync" is the mechanism applied rather than an observed outcome. `CACHE_STORE`:
the imposition is measured, deliberately - the cell's recipe writes the contrary
`CACHE_STORE=redis`, the value a developer running a Redis cache would have, and
the env test reads `file` after the sync, so a regression that defaulted the key
would turn red. That assertion is this item's open half stated as a test.

### 3. `horizon:clear` clears exactly one queue

With no `--queue` option, which is how `features/horizon.yaml:73` calls it,
`ClearCommand::getQueue()` falls back to `config("queue.connections.redis.queue")`
— `env('REDIS_QUEUE', 'default')` in the skeleton — so it clears `default` and
leaves every other queue name untouched. Real Horizon projects commonly run
named queues (`emails`, `exports`, …), for which the feature's stated purpose
(stopping jobs enqueued against the pre-sync database from running) is only
partly achieved. The user sees "Cleared N jobs from the [default] queue" and
reasonably reads it as done. The `horizon` row lists this under "Not run".

### 3b. `filament`'s `filament:upgrade` entry cannot be credited for anything

Measured 2026-10-12, after this file was first written, in the kept
`mxfeatlaravel` tree at filament/filament v5.10.1. `features/filament.yaml`
declares two commands; the first is `php artisan filament:upgrade`. The laravel
profile's **first** setup command is `composer install`, which fires the
project's `post-autoload-dump` — `[..., "@php artisan filament:upgrade"]`, put
there by `filament:install` — and
`Filament\Support\Commands\UpgradeCommand::handle()` calls `AssetsCommand`,
`config:clear`, `route:clear`, `view:clear`.

Probe: with `public/css|js|fonts/filament` deleted, `composer install
--no-interaction` alone restored all three and printed the whole chain, no
feature command involved. The feature's own entry then runs second against work
already done.

The decision, which is why this is recorded rather than fixed: keep a command
the feature cannot be credited for, drop it, or detect the hook. Dropping it is
**not** safe without thought — a project whose `composer.json` lacks that hook
(one that never ran `filament:install`, or that pruned its scripts) has nothing
else running `filament:upgrade`, and the feature is the only thing that would.
The second command, `filament:optimize`, is unaffected and is what the row's
effect evidence rests on.

### 4. Both service features fail silently

`features/horizon.yaml:73` and `features/symfony-search.yaml:10` are both
`allow_failure: true`. A user whose Redis or Elasticsearch is unreachable from
the web container gets a green sync and an uncleared queue or a stale index,
with no line in the output saying so. `allow_failure` with no reported outcome
means the user cannot tell "skipped" from "worked", which is a product decision
rather than a row: this whole matrix exists to compensate for exactly that gap,
and the compensation only covers the cells that exist.

### 5. A `--steps setup` run meets the `APP_ENV=production` refusal for real

`SELECTION_GROUPS["setup"]` is `(StepKind.SETUP,)`
(`src/ddev_sync/engine/steps.py:70`), so a setup-only run does **not** carry the
ENV step that supplies `APP_ENV=local`. On a never-synced tree whose `.env` still
reads `production`, `ConfirmableTrait` refuses, the prompt resolves to its
default of false because the engine execs without a tty, `horizon:clear` returns
1 and clears nothing, and `allow_failure` reports it as fine.

This is the one window in which the hazard is reachable — "re-run just the setup
commands" is a plausible thing to do. The other two windows are
`env: strategy: none` and a project that overrides `APP_ENV` itself. All three
are now named in the `horizon` row and in
`tests/matrix/cells/mxfeathorizon/install.sh`; an earlier wording of both said
the hazard reached any project running `APP_ENV=production` locally, which the
engine's own step order contradicts.

### 6. The Elasticsearch service's health ceiling is thin — closed

`tests/matrix/docker-compose.yaml`'s `elasticsearch` healthcheck was 2 s ×
60 retries with no `start_period`, i.e. 120 s, measured against an 11 s local
start. On a loaded CI runner starting five containers having just pulled a
1.48 GB image, a slow start failed the cell with "the stack did not come up"
(`tests/matrix/stack.py`'s `up`) rather than with anything naming the cause. The
`redis` service's ceiling is ample; this was the one with no margin.

It now carries `start_period: 60s`, chosen over more retries because during a
start period a failing probe does not count against `retries` — so the boot
does not spend the budget that exists for a service which answered once and
then stopped, which is a different failure. 60 s is roughly five times the
observed start. **Not measured:** an actual slow runner; this widens a margin
rather than fixing an observed failure.

### 7. `compose exec` into a profiled service without `--profile` — closed

All three readers did it — `Stack.redis`, `es_count` and `es_delete`. It was
correct on the compose version this work was done on (v5.5.1, measured) and it
is what kept the readers simple, but the CI runner's compose version is pinned
nowhere and older compose refused exactly this.

All three now go through `Stack.service_exec`, which passes `--profile <name>`
before the subcommand when the cell declares that service, the way `up` and
`down` already do. Worth being exact about what this is: **no failure was ever
observed here.** It removes a dependency on one compose version's leniency, in
a file that has no way to pin that version.

### 8. Neither Redis nor Elasticsearch is synced

A user may well expect a tool that replaces the local database to deal with the
search index it just invalidated. The spec scopes it out, and for Elastica the
answer is adequate because `fos:elastica:populate` repopulates from the imported
database; for a project whose index is not derived from the database there is
nothing at all.

### 9. Two dangling references to the same non-existent document — closed

`profiles/symfony.yaml:71` said "§5.1 of the features design doc is explicit
that Messenger/Scheduler state must always be cleared", and
`profiles/typo3.yaml:68` cited "(spec §5.1)" for the same scheduler rule. No
such document exists: `docs/` holds only `plans/` and `specs/`, and none of
those specs is a features design doc or carries a §5.1. The claim was about
that section and that document, not about numbering in general — numbered
headings do exist here, `docs/specs/2026-10-08-sync-init-part-b2-design.md:232`
being `### §8 item 3`.

A third copy, in `features/horizon.yaml`, was dropped while this file was being
written. Both of these were then dropped too: each pointer's argument is made
inline in the same comment, so removing the citation costs the comment nothing.
The symfony one now states the rule as this project's own, which is what it
always was.

Scope of this item, stated because one of the lessons below is exactly this: it
covered `§5.1` and a features design doc. Other `spec §` pointers elsewhere in
the tree (`profiles/laravel.yaml:50`, `features/node-build.yaml:21`) reference
different documents and were not checked — so "no dangling pointers remain" is
not a claim this item supports.

### 10. The URL-key fix, and the three gaps it leaves

Appended by the env-defaults branch. Its review found a Critical and the branch
fixed it: a non-empty `REDIS_URL`, `DB_URL` or
`DATABASE_URL` is `array_merge`d over every discrete host, port, database, user
and password key beside it (`ConfigurationUrlParser::parseConfiguration`; an
empty value is ignored), so the profile's `DB_*` and `REDIS_HOST` pins decided
nothing at all for a project whose `.env` carried a production URL — and with
`QUEUE_CONNECTION` newly allowed to stay `redis`, that project would have
queued onto production's Redis from a developer's machine. All three keys are
now imposed as empty in `profiles/laravel.yaml`'s `env.set`, and the fix is
measured rather than argued: the `mxfeathorizon` recipe appends
`redis://:s3cret@redis.production.invalid:6379` and two
`mysql://acme:s3cret@db.production.invalid:3306/acme_prod` URLs after its own
artisan calls, and the env test reads all three back empty.

**The fourth family, found by the whole-branch review: `MAIL_URL`.** The fix
above was written against `config/database.php` and stopped there, so
`config/mail.php:43` — `'url' => env('MAIL_URL')` — went unread, and a `.env`
carrying `MAIL_URL=smtp://apikey:…@smtp.sendgrid.net:587` kept production's
relay through a sync while `MAIL_MAILER`, `MAIL_HOST` and `MAIL_PORT`, pinned
so mail cannot leave the machine, decided nothing. The lesson this branch
recorded — *read the framework's own config for the keys that feed the value,
not the profile's list for the keys it happens to name* — had been applied to
one config file, and **"the framework's own config" is plural**: a key family
is only closed once every shipped `config/*.php` that reads a URL has been
looked at. The stock skeleton has exactly four such keys (`DB_URL`,
`REDIS_URL`, `MAIL_URL` and `AWS_URL`, which names a public base URL for the
`s3` disk rather than a credential path), plus the pinned `APP_URL` and
`LOG_SLACK_WEBHOOK_URL`.

`MAIL_URL` is now imposed as the local DSN `smtp://localhost:1025`, and the
difference from the other three is load-bearing rather than a style choice:
`Mail\MailManager::getConfig` guards on `isset($config['url'])`, not on
truthiness, so an empty value still enters the parse branch, and
`$config['transport'] = Arr::pull($config, 'driver')` then leaves `transport`
null — `createSymfonyTransport` throws `Unsupported mail transport []`.
`MAIL_URL: ""` would therefore have broken mail on every Laravel 11+ project
instead of being inert. The cost of the DSN: the host and port parsed from it
outrank `MAIL_HOST` and `MAIL_PORT`, so a project needing another local mail
port overrides `MAIL_URL` beside `MAIL_PORT`. Measured the same way as the
other three — the `mxfeathorizon` recipe appends
`smtp://apikey:s3cret@smtp.production.invalid:587` and the env test reads the
local DSN back.

**Three gaps the fix does not cover.** None is observed as a failure; each is a
statement about reach, and none of them is exercised by any cell.

1. **A project whose own config reads other key names.** The three URL names,
   and the `DB_*` and `REDIS_HOST` names beside them, are the ones
   `laravel/laravel`'s shipped `config/database.php` reads. A project that
   edited that file, or that resolves its connection from keys of its own, is
   pinned by nothing the profile names. Nothing in the matrix or the unit suite
   reads a project's `config/`.
2. **Real environment variables, which beat `.env` outright.**
   `.ddev/config.yaml`'s `web_environment` (and an `environment:` block in a
   `docker-compose.*.yaml` override) puts variables into the web container, and
   the env step never touches either file — it reads and writes exactly one
   `.env`. A real variable wins over a `.env` line, which is Laravel's own
   doing — **read from source in the kept `mxfeathorizon` tree, with nothing
   run**: `Illuminate\Support\Env::getRepository()` builds its repository
   `immutable()` (`Env.php:89`), `LoadEnvironmentVariables::createDotenv` hands
   that repository to `Dotenv::create`
   (`Bootstrap/LoadEnvironmentVariables.php:84-87`), and the decisive link is
   phpdotenv's own writer: `ImmutableWriter::write()` returns false for a name
   that `isExternallyDefined`, under the comment "Don't overwrite existing
   environment variables" (`vendor/vlucas/phpdotenv/src/Repository/Adapter/ImmutableWriter.php:53-59`).
   So a `DB_URL` or `REDIS_HOST` set through `web_environment` outranks every
   pin in this profile. No cell sets `web_environment`, no probe was run against
   one, and the DDEV side of it — that `web_environment` reaches the web
   container as a real variable — is read from DDEV's documented behaviour, not
   shown here either.
3. **Other hosts, for the two keys the profile now defaults.** A kept
   `beanstalkd` queue or a memcached session store names a host of its own, and
   only `REDIS_HOST` is pinned — so "a synced project cannot reach production's
   service" holds for Redis and for nothing else. The profile's own ACCEPTED
   RISK comment names this class, next to the `sqs`/`dynamodb` case, where the
   target is a set of credentials rather than a host and no pin can reach it at
   all.

**Not a fourth gap:** `REDIS_PORT` and `REDIS_PASSWORD` stay unpinned on
purpose. 6379 is both Laravel's default and the add-on's exposed port, and the
design's ground for leaving them is that a `.env` carrying production's port or
password fails closed against the local service, which is the direction it wants
(spec decision 5). That failing-closed is the design's **inference, not a
measurement**: no run here has pointed a wrong port or a stale password at the
DDEV Redis and watched it refuse, so what the two keys actually do to a synced
project is unobserved in either direction.

### 11. `--yes` cannot say that a URL key is being blanked — dry-run half closed

Appended by the env-defaults branch's final wave, which fixed the Critical
above and deliberately left this: it is a change to the reporting surface, not
a one-liner.

The env step now **blanks** `REDIS_URL`, `DB_URL` and `DATABASE_URL`, the first
keys this tool empties rather than sets, and overwrites a fourth, `MAIL_URL`,
with a local DSN. **One output does express it:** the confirmation prompt shows
a whole-file unified diff before anything is written, so a user who runs `ddev
sync` and reads the prompt sees each line change. The gap was the two paths that
cannot. The dry-run detail rendered key *names* only, so `DB_URL` in that list
read as "will be set" rather than "will be emptied" (**closed, see below**); and
`ddev sync --yes` skips the confirmation diff entirely (`operations/env.py`'s
`assume_yes` branch), so a user who always passes `--yes` — in a script, or out
of habit — never sees a line change at all. Between the two, a project that
legitimately uses `DB_URL`, `DATABASE_URL` or `REDIS_URL` locally can lose it
without being told.
`DATABASE_URL` is the most likely: it is the Doctrine and Symfony convention
and is read by plenty of non-PHP tooling out of the same `.env`, so a Laravel
project can carry it for something that is not Laravel. The recovery —
override the key in the project's own `env.set` — is documented in a profile
comment and nowhere a user would look, and for `MAIL_URL` the recovery is
**two** overrides rather than one: the host *and* the port parsed from a URL
outrank the discrete keys, so a project that needs another local mail port sets
`MAIL_URL` beside `MAIL_PORT` or the port it sets decides nothing.

This is the whole-branch review's highest-likelihood new hit on that branch,
and it is also the item the spec explicitly put out of scope ("Reporting which
keys the env step changed"), where it belongs with the `allow_failure`
reporting question because the same output surface is at stake. Shape of a fix,
not a decision: render the value beside the name when the imposed value differs
from what the file holds, or at minimum name the keys the step will empty.

**Status: the dry-run half is closed; the `--yes` half is not.**
`engine/plan.py::_env` now names the keys whose imposed value is the empty
string in a group of their own, between the plain list and `if absent`:
`... (APP_DEBUG, ..., REDIS_HOST; emptied: DATABASE_URL, DB_URL, REDIS_URL; if
absent: QUEUE_CONNECTION, SESSION_DRIVER)`. `MAIL_URL` is deliberately **not**
in it - it is replaced with a local DSN, not emptied - so it still reads as a
plain name, and the dry-run still does not say that its value is overwritten,
nor what with. A profile with no emptied keys renders the string it always did;
`tests/cli/golden/laravel-full.txt` is the only golden that moved. What remains
true: `ddev sync --yes` shows no confirmation diff, so a user who always passes
it sees the emptying only on the dry-run, or in the sync's own output at `-v`
and above - `cli/reporter.py::starting` prints each step's `detail` from `-v` up,
so `ddev sync --yes -v` carries the same `emptied:` line - and not at all at the
default verbosity. That is deliberate - skipping the diff is what `--yes` is for -
and it is recorded here as the remaining gap rather than fixed. The
two-override recovery for `MAIL_URL` is likewise still documented in a profile
comment only.

### 12. A project's own `ddev-sync.yaml` is never validated — `env.default` closed

Appended by the same wave, and parked for the same reason: the fix is a
validation surface rather than a line.

`validate_profile` runs on profile layers (`profiles/registry.py:92`) and on
feature contributions (`features/schema.py:87`) and never on the project's own
file. So `env: default: "QUEUE_CONNECTION=sync"` — a string where a mapping
belongs — in a project's `ddev-sync.yaml` produces no error, no warning and no
default: the three runtime readers each treat a non-mapping as an empty block,
deliberately, so that a mistake in a file the user controls cannot fail a sync
mid-run (each of them now says so in place, and
`profiles/schema.py::_validate_env` says why it does the opposite). Silence is
the right behaviour for the *sync*; it is the wrong behaviour for the *user*,
who gets no hint that the block they wrote does nothing.

Spec decision 1 does not mention the project layer's case at all. A fix would
be a validation pass over the project layer that **warns** rather than raises —
which is a new output path and a new decision about what `ddev sync` refuses,
not a one-liner.

**Status: the `env.default` case is closed; the item is not.** A non-mapping
`env.default` in a project's `ddev-sync.yaml` is now a `ConfigError` from
`engine/plan.py::_env` - "`env.default` must be a mapping of keys to values,
got ..." - which is where the existing `env.file` and `append_block` refusals
already live. It is a raise rather than the warning this entry proposed, on the
precedent rather than on a new output path: the plan runs before anything is
written, so the refusal costs the user nothing, where the operation's own check
would have fired after the database import. A bare `default:` (YAML null) is
still read as nothing written - and **in a layered configuration that is worse
than it sounds**: `merge_layers` lets a project's null replace the mapping the
profile put there, so a bare `default:` in `ddev-sync.yaml` silently discards the
profile's own defaults (for laravel, `SESSION_DRIVER` and `QUEUE_CONNECTION`),
not merely adds none. Nothing refuses it. What makes it survivable is that the
dry-run shows it: the `if absent` group is gone from the ENV line
(`tests/engine/test_plan.py` pins that). Left as it is, because changing how a
null merges is a contract question about every block, not a fix to this one. What is **not** closed: a non-mapping `env.set`
is still silently no block, in the plan, the operation and
`cli/init.py::_missing_env_file` alike; `_missing_env_file` also still reads a
malformed `env.default` as empty, so `ddev sync init` does not report it and
the first plan does; and every other block a project layer can carry
(`database`, `files`, `sanitize`, `setup`) is as unvalidated as it was.

## A rule this session earned four times over

**Every check needs a positive control: something that would have come out
differently had the check itself failed.** Four separate times on this branch a
check reported success because of its own shape, not because of what it was
checking. All four are the same defect, and it is the same defect the branch
exists to remove from prose — a claim that cannot fail.

Two of the four are recorded in the branch's execution ledger and two are
session observations with no artefact in this repository. They are marked, and
the mark is the point: a file whose subject is "check your checks" cannot carry
an unsourced count.

1. *(session observation, not recorded in this tree)*
   **`uv run mypy 2>&1 | tail -1`** reports `tail`'s exit status, not mypy's —
   seen three separate times while this branch was worked on. A pipeline's
   status is its last command's; run each gate on its own line, or read
   `PIPESTATUS`.
2. *(session observation, not recorded in this tree)* **A probe script printed
   `$?` after a pipe** and reported 0 over a `doctrine:schema:create` that had
   failed. Same mechanism, one layer down: the `$?` was the pipe's tail, not the
   command being probed.
3. *(recorded, and the control is cited in `profiles/VERIFIED.md`'s `horizon`
   row)* **A GitHub contents-API sweep** for "does `laravel/horizon` ship a
   `database/` directory" would have read *identically* had the request failed:
   both a missing directory and a broken call produce no match. What made the
   zero mean absence was asserting `src/` present on every call — that control
   is the only reason the `horizon` row can say "a zero means absence rather
   than a failed request".
4. *(recorded in the branch's final verification pass)* **A `git log --format`
   footer check** put the hash and the trailer on separate lines, so `grep -cv`
   counted the hash lines and reported all 14 commits as missing their
   `Co-Authored-By` footer. The check was wrong, not the commits.

The practical form: before trusting a check that reports "clean", "absent" or
"zero", make it fail on purpose once. The `--profile` argv test added at the end
of this branch was written that way — `tests/matrix/test_stack_profiles.py`
carries its own minimum (`test_some_cell_declares_a_service_so_the_assertions_above_can_fail`)
so it cannot pass vacuously against a registry where no cell declares a service,
and dropping the argument list from `Stack.down` was confirmed to turn it red in
six places before it was committed.

## Facts about this repository that cost time to find

**A count in a comment is stale by construction.** Two committed sentences said
"six of the cells" about a registry that had eight when they were written and
ten by the end of the same branch — and the branch that added the two cells was
the branch that wrote the sentences. **Three** sites were de-counted, not two:
`Cell.features`' docstring said "the six profile cells" and was *accurate* at
both counts (6 of 10 cells declare no feature check, measured), and it was
rephrased anyway, because it sits ten lines above one of the wrong ones and will
go stale the next time a feature cell is added. `.github/workflows/matrix.yaml`'s
concurrency comment lost two numbers: "twelve", against a registry that has
never had twelve cells, and "20-minute", which nothing on this branch measured
while the job's own `timeout-minutes` is 45. Each count now lives where it is
derived — the cell list in `tests/matrix/cells.py`, which `prepare` reads, and
the per-cell ceiling in the workflow's own `timeout-minutes`.

**`GET /{index}/_count` answers identically through an alias**, so a count alone
cannot tell an index from an alias. This matters because the `symfony-search`
row's "the index is a concrete index, not an alias" conclusion originally cited
a `_cat/indices` / `_cat/aliases` reading that the cell run never performed — it
came from an exploratory probe made while planning the cell. The run's own
evidence for the conclusion is its `DELETE`, and it is weaker than a status
reading: `es_delete("app")` **did not raise** on the index the earlier sync had
populated, and it raises on any status outside 200 or 404
(`tests/matrix/stack.py:240`), while Elasticsearch answers `DELETE` on an alias
with 400. That excludes the alias. It does **not** establish that a 200 was
seen: the reader returns `None` and never yields a status, so "`es_delete`
returned 200" would be an inference dressed as an observation — in the one
paragraph whose subject is that distinction. `profiles/VERIFIED.md`'s
`symfony-search` row words it as the absence of a raise; this is the same
claim.

**That 400 is measured, against this matrix's own image.** 2026-10-12,
`docker.elastic.co/elasticsearch/elasticsearch:9.0.0` with the compose file's own
`discovery.type=single-node`, security off and 512 MB heap: with alias
`probe_alias` added to index `probe_index`, `DELETE /probe_alias` answered
HTTP 400 and `illegal_argument_exception`, reason *"The provided expression
[probe_alias] matches an alias, specify the corresponding concrete indices
instead."*, and `probe_index` was still in `_cat/indices` afterwards. With the
controls that make the 400 mean "alias" and not "bad request": `DELETE` on a
name that was neither answered 404 (`index_not_found_exception`) and
`DELETE /probe_index` answered 200 (`{"acknowledged":true}`) — which are exactly
the two statuses `es_delete` accepts, so the 400 is a raise. The same probe
confirmed the `_count` point above: `{"count":1}` with HTTP 200 both through an
index and through its alias.

**A code-scoped sweep cannot see a claim made in prose.** `README.md:96`
advertised "TYPO3's Solr and processed-files handling" among the optional
features for the whole life of this branch, after `6a472dc` had deleted
`features/typo3-solr.yaml` — true at `144a5c1`, false from the first commit
after it. Nothing caught it because the plan's Step 1 sweep was
`grep -rn "typo3-solr\|typo3_solr" features profiles src tests tools`: the
README is outside every path in that list, and the prose names the product
("Solr"), not the identifier the grep looked for. Both halves matter — widen the
paths *and* search for what a human would write, not only for what the code
calls it. The branch's own "exactly 1 reference to typo3-solr" check inherited
the same blind spot and reported clean.

**An audit of the keys a profile already names cannot see an absent key that
overrides them.** The env-defaults branch checked `profiles/laravel.yaml`'s
`env.set` key by key against the rule that decides `set` from `default`, and
found nothing wrong with the `DB_*` and `REDIS_HOST` pins. The keys that
defeated those pins — `REDIS_URL`, and `DB_URL`/`DATABASE_URL` — were
**absent** from the profile, so no pass over what was present could have reached
them however careful it was; the review that found them read `laravel/laravel`'s
own `config/database.php` and asked which keys feed the connection. That is the
practical form: to check whether a set of pins decides anything, read the
framework's config for the keys that feed the value, not the profile's list for
the keys it happens to name. Same shape as the code-scoped sweep above — a check
whose scope is the artefact under audit cannot find what that artefact omits.

**A claim corrected in one file can stand in the file it came from.** The
`APP_ENV=local` overstatement was found in a cell recipe and an evidence row,
and it was still live in the spec that specified them, at
`docs/specs/2026-10-10-service-feature-verification-design.md:207` and in that
spec's out-of-scope entry. Both are corrected now, with a note saying the
branch's own cell disproved them — the house precedent set by
`2026-10-09-feature-verification-follow-ups.md`, where two figures a run
disproved were corrected in the spec rather than only in the row.

**A matrix test that depends on an earlier test's whole sync cannot be run on
its own**, and the red it produces names the wrong cause.
`test_the_feature_command_populated_the_index` counted documents that came from
`doc` rows the *import* brings in, while invoking only `--steps setup`; run as
`-k "mxfeatsymfony and index"` it indexed the one row the install seeds and
failed with "the feature's command did not populate it". It now runs
`--steps db,setup`, so the invocation carries its own import. The sibling queue
test does not need this — its marker is planted by the test body, not imported.

## Where the authoritative version of each claim lives

`profiles/VERIFIED.md` is the authority for everything the two new cells
established. Where this file's prose and a row disagree, the row wins: it was
written against the run. The `horizon` and `symfony-search` rows carry the chains
summarised in items 1-5 and 8 above at file-and-line, including the three
conditions under which the `APP_ENV=production` refusal is reachable and the
measurement behind the index-not-alias conclusion.
