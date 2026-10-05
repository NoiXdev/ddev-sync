<img src="assets/logo-256.png" alt="" width="96" align="right">

# ddev-sync

Pull a project's production database, files and setup commands into a local
DDEV environment — and leave the production data behind while doing it.

One command replaces the sequence every developer otherwise repeats by hand:
dump the remote database over ssh, import it, rsync the uploads, rewrite the
local environment, anonymise what came across, and run the framework's own
post-import commands. What those commands are is decided by a **profile** per
project type, so the tool knows that TYPO3 wants `vendor/bin/typo3
cache:flush` where Laravel wants `php artisan optimize:clear`.

## Status

**Installable as a DDEV add-on from a checkout; there is no published release
yet.** The engine, the profiles, the container image and the add-on packaging
(the manifest, the sidecar service and the `ddev sync` command) all exist.
What does not exist is a published image or a tagged release, so
`ddev add-on get noixdev/ddev-sync` does not work yet: you install from a clone
and build the image yourself.

```bash
git clone https://github.com/NoiXdev/ddev-sync /path/to/ddev-sync
docker build -t ddev-sync:dev /path/to/ddev-sync

cd /your/project
ddev add-on get /path/to/ddev-sync
printf 'DDEV_SYNC_IMAGE=ddev-sync\nDDEV_SYNC_VERSION=dev\n' >> .ddev/.env
ddev auth ssh
ddev restart
ddev sync doctor
```

**The order matters.** A pre-start hook runs on every project start and reads
the image, so the image has to exist and `.ddev/.env` has to name it *before*
the first `ddev restart`. Get it wrong and the whole project refuses to start,
not just the sidecar. The add-on also needs DDEV's ssh agent: if
`ddev-ssh-agent` is listed in `omit_containers` anywhere, no project with the
add-on installed will start, and the hook says what to remove.

Installation, the first sync and removal are walked through in the
documentation at <https://docs.noix.dev/ddev-sync/getting-started/installation/>.

Only macOS with Docker Desktop and DDEV v1.25.4 has been exercised. Linux,
Colima, OrbStack, rootless Docker and DDEV 1.24.x have not, and no real sync
against a real remote host has yet been run through the add-on. The CI workflow
that tests the add-on has not run yet either.

## What it syncs

| Step | What happens |
|---|---|
| `db` | `mysqldump` over ssh, streamed, imported into the DDEV database, and the local `.env` pointed at it |
| `files` | `rsync` of the paths the profile declares — uploads, `fileadmin`, `storage/app/public` |
| `s3` | `rclone` between two S3 endpoints, for projects that keep media in a bucket |
| `sanitize` | Truncates sessions and queues, anonymises user tables, resets passwords, creates a local admin |
| `setup` | The framework's own commands: `composer install`, migrations, cache rebuilds |

Nothing is pushed, ever. The remote is only ever read.

## Supported project types

Profiles ship for **TYPO3** (9 through 13), **Laravel**, **Symfony**,
**WordPress**, **Drupal** and **Shopware 6**, plus optional features for
Horizon, Filament, Livewire, Inertia, node builds and TYPO3's Solr and
processed-files handling.

A `generic` fallback covers DDEV project types with no dedicated profile; it
syncs the database and files but leaves the framework-specific work alone,
and it needs credentials in `.ddev/ddev-sync.yaml` because there is no
convention to read them from.

Which of them have actually been run against a real installation — rather
than only reasoned about — is recorded per variant in
[`profiles/VERIFIED.md`](profiles/VERIFIED.md). Read that file before trusting
a profile with a production database. It also records, per rule, what has no
run-time evidence.

## What a run looks like

```
ddev sync --target=production --dry-run
```

```
  Plan for target 'production'        typo3@12
  acme  -  deploy@web01.example:22  /srv/app/
  Nothing will be executed.

   1  preflight     remote  ssh -p 22 deploy@web01.example   [built-in]
   2  db.dump       remote  mysqldump --single-transaction $TYPO3_DB_NAME   [profile:typo3]
   3  db.download   -       streamed as part of db.dump into .ddev/.downloads/dump.sql   [built-in]
   4  db.import     local   mysql -h ddev-acme-db db < .ddev/.downloads/dump.sql   [built-in]
   5  files.sync    rsync   rsync -a --info=progress2 -e ssh -p 22 deploy@web01.example:/srv/app/public/fileadmin/ /var/www/html/public/fileadmin/   [profile:typo3]
   6  files.sync    rsync   rsync -a --info=progress2 -e ssh -p 22 deploy@web01.example:/srv/app/public/uploads/ /var/www/html/public/uploads/   [profile:typo3]
   7  sanitize      sql     10 rules: clear-be-sessions, clear-scheduler-tasks, clear-fe-sessions, clear-sys-log, anonymize-fe-users, reset-fe-passwords, anonymize-be-users, reset-be-passwords, local-admin, flush-cache   [profile:typo3]
   8  setup         web     composer install --no-interaction   [profile:typo3]
   9  setup         web     vendor/bin/typo3 extension:setup   [profile:typo3]
  10  setup         web     vendor/bin/typo3 cache:flush   [profile:typo3]

  Sources:  [built-in]   [profile:typo3]
```

That is not an illustration: it is `tests/cli/golden/typo3-12-full.txt`, a
snapshot the test suite checks, so a change to a profile shows up as a diff
there rather than as a surprise on someone's machine. Every line names the
layer it came from, and `--dry-run` walks the same plan object the executor
would — so what you read is what would run.

## Safety

`db.import` **drops and recreates** the local database. The sanitize step
rewrites user tables in place. Both are the point of the tool, and both are
irreversible locally, so `ddev sync doctor` exists to tell you whether a sync
will work before one moves a byte.

Sanitize rules are what keep production data from living on a laptop. They
are per project type, and a project can add its own — but a rule that was
never verified against a real installation can silently do nothing, which is
why `profiles/VERIFIED.md` is part of this repository rather than an
afterthought.

## Development

```bash
uv sync
uv run pytest                 # unit suite; the gate is 100 % line and branch coverage
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict, over src, tests and tools
```

Two further suites exist and are excluded from the default run:

```bash
uv run pytest -m integration            # a real pull against containers
uv run pytest -m matrix --no-cov        # installs real frameworks and syncs against them
```

The matrix is what turns a profile's declared commands from a claim into
evidence. A cell installs the real framework, seeds a stand-in production
database from that installation's own schema, and runs a whole sync against
it with the shipped image. It takes about twenty minutes for six cells.

User-facing documentation lives in `NoiXdev/noix-docs`; this README stays
short on purpose.

## License

[MIT](LICENSE) © noix.dev
