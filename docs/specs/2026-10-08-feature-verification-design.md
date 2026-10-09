# Verifying features in the matrix — design

The matrix proves what a *profile* claims. It has never proved anything a
*feature* claims. This gives it the means to, and uses them on the five
features that need no service of their own.

## Why

`profiles/VERIFIED.md` has two tables. The profile table has six filled rows.
The feature table has **none**: `node-build`, `filament`, `livewire`,
`inertia`, `horizon`, `typo3-processedfiles`, `typo3-solr` and
`symfony-search` all read "not yet verified", and two of them say more than
that — `typo3-solr`'s note records that its declared command
`solr:initializesolrconnections` could not be located in the package at all,
and `node-build`'s that `${PM}`/`${PM_RUN}` expansion is "checked by unit
test only".

That is eight claims shipping with no run-time evidence, in a repository whose
own rule is that "a profile declares what a framework's commands are. Every
one of those is a claim, and claims in this repository are verified rather
than assumed" (CONTRIBUTING.md). The profile table earned that rule the hard
way: the typo3@13 cell found that two commands the profile declared are not
TYPO3 core commands at all, and that one of them was reported `OK` because the
step was `allow_failure`. Features are declared the same way, run the same
way, and are `allow_failure` even more consistently — so the same class of
defect is as available to them, and nothing would catch it.

## What is already true

- A matrix cell is one **profile variant**, installed for real and synced end
  to end. Its recipe is a directory under `tests/matrix/cells/<name>/` -
  `install.sh`, `ddev-sync.yaml`, `seed.sql`, `keep-data.txt` and a `remote/`
  tree - plus one entry in `tests/matrix/cells.py`'s `CELLS`.
- `cells.py`'s docstring states the premise this design has to respect:
  "Everything a cell asserts is declared here rather than written into a test
  body: adding a cell is then a registry entry plus three recipe files, not a
  new test."
- The `Cell` dataclass is entirely profile-shaped: `required_tables`,
  `truncated_tables`, `admin_user`, `password_column`, `identity_column`,
  `seeded_users`, `production_url`, `flush_marker`. **There is no field, and
  no concept, for a feature.** `grep -rn feature tests/matrix/*.py` finds one
  hit, in a comment about what the image bundles.
- The matrix stack (`tests/matrix/docker-compose.yaml`) has four services:
  `web`, `local-db`, `remote-db`, `remote`. No Redis, no Elasticsearch, no
  Solr.
- The web image (`tests/matrix/web/Dockerfile`) is a PHP image plus composer,
  a MariaDB client, git/unzip, `pdo_mysql`, `mysqli`, `intl`, `zip`, `gd` and
  wp-cli. **It has no node and no npm.**
- `FlushMarker` already encodes "a row this cell plants, which the sync must
  have removed" — the exact shape one of the five features needs.
- Cell runtime in CI, from the 2026-10-08 nightly: `mxtypo312` 2m44s,
  `mxtypo313` 2m29s, `mxdrupal` 2m54s, `mxsymfony` 2m19s, `mxlaravel` 2m50s.
  A cell costs about three minutes, and the cells run in parallel.

## Decisions

1. **Two new cells, not changes to the existing ones.** The filled profile
   rows rest on having run against an untouched skeleton — the laravel row
   says "against a fresh `laravel/laravel` skeleton, profile unchanged".
   Installing `filament/filament` and `livewire/livewire` into `mxlaravel`
   would make that sentence false, and the row would have to be weakened to
   buy three minutes of runtime. The evidence is worth more than the minutes.

2. **The registry carries the feature assertions**, like every other
   assertion, rather than a test body growing a branch per feature.

3. **Detection is part of the claim.** A feature whose commands work while
   its `detect:` block never fires is as broken as one whose commands do not
   exist — it simply never runs. Every feature a cell names must appear as
   detected in that run.

4. **An `appears` path must be absent before the sync**, and the install
   recipe is what makes it absent. This is the same device `mxlaravel`
   already uses for its deliberately unapplied migration. Stated as a
   decision because forgetting it is silent: the assertion still passes, and
   proves nothing.

