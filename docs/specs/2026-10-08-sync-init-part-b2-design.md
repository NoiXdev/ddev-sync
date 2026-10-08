# `ddev sync init`, part B2: proving the credentials — design

**Status:** approved by the owner 2026-10-08, after a design conversation.
Second half of part B; `docs/specs/2026-10-07-sync-init-part-b1-design.md`
is the first.

## Why

Part B of `ddev sync init` was split along the line this codebase already
draws: B1 was everything decidable without a network, B2 is everything that
probes the remote. B1 shipped. This is §8 items 2 and 3 of
`2026-10-05-ddev-sync-findings-from-the-matrix.md`, under the framing the
whole command exists for: **`init` should refuse to leave behind a
configuration that `doctor` would then reject.**

Item 2 asks that `init` prove the credentials source actually yields
credentials, and offer `explicit` or `wp_cli` when the default `env` finds no
file. Item 3 asks that the sanitize-table probe run before a configuration is
handed over.

## What is already true

Measured against the code, not assumed. Three of these change the shape of
the work.

- **`doctor` is blind to credentials for a profile with no required table.**
  `PreflightOperation._check_sanitize_tables` returns `(None, "")` when
  `_required_tables(...)` is empty, and `_probe_sanitize_tables` returns
  early in the same case. `profiles/generic.yaml` has `sanitize.rules: []`
  **and** `database.remote_credentials: explicit`, and nothing writes
  `database.credentials`. So a generic project gets a green `doctor` and then
  dies at `db.dump`, where `_from_config` raises. The local database is
  untouched — the dump is before the import — but `doctor`'s promise, "tell me
  whether a sync will work", is broken there. On `drupal` the row exists and
  is red, because `anonymize-users` declares `requires_table:
  users_field_data`.
- **`env.file` means two different things.**
  `operations/database.py::_env_candidates` and
  `operations/env.py::_find_env_file` read the same key. One joins it onto
  `ResolvedTarget.data_root` to find the credentials file **on the remote**;
  the other matches it against the project root's real directory entries to
  find the file **to rewrite locally**. The deployer layout's directory
  difference is handled cleanly by `data_root`; the *filename* is not. When
  both are `.env`, which is the common case, nothing shows.
- **B1 made that collision reachable through a question.** B1's env check
  writes `env.file` when the local file is named something else. A project
  whose local file is `.env.ddev` now gets `env.file: [.env.ddev]` written,
  and the remote credential lookup follows it to `shared/.env.ddev`, which
  does not exist. Before B1 this took a hand edit; now a wizard offers it.
  B2 is where it gets closed.
- **Nothing ignores `.ddev/ddev-sync.local.yaml`.** The add-on ships a
  `.gitignore` (`install.yaml`'s `project_files` lists `ddev-sync/.gitignore`,
  installed at `.ddev/ddev-sync/.gitignore`), but it covers that directory's
  contents — `/passwd*`, `/home/` — not a sibling file one level up. DDEV's
  own `.ddev/.gitignore` carries `#ddev-generated: Automatically generated`
  and is rewritten on start; its "local" patterns are `/config.local.y*ml`
  and `/config.*.local.y*ml`, which `ddev-sync.local.yaml` does not match.
- **Renaming the file to match DDEV's pattern is a trap.** `.ddev/config.*.yaml`
  is loaded by DDEV itself as additional DDEV configuration, so a file named
  `config.ddev-sync.local.yaml` would be ignored by git for free and then
  parsed by the wrong program. Rejected.
- **`read_remote_credentials` is memoised per run** on `context.cache`
  (`_CREDENTIALS_CACHE_KEY`), and registers the password in `context.secrets`
  so the verbose reporter scrubs it. Any second caller in the same run is free.
- **`_from_config` makes no network call** — the `explicit` source reads
  `database.credentials.*` from the merged configuration. Only `env` and
  `wp_cli` cost an ssh round trip.
- **`database.env_map` already exists** and is remote-specific: it names the
  variable names to read *inside the remote env file*
  (`context.config(f"database.env_map.{key}", ...)`).
- **The preflight step already carries selection state.**
  `engine/plan.py` builds `params={"sanitize_selected": StepKind.SANITIZE in
  steps}` and `PreflightOperation.execute` reads it from its own params,
  because the operation never sees the Plan.
- **The missing-table message does not name the override key it means.**
  `_first_missing_table_problem` says "override it in `.ddev/ddev-sync.yaml`"
  without naming `database.wp_table_prefix`, which is the actionable key when
  the table name came from a `${WP_TABLE_PREFIX}` expansion.
