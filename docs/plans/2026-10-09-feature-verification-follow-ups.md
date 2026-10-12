# Feature verification — what is left, and what the branch learned

Written when `docs/plans/2026-10-09-feature-verification.md` was finished and
merged. Its working notes lived in a git-ignored scratch directory that the
process deletes; this file is the part worth keeping.

Nothing here blocks anything. Each item was reviewed, ruled "can stand", and
left on purpose. Items marked **Closed** were done later, on the branch named
in each; the reasoning is kept rather than deleted, because what a claim rested
on is the part that is expensive to reconstruct.

## Still open

### 1. Two rows claim attribution more firmly than the reasoning they print

`profiles/VERIFIED.md`'s `node-build` and `livewire` rows rule out the install
recipe and the cell's `remote/` tree, then conclude that only the feature's own
command can have created the asserted path. That conclusion is correct, but the
confounders actually ruled out during the work were **sync-internal** and the
rows do not name them:

- `composer install` (the laravel profile's own setup step) triggers
  `post-autoload-dump`, which runs `filament:upgrade`, which runs
  `filament:assets` — and that publishes to `public/js|css|fonts/filament`,
  none of the asserted paths.
- Livewire's assets publish under the `livewire:assets` tag, not the
  `laravel-assets` tag that `post-update-cmd` uses, so `vendor:publish` is not
  the writer of `public/vendor/livewire/`.

The `filament` and `typo3-processedfiles` rows do carry their reasoning, so the
asymmetry was in presentation, not in rigour.

**Closed, by the fresh cell run this item asked for** (`mxfeatlaravel` with
`DDEV_SYNC_MATRIX_KEEP=1`, 2026-10-12, 10 passed / 17 skipped / 0 failed), and
both mechanisms came out sharper than this item stated them:

- The confounder is not only `filament:assets`. `composer install` is the
  profile's *first* setup command and fires the project's
  `post-autoload-dump`, which runs `filament:upgrade`, which calls
  `AssetsCommand`, `config:clear`, `route:clear` and `view:clear` — all of it
  before any feature command. Measured directly: with all four asserted paths
  deleted, `composer install --no-interaction` alone restored **none** of them,
  while it did write `public/css|js|fonts/filament`. The confounder is real,
  and it does not reach a single asserted path.
- The livewire half as written here was wrong in a useful direction. There is
  no competing `vendor:publish` during a sync at all: the project's
  `--tag=laravel-assets` call hangs off `post-update-cmd`, which `composer
  install` does not fire. And the tag would be wrong anyway — livewire's `dist`
  maps to `public_path('vendor/livewire')` under `livewire:assets`
  (`FrontendAssets.php:36-42`), requested only by livewire's own
  `livewire:publish --assets` (`PublishCommand.php:34`), while its service
  provider publishes nothing but the config. Two independent reasons, not one.

Both clauses are in the rows, with the probe's date and the versions.

### 2. The `appears` attribution guard reads one of two trees

`tests/matrix/test_matrix.py`'s `appears` branch refuses a path that is also in
the cell's `remote/` tree, because the `files` step could then be what created
it. `tests/matrix/conftest.py` copies **both** `remote/` and `remote-only/`
onto the stand-in production host, so the guard's stated purpose is broader
than what it checks.

Inert when written: `mxwordpress` was the only cell with a `remote-only/`, and
it declares no features. **Closed:** the guard now checks both trees and names
whichever one carries the path, so the gap is shut before a cell relies on it
rather than after.

### 3. A feature's sanitize-rule failure names the table, not the feature

`test_the_feature_rule_removed_the_planted_row` builds a list of bare
`FlushMarker`s, so `check.feature` is not in scope where the assertions fail.
Naming the feature needs the list to carry `(feature, marker)` pairs.

Harmless while one such marker existed — its table, `sys_file_processedfile`,
is unique to the one feature that declares it, and the test id names the cell.
The second feature with a `row_marker` is when it would have started costing
time. **Closed:** the list carries `(feature, marker)` pairs and all three
messages name the feature, so a failure sends a reader to the rule rather than
to the table.

### 4. `filament:upgrade` is exercised, with its effect not observed

**The three features this item used to name are settled.** It read "Three
features still have no run-time evidence — `horizon` needs Redis,
`symfony-search` needs Elasticsearch, `typo3-solr` needs Solr". The branch
planned in `docs/plans/2026-10-12-service-feature-verification.md` closed all
three: `horizon` and `symfony-search` have effect evidence from the
`mxfeathorizon` and `mxfeatsymfony` cells against a real Redis and a real
Elasticsearch, and `typo3-solr` was **deleted rather than verified** — this
file's suspicion that it "may need correcting or removing" was right, and four
checks against `TYPO3-Solr/ext-solr@main` established that its one declared
command does not exist in the package at all. `profiles/VERIFIED.md` is the
authority for each; what that branch left open is in
`docs/plans/2026-10-12-service-feature-verification-follow-ups.md`.

What remains under this number is the other half it carried:

`filament:upgrade` was recorded as **exercised, effect not observed** — it runs
and reports success, but its likely outputs under `public/` were created by
`filament:install --panels` and nothing removed them first, so their presence
afterwards is not attributable to it. This item said that removing what
`filament:assets` publishes and re-running would settle it.

**It did settle it, the other way.** Probed 2026-10-12 in the kept
`mxfeatlaravel` tree at filament/filament v5.10.1: with
`public/css|js|fonts/filament` deleted, `composer install --no-interaction`
alone restored all three and printed the whole chain — `Successfully published
assets!`, three cleared caches, `Successfully upgraded!` — with no feature
command running. The project's `composer.json` carries `post-autoload-dump:
[..., "@php artisan filament:upgrade"]`, which is what `filament:install` puts
there, and the profile runs `composer install` as its first setup command.

So the effect is not unobserved for want of a better probe: **the feature's
`filament:upgrade` entry runs second, after the profile's `composer install`
has already run the identical command, and has no observable effect left to
claim.** On a stock Filament project it is redundant by construction.

What that is *not*: a reason to delete the entry. A project whose
`composer.json` lacks the hook has nothing else running it. Whether the feature
should keep a command it cannot be credited for, drop it, or detect the hook is
a decision about what the feature promises — recorded in
`docs/plans/2026-10-12-service-feature-verification-follow-ups.md` beside the
other feature-definition questions, not decided here.

## Facts about this repository that cost time to find

**`pytest -k` matches substrings of the test id, and `matrix.yaml` runs one job
per cell with `-k "$CELL"`.** Measured: `-k mxtypo312` collects 25 tests,
`-k mxtypo31` collects 50 — both TYPO3 cells. So **no cell name may contain
another cell's name**, or one cell's CI job silently runs two cells and emits
the wrong `VERIFIED.md` row. The first draft of this work named its cells
`mxlaravelfeat` and `mxtypo3feat`; `mxlaravelfeat` contains `mxlaravel`. They
are `mxfeatlaravel` and `mxfeattypo3` for that reason, and
`tests/matrix/test_feature_registry.py` now enforces the rule instead of a
comment stating it.

**`addopts` carries `-q`, which cancels a single `-v`.** A matrix run needs
`-vv -rA` to print per-test pass/skip lines and skip reasons; with `-v` the
output collapses to dots.

**A cell run deletes the kept trees unless `DDEV_SYNC_MATRIX_KEEP=1` is set.**
`tests/matrix/conftest.py`'s teardown wipes both the installed project and the
stand-in remote. Two tasks deliberately preserved their trees so later work
could re-check claims against them; a later verification run without the
variable destroyed both.

**A rebase can falsify a comment without producing a conflict.** When `main`
gained `--no-blocking` for `mxtypo313`, this branch rebased cleanly — and two
sentences in `mxfeattypo3/install.sh` describing `mxtypo313` became false.
Nothing in git can see that class of breakage; only reading can.

## Measurements that were wrong, and where the right ones are

Two figures in `docs/specs/2026-10-08-feature-verification-design.md` came from
a hand probe and were disproved by the first cell within minutes of its first
run. Both are corrected in the spec, with a note saying the cell caught them:

- `livewire:publish --assets` publishes **eleven** files, not thirteen —
  confirmed three ways at the same v4.4.7 (the run's output, the published
  directory, and `vendor/livewire/livewire/dist` itself).
- The claim that "Vite 5 moved the manifest into `public/build/.vite/`" was
  never measured here, and the run contradicted it: vite 8.3.4 with
  laravel-vite-plugin 3.2.0 wrote `public/build/manifest.json` and no `.vite/`
  directory. The conclusion it was invented to support — assert the directory,
  not a filename — stands without it.

A third claim, that `filament:upgrade` should be recorded "in the same words
the wordpress row uses for `wp cache flush`", was found backwards: that row
states `wp cache flush` **has** effect evidence with the object-cache drop-in
the cell installs, and is unproven only on a stock site. The comparison was
removed rather than qualified.

`profiles/VERIFIED.md` is the authority for all of these. Where this plan's
prose and a committed row disagree, the row won — it was written against the
run.
