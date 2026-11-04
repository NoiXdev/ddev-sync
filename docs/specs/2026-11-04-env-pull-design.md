# Pull listed environment values from the remote

## Intent

A sync brings the database and the files, but not the settings the application
needs to run them. `env` only patches the local file with the profile's values
(`operations/env.py`), and nothing from the remote environment reaches the
developer's machine. The remote file is read for the five database credentials
alone, on `provider: server` and `provider: docker` alike.

So a developer with a fresh checkout gets a current production database and an
application that stops with "No application encryption key has been specified".
In the words of the person who asked: when the configuration is missing, the
sync is of no use.

The goal is that a project names the values it needs, and every sync brings
exactly those, from either provider. Whoever deploys a Docker stack should be
able to allow this once, rather than editing the stack each time a project needs
one more value.

## Goals

- `env.pull` in the project configuration lists variable names. The `env` step
  copies their remote values into the local environment file.
- It behaves the same under `provider: server` and `provider: docker`.
- `DDEV_SYNC_EXPORT_ENV: all` on the endpoint hands over its whole environment,
  so the stack needs no upkeep. A list keeps working unchanged.
- A value that cannot be pulled stops the run in preflight, before the database
  is touched.
- A pulled value reads back in the application exactly as it does on the remote.
  The one exception is a reference to another variable, under decision 18.

## Non-goals

- Copying the whole remote environment file. The sync only copies names the
  project lists.
- Pulling without the `db` group. `env` rides with `db` (`engine/steps.py`) and
  stays there.