5. **Five features now; three later.** `horizon` needs Redis,
   `symfony-search` needs Elasticsearch, `typo3-solr` needs Solr. Each is a
   new service in the stack, and the mechanism this spec introduces has not
   yet been shown to work on a single feature. They get their own spec once
   it has.

6. **A feature with no observable effect is a finding, not a failure to
   work around.** `filament:upgrade` and `filament:optimize` are both
   `allow_failure`, and whether either leaves anything behind has not been
   measured. If one does not, the row says so — the wordpress row already
   does exactly this for `wp cache flush`, which has no effect without a
   persistent object-cache drop-in. No assertion will be invented to make a
   feature look proven.

## Design

### The registry gains one dataclass and one field

```python
@dataclass(frozen=True)
class FeatureCheck:
    """What a cell proves about one feature.

    `feature` is the name `features/<name>.yaml` declares, and naming it is
    itself an assertion: the run must report that feature as detected. A
    check with nothing else set therefore still proves something, which is
    exactly `inertia`'s case - it declares no commands at all.

    `appears` are paths that must exist after the sync and must have been
    removed by the install recipe before it (see decision 4).
    `disappears` are paths that must be gone after it. `row_marker` reuses
    `FlushMarker`: a row the cell plants which the sync must have removed.
    """

    feature: str
    appears: tuple[str, ...] = ()
    disappears: tuple[str, ...] = ()
    row_marker: FlushMarker | None = None
```

and on `Cell`:

```python
    features: tuple[FeatureCheck, ...] = ()
```

Defaulted, so the six existing cells are unchanged and keep asserting exactly
what they assert today.

### `mxlaravelfeat` — four features on one Laravel

`profile: laravel`, `php_image: php:8.3-cli`, same stand-in remote and
credentials as `mxlaravel`. Its `install.sh` builds the same
`composer create-project laravel/laravel`, then adds
`filament/filament`, `livewire/livewire` and `inertiajs/inertia-laravel`, and
then removes the paths the sync must recreate.

| Feature | Check | Why that is the evidence |
|---|---|---|
| `livewire` | `appears: public/vendor/livewire/` | `livewire:publish --assets` publishes the asset directory; the recipe deletes it first |
| `filament` | `appears: bootstrap/cache/filament/panels/`, `bootstrap/cache/blade-icons.php` | Measured (see below). `filament:optimize` prints two steps, "Caching components" and "Caching Blade icons", and those are the two paths that appear |
| `node-build` | `appears: public/build/` | Laravel's `package.json` really ships `"build": "vite build"` (checked against `laravel/laravel` 12.x), so `detect` fires; Vite writes into `public/build/`. The **directory** is asserted, not a manifest path, so the claim does not depend on where inside it the manifest lands. One precondition, below, is not optional |
| `inertia` | detection only | The feature declares no commands — "Detected for reporting; the build itself is node-build's job" |

`node` and `npm` join the web image, beside composer and wp-cli, which are
already there for the same reason: the engine runs the command and the image
has to carry it.

**Measured, 2026-10-08**, in the matrix web image built from `php:8.3-cli`,
with `filament/filament` v5.10.1 and `livewire/livewire` v4.4.7 on a
`laravel/laravel` skeleton:

- `filament:optimize` prints `Caching components` and `Caching Blade icons`,
  and exactly two paths appear: `bootstrap/cache/filament/panels/admin.php`
  and `bootstrap/cache/blade-icons.php`. Those are the feature's evidence.
  Note what had to be true first: the probe ran `filament:install --panels`,
  as a real project would have, because without a panel there is nothing to
  cache. The cell's recipe does the same, then deletes the caches.
- `livewire:publish --assets` copies `vendor/livewire/livewire/dist` into
  `public/vendor/livewire/` - eleven files, `livewire.js` and `manifest.json`
  among them. (This entry said *thirteen* until the `mxfeatlaravel` cell ran:
  that run published eleven at the same v4.4.7, and `dist` itself holds
  eleven. The earlier probe's count was wrong, and the cell is what caught
  it - which is the point of the cell.)
- **`filament:upgrade` is not settled by this measurement.** It printed
  "Successfully upgraded!" and no new path appeared - but its likely outputs
  are assets published under `public/`, which `filament:install` had already
  created and the probe did not remove, so their absence from the diff says
  nothing. The cell asserts `filament:optimize`'s two files only, and the
  `VERIFIED.md` row records `filament:upgrade` as **exercised, effect not
  observed**, in the same words the wordpress row uses for `wp cache flush`.
  An implementer who removes what `filament:assets` publishes and finds an
  attributable effect may add it; nobody should invent one.