- **`check()` is documented never to raise**, and `_check_sanitize_tables`
  is the pattern for that: guard on connectivity, catch `OperationError`,
  return a verdict and a detail string.

## Decisions

Each was taken by the owner during the design conversation.

1. **The credentials normally live on the production system** — in the
   application's configuration or its `.env`, which this tool already reads.
   So `init`'s job is to prove the configured source works, and when `env`
   finds nothing the first question is *where the file is on the server*, not
   *what is your password*. `explicit` is the override for the cases where
   nothing else reaches them, not the main path.
2. **The remote path gets its own key, `database.env_file`**, additive, with
   a fallback to `env.file`. It sits beside `database.env_map` because that
   key already describes the same remote file.
3. **The local layer moves to `.ddev/ddev-sync/local.yaml`**, inside the
   directory the add-on already owns and already ignores. The old path is
   still read.
4. **`init` writes the credentials only after probing them.** A password that
   does not work never reaches the disk.
5. **The "exactly one file per run" rule is retired, deliberately.** Parts A
   and B1 both carried it as a constraint, and B1 kept it by putting every
   setting it writes into `.ddev/ddev-sync.yaml`. B2 cannot: the whole point
   of the local layer is that secrets live somewhere the project file is not,
   so a run that writes credentials writes two files. What replaces the rule
   is narrower and still worth stating: **`init` writes the project file and,
   at most, one local layer — never the global file**, which the sidecar
   mounts read-only. Each write keeps its own proof and its own restore.

## Design

### The proof belongs in preflight, not in the wizard

This is the load-bearing change, and it is why B2 is not mostly wizard code.

`PreflightReport` gains `credentials_ok: bool | None` and
`credentials_detail: str`, in the shape `sanitize_tables_ok` already has.
`check()` probes `read_remote_credentials` and reports the verdict;
`render_doctor` renders the row when it is not `None`, between `disk space`
and `sanitize tables` — credentials are a precondition of the table probe, so
they read in that order.

Three things follow without any further work:

- **`doctor` tells the truth for every profile**, including `generic`, closing
  the measured blind spot above.
- **A real sync refuses before the dump** rather than failing at it.
- **`init` inherits the proof**, because part A already ends by calling
  `doctor_command` and returning its exit code.

`init`'s own job shrinks to the one thing only a wizard can do: **offer a
correction when that row is red.** Writing the proof into the wizard instead
would have protected exactly one run per project — the one where somebody
happened to use `init`.

**Cost.** For a profile whose rules require a table, this is free: the table
probe already reads credentials and the result is memoised. For `explicit`
it is free, being a config read. The new cost is one ssh round trip in
`doctor` for an `env` or `wp_cli` profile with no required table. `doctor`
exists to pay exactly that kind of cost.

**Guard.** Like `_check_sanitize_tables`, the probe returns `(None, "")` when
`ssh_ok` or `remote_path_ok` is false. An `explicit` source could technically
be validated without ssh, but a green credentials row under a red ssh row
invites the user to fix the wrong thing; diagnosis is sequential and the ssh
row is first.

**Refusal in `execute()` is conditional on the database being selected.**
`engine/plan.py` passes `db_selected: StepKind.DB_DUMP in steps` alongside the
existing `sanitize_selected`, and `execute` refuses only then. A `files`-only
sync needs no database credentials and must not be stopped for lacking them.

### `database.env_file`, and why the fallback runs this way

`_env_candidates` reads `database.env_file`, then `env.file`, then the
`[".env.local", ".env"]` default. Nothing existing changes.

The fallback to `env.file` is kept deliberately, even though it preserves the
ambiguity: a project that set `env.file` in order to steer the *remote* lookup
would otherwise break, and this spec will not break a working configuration to
tidy a key name.

The collision B1 made reachable closes anyway, because of when it can occur.
It needs the fallback to *fail* — the remote file is not where `env.file`
says. That is precisely the condition that turns the credentials row red,
which fires the question below, which writes `database.env_file`. From then on
the two keys are written by two different questions: `env.file` by B1's local
check, `database.env_file` by B2's remote probe, each naming the file on its
own side.

`profiles/schema.py` learns the key, as it validates the rest of `database`.

### The questions, and their order

They run **after** the target has been written, because
`read_remote_credentials` needs an `ExecutionContext` and that needs a
resolved target. That is also where they belong in meaning: these are
questions about the connection, not about the project, so they sit on the
other side of the write from B1's three.

1. **Probe the configured source.** Green — no question; `init` is done.
2. **`env` found no file.** `init` prints the full remote paths it tried, then
   lists the `.env*` entries that actually exist in `data_root` — one `ls`
   over the connection it has already proved — and offers them. The answer is
   written as `database.env_file`. This mirrors B1's local env question
   deliberately: same shape, same reason, the other side of the wire.