- A `ddev sync doctor` row. Doctor does not know which steps will be selected,
  the same reason it does not check `files.paths`. *(Claude's call.)*
- Creating a missing local file for a project without `env.pull`. That behaviour
  stays as it is, and the existing refusal stays. *(Claude's call.)*
- Checking that the local file is ignored by git. The sidecar may have no git,
  so this is a caution in the docs. *(Claude's call.)*
- Secrets in Docker secret files or `*_FILE` variables, as in the handover design.

## Anti-goals

- A local application that reaches production because a value came along
  unasked. This is why there is a list and no "pull everything" mode: S3 keys,
  payment keys and relays would otherwise point a laptop at the real services.
- A profile safeguard that a pull can undo. `DB_HOST` must not be able to point
  at production because someone listed it.
- A project configuration deciding alone what leaves a stack. The sync does not
  ask the endpoint for names; what the session can read is the deployer's
  decision, made in the stack.
- A secret in a log, a dry run or the confirmation diff.
- A value changed on the way, such as a `$` from the handover that gets
  interpolated, a `#` read as a comment, or stripped quotes.

## Constraints

- Code, comments and specs are in English; tests are pytest with 100 % coverage;
  commit only on request.
- `env.pull` names are not secrets, so they may be committed in
  `.ddev/ddev-sync.yaml`. Lists extend across layers, as all lists do.
- The endpoint image and the add-on are versioned apart per stack and per
  project, so an older engine must keep reading a newer endpoint's file.
- `_parse_env` and how a mounted `.env` is found stay unchanged.

## Decisions

### Agreed with the user

1. **A list in the project.** `env.pull` names what comes along. Everything else
   stays local.
2. **One allowance in the stack.** `DDEV_SYNC_EXPORT_ENV: all` replaces the list
   for a deployer who wants no upkeep. The sync does not pass names to the
   endpoint.
3. **The profile wins.** A name also in `env.set` keeps the `env.set` value.
4. **A missing local file is created from `.env.example`** when `env.pull` is set.
5. **The diff is masked and confirmed**, as the existing step confirms.
6. **`DDEV_SYNC_*` is never handed over**, also under `all`.
7. **Multi-line values are skipped under `all`** rather than stopping the
   endpoint, and the sync names one if it is pulled.

### Claude's calls

8. **`env.pull` shape.** A list of names, each matching
   `[A-Za-z_][A-Za-z0-9_]*`, not starting with `DDEV_SYNC_`. Anything else is a
   `ConfigError` at plan time, so a dry run shows it. The same goes for the names
   that say where the database is, whatever the profile: the five that
   `database.env_map` resolves to, or `DATABASE_*` where it sets none, plus `DB_URL`
   and `DATABASE_URL`. Decision 3 alone would not protect these on a profile whose
   `env.set` does not name them, such as `generic`.
9. **Order of precedence.** `env.set`, then `env.pull`, then `env.default`. A
   pulled value counts as declared, so a `default` does not replace it. A name
   dropped because `env.set` has it is reported in one notice line, naming it.
10. **Strategy.** `env.pull` needs `patch_keys`. With `append_block` or `none` it
    is a `ConfigError` at plan time. A profile with `none` opts in by setting
    `env.strategy: patch_keys`. The `_env` planner must plan the step when only
    `env.pull` has content.
11. **Source file.** The same one the credentials come from: `remote_env_paths`,
    first match, whichever `database.remote_credentials` says. The read is
    memoised on `context.cache` so one run makes one round trip for it, and stays
    `sensitive=True`. The sources are not merged: a mounted `.env` hides the
    handover entirely, as it does for credentials, so the "not on the remote"
    refusal names the file it read. Under Docker, when that file is not the
    handover, it says the mounted file wins and the mount has to go or hold the name.
12. **Values are not expanded.** `expand_placeholders` must not touch a pulled
    value: `${...}` in a production password is the password.
13. **An unset and an empty value differ.** A name absent from the remote refuses.
    A name set but empty is written empty, as in the handover.
14. **Preflight, gated on `db_selected` and a non-empty list.** It reads the
    remote file and refuses, listing every problem at once:
    - names not on the remote; under Docker the message says to add them to
      `DDEV_SYNC_EXPORT_ENV` or set it to `all`;
    - names whose value has a line break: listed in `DDEV_SYNC_SKIPPED` when the
      handover was read, an unclosed opening quote when a `.env` was;
    - no local file among the `env.file` candidates and no `.env.example`.

    Each gets a docs slug: `env-pull-variable-missing`,
    `env-pull-value-has-a-line-break`, `env-pull-no-example-file`.
15. **Creating the file.** `.env.example` is copied to the first `env.file`
    candidate, in the `env` step, after confirmation. The diff shows it as new.
    Its keys count as declared, so `env.default` behaves as for any existing file.
16. **Masking.** In the printed diff, every line assigning a pulled name shows
    `NAME=***`, on both sides. Imposed values print as they do today. If a pulled
    value did not change, it produces no diff line, and a run with nothing to
    change does not prompt. *(Comparing must use the real values, not the masked
    ones.)*
17. **Dry run and plan.** The env step's detail names the pulled keys, never their
    values.

### How a value is written

18. **From a `.env` file: the raw text.** The right-hand side of the remote
    assignment is copied as written, quotes and all. Decoding and re-encoding it
    would change what phpdotenv reads. The last assignment of a name wins, and
    `export NAME=` counts as assigning `NAME`. A value whose opening quote does not
    close on its line is a multi-line value and refuses under decision 14. A value
    that refers to another variable (`$` in an unquoted or double-quoted value) is
    copied too, since `VITE_APP_NAME="${APP_NAME}"` is ordinary in a Laravel `.env`,
    and resolves against the local file. One notice line names it, because it can read
    differently from the remote if the other variable is not pulled.
19. **From the handover: encoded.** The value is verbatim, so it is written bare
    when it holds only `[A-Za-z0-9_./:@+=,-]`, otherwise in single quotes as it is,
    or, when it holds a `'`, in double quotes with `\`, `"` and `$` escaped by a
    backslash. Single quotes alone are not enough because phpdotenv has no escape
    inside them: it takes everything literally up to the first `'`, so no single-quoted
    form can hold one. An empty value writes `NAME=`.
20. **A new write path.** `patch_keys` and `_requote` wrap the replacement in the
    old line's quotes, which is wrong for an already quoted or encoded value. A
    pulled value must replace the whole right-hand side, on every line assigning it,
    or be appended if the name is absent. Imposed values keep `_requote`.

### The endpoint

21. **`all` is the whole value.** After trimming, `DDEV_SYNC_EXPORT_ENV` equal to
    `all` means everything. Any other value is a list as today, and the existing
    refusal for `=` still comes first. A variable that happens to be called `all`
    can still be listed beside another name.
22. **What `all` writes.** Every entry of `/proc/$$/environ` whose name passes the
    existing name rule and does not start with `DDEV_SYNC_`. It is read from there
    for the same reason as today: the script's own variables must not shadow it,
    and each entry gets its own command so a large environment does not split.
    A name that fails the rule is skipped silently.
23. **Skipped values are named in the file.** A value with a line break is left
    out under `all`, and its name goes into one line,
    `DDEV_SYNC_SKIPPED=NAME,NAME`, written only when there is one. It has the
    `NAME=value` shape on purpose: `parse_handover` from rc.5 reads it as an
    ordinary variable and ignores it, so the format stays v1. A list still refuses
    a multi-line value at start, as today.
24. **The log** says `handing over N variables to the sync session`, and adds
    `, skipped M with a line break` when M is not zero. No names.
25. **An older endpoint** treats `all` as a variable name and refuses to start with
    "all is not set", which is loud enough. The docs state the first release that
    understands it.

## What changes in what we already wrote

- `docs/specs/2026-10-29-endpoint-env-handover-design.md`: the non-goal "using the
  handover for anything other than the database credentials" and the anti-goal
  "writing the whole container environment" no longer hold as written. Add a note
  pointing here. The reason still stands for the laptop: only listed names arrive
  there. It no longer stands for the session, which can read all of it under `all`.
- `endpoint/docker-compose.ddev-sync-endpoint.yaml`: comment on `all`.
- `noix-docs`: `env.pull` in the configuration reference and the targets or env
  guide; `all` in the Docker endpoint guide; three troubleshooting entries; a
  caution that pulled values land in a local file that must be git-ignored; the
  exposure of `all` on the security page. Merged only after the release that
  carries this, with the example tags bumped then.

## Risks accepted

- Under `all`, everyone with a key in the endpoint can read every value given to
  the service, not only what `env.pull` names. They could already change the
  production database, so this adds reach to other secrets, for instance Redis.
  The deployer chooses it, in the stack, in one visible word.
- `.env` holds production values on a laptop. That follows from the feature; the
  list keeps it to what the project named.

## Left to the builder

- Where the remote read and the value encoding live, and how the raw text is carried
  from reader to writer.
- The exact message text, within the rules above.
- How the matrix proves it. `mxdocker` and one `provider: server` cell should each
  pull at least one tricky value and read it back through the framework's own
  loader, since the local file's meaning is defined by phpdotenv and not by us.
