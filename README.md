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

**Published as a release candidate.** `v0.1.0-rc.1` is the first published
version: the engine, the profiles, the container image and the add-on
packaging all exist and are installable, and what has *not* happened yet is
written down rather than implied. No real sync against a real remote host has
been run through the add-on, and `profiles/VERIFIED.md` names every profile
variant whose commands no run has touched. Installing from the published
release has not itself been exercised yet either, only the image it installs.

```bash
cd /your/project
ddev add-on get NoiXdev/ddev-sync
```

No `.ddev/.env` lines are needed: the add-on names the image it shipped with.

To work on the add-on itself, install from a checkout and point it at a local
image instead:

```bash
docker build -t ddev-sync:dev /path/to/ddev-sync
cd /your/project
ddev add-on get /path/to/ddev-sync
printf 'DDEV_SYNC_IMAGE=ddev-sync\nDDEV_SYNC_VERSION=dev\n' >> .ddev/.env
```

**The order matters.** A pre-start hook runs on every project start and reads
the image, so the image has to exist and `.ddev/.env` has to name it *before*
the first `ddev restart`. Get it wrong and the whole project refuses to start,
not just the sidecar.

Then load your key, restart, and let `init` ask for the target:

```bash
ddev auth ssh
ddev restart
ddev sync init
```

`init` asks for a name, where the remote is and which stage it is, writes that
one target into `.ddev/ddev-sync.yaml` - the commented template the install
put there - and finishes by running `doctor` against it. It only ever writes
that file, and it reads the answers from a terminal, so there are no flags to
supply them with: to set a target up without one, edit the template by hand
and run `ddev sync --target <name> doctor` yourself.

**The order matters.** A pre-start hook runs on every project start and reads
the image, so the image has to exist and `.ddev/.env` has to name it *before*
the first `ddev restart`. Get it wrong and the whole project refuses to start,
not just the sidecar. The add-on also needs DDEV's ssh agent: if
`ddev-ssh-agent` is listed in `omit_containers` anywhere, no project with the
add-on installed will start, and the hook says what to remove.

`doctor` is the proof that installation worked, and what to look for is the
`web container` and `ssh agent` rows being `OK`. Without a reachable remote its
`ssh connection` row will fail, and that is expected: it says nothing about the
installation. An unknown host is trusted the first time it is seen, without
asking, and remembered after that; a host whose key has *changed* is refused,
and `doctor` says so on the `ssh connection` row. The exception is a jump host:
its key is not covered, so a target behind one fails on the bastion's key until
ssh has been told about it. See [SECURITY.md](SECURITY.md) for why and for the
workaround.

The user documentation (`NoiXdev/noix-docs`, in its ddev-sync section) walks
through installation, the first sync and removal.

The add-on declares **DDEV v1.25.0** as its floor, which is what it has been
exercised against: only macOS with Docker Desktop and DDEV v1.25.4. Linux,
Colima, OrbStack and rootless Docker have not been tried, and no real sync
against a real remote host has yet been run through the add-on. The CI workflow
that tests the add-on has not run yet either.

The published image is built for both `linux/amd64` and `linux/arm64`. On arm64
it is only checked that the image starts and reports itself correctly; no sync
has been run on arm64 at all.

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
Horizon, Filament, Livewire, Inertia, node builds, Symfony search
reindexing and TYPO3's processed-files handling.

A `generic` fallback covers DDEV project types with no dedicated profile; it
syncs the database and files but leaves the framework-specific work alone,
and it needs credentials of its own because there is no convention to read
them from. They are secrets, so they belong in `.ddev/ddev-sync/local.yaml`
rather than the project file — inside the directory the add-on's shipped
`.gitignore` already covers, so there is nothing to check yourself. A project
that still keeps them in the old `.ddev/ddev-sync.local.yaml` continues to
work — that path is still read — but should move them to the new one; that
file sits one level up, outside the directory the `.gitignore` covers.

The local database calls speak MariaDB's client protocol. The test suites
exercise them end to end against a MariaDB 11 server, and they have been
measured to connect against DDEV's MariaDB 10.8, 10.11 and 11.8 images. A DDEV
project on MySQL 8.0 is not supported: its accounts use an authentication
plugin this image's client cannot load, so the connection is refused whatever
TLS does. MySQL 5.x connects but is not otherwise covered here, and PostgreSQL
is not addressed at all. Nothing here reads DDEV's own `database.type` from
`.ddev/config.yaml`, so an unsupported project fails at the first local call
rather than up front.

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

   1  preflight     remote  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 deploy@web01.example   [built-in]
   2  db.dump       remote  mysqldump --single-transaction $TYPO3_DB_NAME   [profile:typo3]
   3  db.download   -       streamed as part of db.dump into .ddev/.downloads/dump.sql   [built-in]
   4  db.import     local   mysql -h ddev-acme-db db < .ddev/.downloads/dump.sql   [built-in]
   5  files.sync    rsync   rsync -a --info=progress2 -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 deploy@web01.example:/srv/app/public/fileadmin/ /var/www/html/public/fileadmin/   [profile:typo3]
   6  files.sync    rsync   rsync -a --info=progress2 -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 deploy@web01.example:/srv/app/public/uploads/ /var/www/html/public/uploads/   [profile:typo3]
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
