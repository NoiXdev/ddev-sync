# Docker endpoint: credentials from the container environment

## Intent

A production stack deployed through Portainer (or any compose setup) usually
hands the application its database credentials as runtime environment
variables: Portainer stack variables, a compose `environment:` block, or an
`env_file:` that compose reads at deploy time. There is no `.env` file on disk
that the endpoint could mount.

The engine reads remote credentials only from a file
(`operations/database.py::_from_remote_env`), and an SSH session in the
endpoint does not inherit the container's environment, because sshd starts each
session with a clean one. So today such a stack can only be synced with
`database.remote_credentials: explicit`. That puts production passwords into
every developer's `.ddev/ddev-sync/local.yaml`, which is exactly what the
endpoint exists to avoid.

The goal is that whoever deploys the stack gives the `ddev-sync` service the
same variables the application gets, and the sync works with no new setting in
the project. In short: runtime environment in, env file out.

## Goals

- The endpoint takes the variables the deployer lists from its own container
  environment and writes them at start into a file that the engine reads like
  a mounted `.env`.
- Under `provider: docker` the engine finds that file by itself. The project
  needs no new key.
- Passwords with any printable character, including `'`, `"`, `#`, `=`, `$`,
  spaces and a leading or trailing space, arrive byte for byte.
- A mistake shows up when the stack is deployed, not when a developer syncs.

## Non-goals

- Reading the application container's environment from the endpoint. That
  needs the Docker socket, and the endpoint has none on purpose.
- Docker secrets (`/run/secrets/*`) and `*_FILE` variables. The deployer can
  already mount such a file. Possible later.
- Anything on `provider: server`. A server keeps its `.env` on disk.
- Using the handover for anything other than what the engine already reads
  from the remote env file, which is the database credentials.

## Anti-goals

- Writing the whole container environment. `DDEV_SYNC_AUTHORIZED_KEYS` and
  unrelated secrets of the stack must not end up in a file that a sync reads
  and copies to a laptop.
- A file that other accounts in the container can read, or that outlives the
  container in a volume.
- A value that is silently changed, such as stripped quotes, trimmed spaces or
  a cut `#` comment, so that the sync fails later with "access denied" and no
  hint why.
- Changing how an existing mounted `.env` is found or parsed.

## Decisions (agreed with the user)

1. The deployer gives the `ddev-sync` service the same variables as the
   application, typically from the same Portainer stack variables or the same
   `env_file:`.
2. Only the variables the deployer explicitly lists are written, never the
   whole environment.
3. A listed variable that is not set makes the endpoint refuse to start and
   name the variable.
4. A mounted `.env` keeps priority. Setups that work today stay unchanged.
5. The engine reads the file like a mounted `.env`, and the project's
   `database.env_map` applies as usual.

## Design

### Endpoint

- New variable `DDEV_SYNC_EXPORT_ENV`: the names to hand over, separated by
  whitespace or commas, for example
  `DDEV_SYNC_EXPORT_ENV: "DB_HOST DB_PORT DB_DATABASE DB_USERNAME DB_PASSWORD"`.
  If it is unset or empty, nothing is written and the endpoint behaves exactly
  as today.
- `entrypoint.sh` checks every name. Each must match `[A-Za-z_][A-Za-z0-9_]*`
  and must not start with `DDEV_SYNC_`; otherwise it refuses with a message
  and a `More:` link, like the existing refusals.
- A listed name that is **unset** is refused (decision 3). A name that is set
  but **empty** is written as empty. Empty is a value, and the engine already
  refuses an empty credential with its own message. *(My call.)*
- A value with a newline or carriage return is refused and named. Database
  credentials do not contain one, and a line-based file cannot carry one
  without a quoting scheme. *(My call.)*
- The file is `/run/ddev-sync/app.env`, rewritten at every start, owned by the
  session uid and gid and mode `0400`. `/run` is not a volume in the template,
  so the file does not persist. The entrypoint says how many variables it
  wrote, never their values.

### File format

- The first line is a fixed marker comment, `# ddev-sync endpoint handover v1`,
  followed by one `NAME=value` line per variable. Everything after the first
  `=` up to the end of the line is the value, verbatim: no quotes, no escapes,
  no trimming, no comments.