3. **Only if that does not settle it**, offer the override: `explicit`, and
   `wp_cli` as well when the profile is WordPress. The five values
   (`name`, `user`, `password`, `host`, `port`) are asked, **probed against
   the remote**, and written only if the probe passes.

A source that fails for a reason no question can fix — ssh is down, the
remote path is wrong — is not met with a question. Those have their own
`doctor` rows and their own fixes.

### The local layer moves

`.ddev/ddev-sync/local.yaml` becomes the path `init` writes;
`ddev-sync/.gitignore` gains `/local.yaml`. That file carries `#ddev-generated`
and is listed in `install.yaml`'s `project_files`, so the ignore arrives with
every install and update of the add-on, and no file DDEV manages has to be
taken away from it. No path outside `.ddev/` is touched.

`ConfigPaths` carries both: `local_config` (the new path) and
`legacy_local_config` (the old one). `load_user_layers` reads whichever
exists, at the same position in the layer order — the local layer is still
the strongest, whichever file supplies it. **If both exist, that is an error
naming both files** — this codebase already refuses to guess when two layers
define the same target (`cli/init.py`'s check against
`_other_layers_defining`), and silently preferring one copy of a credentials
file over another is the same class of mistake with worse consequences.
When only the old file exists it is read as before, and `doctor` says it
should move.

**`init` writes to whichever file is already in use, and creates only the
new one.** If `.ddev/ddev-sync.local.yaml` exists, that is the file `init`
appends credentials to, and it says the file should move; if it does not,
`init` creates `.ddev/ddev-sync/local.yaml`. Writing to the new path while
the old one exists would produce exactly the both-exist state the rule above
forbids — a command cannot be allowed to create the error it then refuses to
resolve. This is the one place where "the new path is the path" yields to
the invariant.

`README.md` and `ddev-sync/ddev-sync.example.yaml` name the new path. The
`.gitignore` advice they currently give — "make sure your repository ignores
that file - nothing here does that for you" — becomes false for the new path
and must change with it.

### §8 item 3: already delivered, minus one sentence

The sanitize-table probe has run in both `check()` and `execute()` since
part 3, `doctor` renders it, and `init` returns `doctor`'s exit code. A
configuration that fails it is not handed over as a success. **Nothing in B2
needs to add the probe.**

What item 3 asked for beyond that was a correction *offer*. B2 deliberately
does not build one. With B1 having resolved the production URL, the realistic
remaining cause of a missing table is a prefix `wp config get` cannot read
back — Bedrock and other env-based WordPress setups — and a wizard branch for
that population would be machinery without readers.

Instead, one sentence: when `database.remote_credentials` is `wp_cli`,
`_first_missing_table_problem`'s "does not exist" message names
`database.wp_table_prefix` as the override, which is the key that actually
fixes it. Today it says only "override it in `.ddev/ddev-sync.yaml`".

This is a deliberate narrowing of §8 item 3 and is recorded here so that
nobody re-derives the omission as a gap.

## Testing

The credential probe, the question flow and the writer are testable with the
fake runner the suite already uses; the `ls` probe is one more scripted
command. The suite covers:

- The credentials row: green, red, and `None` under a failed ssh — for each of
  the three sources.
- That `doctor` now reports credentials for a `generic` project, which is the
  blind spot this closes.
- That `execute()` refuses on bad credentials when the database is selected
  and does **not** when only files are.
- `database.env_file` taking precedence over `env.file`, and the fallback
  chain when it is absent.
- The remote-file question writing `database.env_file`, and the explicit
  branch writing credentials **only** after a passing probe.
- Both local-layer paths: new only, old only, and both present (an error
  naming both) — and that `init` appends to the old file when it exists
  rather than creating the new one beside it.
- The missing-table message naming `database.wp_table_prefix` for a `wp_cli`
  source and not otherwise.

Project gates apply unchanged: 100 % line and branch coverage,
`# pragma: no cover` forbidden, ruff, ruff format, mypy strict over `src`,
`tests` and `tools`, `subprocess` confined to `transport/runner.py`.
TDD: the failing test first.

## Out of scope

- A non-interactive `init` driven by flags, as in parts A and B1.
- Any correction offer for a missing sanitize table beyond the one sentence
  above.
- Migrating an existing `.ddev/ddev-sync.local.yaml` automatically. `init`
  and `doctor` say it should move; moving someone's credentials file for them
  is not a thing a wizard should do silently.
- Reading credentials from anything other than the three existing sources.
