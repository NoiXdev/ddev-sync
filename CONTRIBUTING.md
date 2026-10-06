# Contributing

Thanks for your interest in ddev-sync! This short guide helps you get started.

## Setup

The engine is a Python package managed with [uv](https://docs.astral.sh/uv/). You do
not need DDEV to work on it — the unit suite runs entirely without containers.

```bash
uv sync
uv run ddev-sync --help
```

Docker is needed only for the three container-backed suites described below, and
the add-on suite needs DDEV as well.

## Before a pull request

All four checks must pass:

```bash
uv run pytest                      # unit suite
uv run ruff check .                # lint
uv run ruff format --check .       # formatting
uv run mypy                        # strict, over src, tests and tools
```

`pytest` enforces **100 % line and branch coverage**, and `# pragma: no cover` is
forbidden — `exclude_lines` is deliberately empty, so a pragma cannot be used to
reach the number. New logic comes with tests; a bug fix gets a regression test that
fails before it.

Three further suites are excluded from the default run, because all of them drive
real containers:

```bash
uv run pytest -m integration           # a real pull: ssh, dump, import, rsync, sanitize
uv run pytest -m matrix --no-cov       # installs real frameworks and syncs against them
bats tests/test.bats --filter-tags '!release'   # installs the DDEV add-on and uses it
```

The `--no-cov` on the matrix matters: without it the coverage gate applies to a
matrix-only run and reports a failure that has nothing to do with your change.

The first two are selected by their pytest markers. The last one is a
[bats](https://bats-core.readthedocs.io/) suite and needs bats 1.8 or newer, DDEV
and Docker: it creates a throwaway DDEV project, installs the add-on into it from
your checkout, runs `ddev sync` there and removes the add-on again. It takes several
minutes, because it builds the container image and starts a project. `--filter-tags
'!release'` leaves out the one test that installs from a published release, which
cannot pass until there is one.

**Run it for any change under the add-on packaging** — `install.yaml`,
`docker-compose.ddev-sync.yaml`, `config.ddev-sync.yaml`, `ddev-sync/` or
`commands/` — because it is the only test in this repository that exercises
installation. The Python suites cannot see a manifest that installs nothing, a
command DDEV runs in the wrong service or a removal that leaves files behind. It
asserts the manifest's file list in both directions, so adding or removing an
installed file means updating `tests/test.bats` in the same change. Note that bats
runs under the bash 3.2 that macOS ships, where `errexit` does not apply to a
failing `[[ ]]` unless it is the last command of the test: every `[[ ]]` assertion
in that suite ends in `|| false`, and a new one has to as well, or it is a silent
no-op there.

## Changing a profile

A profile declares what a framework's commands are. Every one of those is a claim,
and claims in this repository are verified rather than assumed — two commands that
looked entirely plausible turned out not to exist in TYPO3 core at all, and nothing
in the suite could see it, because the tests stubbed the binaries.

So, for a profile change:

1. **Verify each command against the framework's own CLI.** Run its `list`/`help` and
   paste the output into the pull request. Not from memory, not from another
   project's configuration, not from an AI assistant — from the tool that will run it.
2. **Make the matrix prove it.** Each supported variant has a cell under
   `tests/matrix/cells/`, which installs the real framework, seeds a stand-in
   production database from that installation's own schema and runs a whole sync
   against it. Run the cell for the variant you touched:
   `uv run pytest -m matrix -k <cell> --no-cov`.
3. **Assert effects, not exit codes, for anything tolerated.** A command marked
   `allow_failure` is reported as `OK` even when it fails, so an exit-code assertion
   proves nothing about it. The existing cells leave a migration unapplied, drop a
   column or plant a cache row, then assert the thing came back.
4. **Update [`profiles/VERIFIED.md`](profiles/VERIFIED.md) honestly.** A row must not
   claim more than its cell proved. Where a rule was only exercised through its
   tolerance, or a command has no observable effect, the row says so. An unverified
   row claiming verification is worse than an empty one.

Adding a variant is a registry entry in `tests/matrix/cells.py` plus three recipe
files — install, seed and the project configuration.

## Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`).
- **History is linear.** Rebase on `main`; this repository has no merge commits.
- **English** for code, comments, commit messages and documentation.
- **Comments explain why, not what**, in full sentences. A comment that records a
  verified fact — a command name, an exit code, a message a CLI prints — has to be
  exactly right, because the next person will trust it instead of re-checking.
- **Only `src/ddev_sync/transport/runner.py` may import `subprocess`.** Everything
  else builds an argument vector and hands it to a `CommandRunner`, which is what
  lets the whole engine be tested without a network. A test parses every module and
  fails otherwise.

## A note on destructive operations

`db.import` drops and recreates the local database, and the sanitize step rewrites
user tables in place. When you change either, assume someone will run your code
against a production target by accident on their first try, and make the failure
loud and early rather than halfway through.