One thing the failed first attempt is worth recording: the same probe in a
bare `php:8.3-cli` could not install Filament at all - it requires
`ext-intl`, which that image lacks. The matrix web image already builds
`intl`, `zip` and `gd` for TYPO3's sake, so the cell gets this for free. A
feature cell on a leaner image would not.

**`node-build` needs a lockfile before it can run at all.**
`features/package_manager.py::detect_package_manager` keys on the lockfile
and nothing else - `package-lock.json`, `yarn.lock` or `pnpm-lock.yaml` - and
`require_package_manager` raises when none is there. A
`composer create-project laravel/laravel` produces `package.json` and no
lockfile: checked against `laravel/laravel` 12.x, where all three lockfile
paths are 404, and `composer create-project` does not run a package manager.
So the recipe runs `npm install` once, which both creates
`package-lock.json` (making `${PM_INSTALL}` resolve to `npm ci`, which itself
refuses to run without one) and populates `node_modules`. That first install
also writes `public/build/` if it builds - so the recipe deletes that
directory afterwards, which is the same precondition decision 4 describes,
and the sync's own `${PM_RUN} build` is then the only thing that can bring it
back.

### `mxtypo3feat` — one feature on TYPO3 13

`profile: typo3@13`, `php_image: php:8.3-cli`, built like `mxtypo313`.
`typo3-processedfiles` contributes both a sanitize rule and a setup command,
so both get evidence:

| Check | Why that is the evidence |
|---|---|
| `row_marker` on `sys_file_processedfile` | The rule is `TRUNCATE TABLE sys_file_processedfile` with **no** `tolerate_missing_table`, and that is correct: the table is defined in `typo3/cms-core`'s own `ext_tables.sql` (line 120 at v13.4.2, checked), so a core install always has it |
| `disappears: public/fileadmin/_processed_` | The setup command is `rm -rf ${DDEV_DOCROOT}/fileadmin/_processed_`; the recipe creates that directory with a file in it, and the sync must have removed it |

The second one is worth more than it looks: `${DDEV_DOCROOT}` reaches a
`run:` string through `operations/placeholders.py::_validated_docroot`, which
exists because a docroot of `../../etc` would otherwise turn this very
command into `rm -rf ../../etc/fileadmin/_processed_`. This is the only
shipped feature that interpolates a placeholder into an `rm -rf`, and no run
has ever exercised it.

### What the rows will say

Each cell emits its `VERIFIED.md` row through
`tools/apply_verified_rows.py`, as the profile cells do. The feature table's
five rows then carry the framework and versions the run resolved, and
separate — as the profile table already does — what has effect evidence from
what was only exercised through a tolerance.

## Testing

The cells *are* the test. Two things sit beside them:

- **A registry guard that costs nothing.** Every `FeatureCheck.feature` must
  name a file that exists in `features/`. A typo there would otherwise cost a
  three-minute cell run to discover, or worse, silently assert about a
  feature that does not exist. The matrix marker is applied by
  `pytestmark = pytest.mark.matrix` in `test_matrix.py` alone, and
  `tests/matrix/conftest.py` has no autouse fixtures, so an unmarked test
  file in that directory runs in the ordinary unit suite without starting the
  stack. That is where this guard goes. Coverage does not force it:
  `--cov=ddev_sync` measures the package, not the tests.
- **No engine code changes in this spec.** If a cell finds a defect in a
  feature definition, fixing it is a change to `features/*.yaml`, and the
  cell that found it is the regression test. That is how the typo3@13 cell's
  three findings were handled.

And the rule the rows live by: each new cell must be run before its row is
written, and the row must not claim more than that run showed.

## Out of scope

- `horizon`, `symfony-search` and `typo3-solr`, and the three services they
  need (decision 5).
- `typo3-solr`'s doubtful command. Its note stands until that spec: the
  feature may need correcting or removing, and this spec does not touch it.
- Any change to the six existing cells or their rows.
- A feature cell for a framework the matrix does not already install.
