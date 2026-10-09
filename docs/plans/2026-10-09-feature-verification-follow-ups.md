# Feature verification — what is left, and what the branch learned

Written when `docs/plans/2026-10-09-feature-verification.md` was finished and
merged. Its working notes lived in a git-ignored scratch directory that the
process deletes; this file is the part worth keeping.

Nothing here blocks anything. Each item was reviewed, ruled "can stand", and
left on purpose.

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
asymmetry is in presentation, not in rigour. One clause per row closes it.

**Before writing that clause, re-measure both mechanisms.** The installed
project trees these were read from no longer exist (see "A cell run deletes the
kept trees" below), so the only honest route is a fresh cell run with
`DDEV_SYNC_MATRIX_KEEP=1`, or reading the vendor packages directly.

### 2. The `appears` attribution guard reads one of two trees

`tests/matrix/test_matrix.py`'s `appears` branch refuses a path that is also in
the cell's `remote/` tree, because the `files` step could then be what created
it. `tests/matrix/conftest.py` copies **both** `remote/` and `remote-only/`
onto the stand-in production host, so the guard's stated purpose is broader
than what it checks.

Inert today: `mxwordpress` is the only cell with a `remote-only/`, and it
declares no features. It needs a code change, so it was out of scope for a
comment-only pass.

### 3. A feature's sanitize-rule failure names the table, not the feature

`test_the_feature_rule_removed_the_planted_row` builds a list of bare
`FlushMarker`s, so `check.feature` is not in scope where the assertions fail.
Naming the feature needs the list to carry `(feature, marker)` pairs.

Harmless while one such marker exists — its table, `sys_file_processedfile`, is
unique to the one feature that declares it, and the test id names the cell. The
second feature with a `row_marker` is when this starts costing time.

### 4. Three features still have no run-time evidence

`horizon` needs Redis, `symfony-search` needs Elasticsearch, `typo3-solr` needs
Solr — each a new service in `tests/matrix/docker-compose.yaml`, which is why
they were scoped out. `typo3-solr` carries a second problem recorded in its
`VERIFIED.md` note: its declared command could not be located in the package at
all, so that feature may need correcting or removing rather than verifying.

`filament:upgrade` is recorded as **exercised, effect not observed** — it runs
and reports success, but its likely outputs under `public/` were created by
`filament:install --panels` and nothing removed them first, so their presence
afterwards is not attributable to it. Removing what `filament:assets` publishes
and re-running would settle it.

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