- The engine reads this file with its own small parser, chosen by the file's
  fixed path, not with `_parse_env`. `_parse_env` trims and strips quotes,
  which would change a password like `"abc"` or one ending in a space, and
  changing `_parse_env` itself would change how existing app `.env` files are
  read (anti-goal). *(My call: this is what keeps "byte for byte" honest.)*

### Engine

- Under `provider: docker`, `/run/ddev-sync/app.env` is appended as the **last**
  candidate after the configured or default names in the data root (decision
  4). It is an absolute path in the endpoint, not joined onto the data root,
  so the probe in `_fetch_first_env_file` must take it as is.
- Everything after reading works as today: `env_map`, the "variable not set"
  error, memoising, secret registration.
- Variable names: the deployer lists the names the application already uses,
  which are the names the framework profile's `database.env_map` maps:
  `DB_*` for Laravel, `DATABASE_*` for Symfony and Shopware, `TYPO3_DB_*` for
  TYPO3. So a framework project needs no new setting. Only a project whose
  names differ from its profile sets `database.env_map`, as it would with a
  mounted `.env` today. The handover does not rename or alias anything.
- Parser choice: the engine uses the handover parser whenever the path it read
  is `/run/ddev-sync/app.env`, wherever that candidate came from, including a
  `database.env_file` that names it. The marker line is required. A file at
  that path without the `v1` marker is a hard error with its own message and
  docs entry, never a fallback to `_parse_env`. *(My call.)*
- The handover only matters for `database.remote_credentials: env`. Under
  `explicit` or `wp_cli` the file is never read, and neither `doctor` nor
  `init` warns about a set `DDEV_SYNC_EXPORT_ENV`. *(My call.)*
- `doctor`'s `credentials` row already names the file it read from, so it
  shows the handover file when that is the one used.
- `ddev sync init`: the handover file is **not** a new selectable entry in
  `choose_remote_env_file`. It is already the last candidate, so picking it
  would write nothing useful. Under `provider: docker` it appears in the
  "looked for" list, and the text shown when nothing was found mentions
  `DDEV_SYNC_EXPORT_ENV` and links the new docs section. This is a message
  change only, with no new prompt flow. *(My call.)*
- The "no environment file found" error under `provider: docker` names the
  handover file among the paths it looked at and points to the new docs
  section.

### Docs and template

- `endpoint/docker-compose.ddev-sync-endpoint.yaml`: a commented example of
  `DDEV_SYNC_EXPORT_ENV` with the variables passed in via `environment:` and via
  `env_file:`, next to the existing `.env` mount, which becomes "one of two
  ways".
- noix-docs `guide/docker-endpoint`: a section "Credentials from the stack's
  environment" with a Portainer example (stack variables) and a compose
  example. `reference/configuration` and `reference/troubleshooting` get
  entries for the new refusals (name invalid, variable unset, newline in
  value), plus an update to the existing "variable missing in the remote env
  file" entry. New slugs go into `docs.py` and the anchor check. One
  troubleshooting entry per refusal, each with its own anchor *(my call)*:
  `endpoint-export-env-name-invalid`, `endpoint-export-env-variable-unset`,
  `endpoint-export-env-value-has-a-newline`, and on the engine side
  `endpoint-handover-file-unreadable` for the missing or wrong marker. The
  guide section gets its own anchor `credentials-from-the-stacks-environment`,
  which the "no environment file found" message links under
  `provider: docker`.

### Tests

- Entrypoint: unset list writes nothing; listed and set writes the file with
  the right owner and mode; unset name, bad name, `DDEV_SYNC_` name and newline
  each refuse with their message; a value with `'"#=$` and leading and trailing
  spaces arrives unchanged.
- Engine: the candidate order under docker and under server; the handover
  parser (verbatim values, required marker, empty value); a mounted `.env` wins
  over the handover file; a file at the handover path without the marker is
  refused.
- Matrix: the `mxdocker` cell (or a variant of it) passes its database
  credentials only via `environment:` and `DDEV_SYNC_EXPORT_ENV`, with no
  `.env` mount, and syncs end to end.

## Left to the builder

- Whether the `mxdocker` change is a new cell or a switch in the existing one.
- How the probe script accepts the absolute candidate. Joining in
  `remote_env_candidates` or a separate list are both fine.
- Exact wording of the messages, in the style of the existing refusals.
