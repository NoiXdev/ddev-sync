# Service feature verification — what is left, and what the branch learned

Written when `docs/plans/2026-10-12-service-feature-verification.md` was
finished. Its working notes lived in `.superpowers/sdd/`, which the process
treats as scratch; this file is the part worth keeping.

Nothing here blocks anything. Every item was measured, reviewed, and left on
purpose — the first two because fixing them means changing a profile, which was
ruled out for this branch when the file was commissioned, and the rest because
they are statements about reach rather than defects.

## Still open

Ordered by how likely each is to reach a person using the add-on, which is the
order the whole-branch review put them in.

### 1. A real Horizon project's queue driver is silently rewritten to `sync`

**This is the next piece of work.** It is also this branch's most valuable
finding, and it is deliberately recorded here rather than fixed.

`profiles/laravel.yaml:25` pins `QUEUE_CONNECTION: sync` in `env.set`, the
strategy is `patch_keys`, which replaces a key in place and appends one that is
absent (`src/ddev_sync/operations/env.py:33`), and ENV rides in the `db` group
(`src/ddev_sync/engine/steps.py:62`). So **every** sync that includes `db` — the
default — rewrites that key in the project's own `.env`. The `sync` driver
executes a dispatched job inline instead of queueing it, so the project's queue
goes out of use and Horizon shows an empty one. Nothing warns about it: not the
engine's output, not the README, not the profile's own comments.

How it was found: it blocked the `mxfeathorizon` cell. The cell's recipe wrote
`QUEUE_CONNECTION=redis` and the post-sync `.env` read `sync`. The cell models a
project that overrides the key back (`tests/matrix/cells/mxfeathorizon/ddev-sync.yaml`,
where the interaction is written down) and `profiles/VERIFIED.md`'s `horizon` row
carries the full chain.

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

### 2. The same pin switches `CACHE_STORE` and `SESSION_DRIVER` to `file`

`profiles/laravel.yaml:26-27`, by the same mechanism and on the same path. A
project running a Redis cache or Redis sessions locally gets file-backed ones
back after every sync, with no notice.

This one is **known rather than suspected, because this branch depends on it.**
The `horizon` row's attribution argument rules out `optimize:clear` →
`cache:clear` → `RedisStore::flush()` (a bare `flushdb()`, which would have
emptied the planted queue) precisely by citing the `CACHE_STORE: file` pin, and
the post-sync `.env` was read by hand to confirm it. The branch could not have
made its argument without the behaviour it is reporting here as a hazard.

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
profile's setup command rather than its `env` block, and it needs a version
check nobody has done - whether the flag exists on Laravel 11 and below.

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
