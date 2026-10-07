# `ddev sync init`, part A: writing a verified target — design

**Status:** approved by the owner 2026-10-07, after a design conversation.
This is the first of two specs; part B is scoped at the end.

## Why

Three places in the engine tell a user with no targets to run
`ddev sync init`, and there is no such command:
`cli/render.py:154`, `cli/context.py:202`, `cli/commands.py:204`. The advice a
new user is given first answers "No such command". That alone would justify
building it.

The shape of it comes from §8 of
`2026-10-05-ddev-sync-findings-from-the-matrix.md`, a list of seven checks
drawn from failures that actually happened during profile verification, under
the framing: **`init` should refuse to leave behind a configuration that
`doctor` would then reject.** Part A delivers items 1 and 7 of that list and
the verification framing; part B delivers items 2 to 6.

## What is already true

Established by reading the code, not assumed:

- **Nothing in the engine writes configuration today.** `operations/env.py`
  rewrites a project's `.env`; that is the only writer. `init` introduces the
  capability, so the mechanism is a design decision rather than an existing
  pattern to follow.
- **The project file already exists, with its comments.** The add-on's
  `post_install_actions` (`install.yaml:71-77`) copies
  `ddev-sync/ddev-sync.example.yaml` to `.ddev/ddev-sync.yaml` when that file
  is absent, stripping the generated-file marker, and prints "define a target
  in it". The copy keeps every explanatory comment.
- **`targets:` ships commented out** (`ddev-sync.example.yaml:24-31`). So a
  first write is an append, with no existing mapping to merge into.
- **`.ddev/ddev-sync.local.yaml` is not shipped.** It appears nowhere in
  `install.yaml`.
- **Every non-empty layer must carry `version`.** `config/loader.py`'s
  `_check_version` rejects a layer without it; an empty document is allowed.
- **The runtime dependencies are pydantic, PyYAML, click, rich and
  questionary.** There is no comment-preserving YAML library, and the image is
  deliberately lean.
- **Profile detection already exists**: `config/detect.py` provides
  `resolve_profile_name`, `resolve_version`, `detect_major_version` and a
  `Resolution` dataclass.
- **Deployer resolution already exists**: `transport/deployer.py`'s
  `make_deployer_resolver(project, runner)` runs `dep config --format=json` at
  most once, and `config/deployer.py::parse_dep_config(payload, alias)` reads
  one host out of it. Listing the hosts is **not** yet possible from outside:
  `_hosts(payload)` is private.
- **The preflight checklist is reusable as it stands**: `doctor_command` runs
  `PreflightOperation().check(execution)` and renders it with
  `render_doctor(report, target, colour=...)`.

## Decisions

Each was taken by the owner during the design conversation.

1. **The user chooses the layer per run** — the global config or the project
   file — rather than one being forced.
2. **All seven checks are wanted**, split across two specs so that neither
   plan grows past the size at which its own prose stops being reliable.
3. **On a failed verification the file stays**, with the failed check named,
   and `init` exits non-zero. Not reverted: the user should correct a file,
   not answer every question again.
4. **A name collision is resolved by replacing after confirmation**, not by
   refusing. This is what makes text surgery necessary, and the safety net
   below is the price of it.

## Design

### The flow

`init` refuses outside a DDEV project; `ddev/project.py::read_ddev_project`
already raises a `ConfigError` naming `.ddev/config.yaml`, and `init` lets it
through rather than restating it.

1. **Confirm the detected profile variant.** `init` prints what
   `config/detect.py` resolved — the profile and the major version — and asks
   whether that is right. It comes first because a wrong answer here
   invalidates everything written afterwards (§8 item 1: a `typo3@12` project
   whose detection stopped matching would silently run `typo3@13`'s commands).
   A "no" stops `init` with a message naming the overrides that settle it:
   the config keys are `project.type` and `project.version` — verified in
   `cli/context.py:56-72`, which reads them as `configured.get("type")` and
   `configured.get("version")`, not under the longer names the CLI flags use.
2. **Ask for the target name**, offering `production`.
3. **Offer the connection source.** The Deployer import is offered **only**
   when `deploy.php` or `deploy.yaml` exists at the project root — the two
   names Deployer itself looks for, as `transport/deployer.py` records.
   Otherwise `init` goes straight to manual entry.
   - **Deployer:** run `dep config --format=json` once, list the hosts found,
     and let the user pick one. The layout follows from the Deployer shape.
     The **stage is always asked**, never inferred: §9 states that some
     projects name hosts `production`/`staging` and others name them after
     machines, so an alias is not a stage. The written target is a
     `deployer:` block, not a copied host — the connection stays resolved at
     run time, which is what that feature was built for.
   - **Manual:** host, user, port (default 22), path, stage. **Not layout:**
     `TargetSpec.layout` is `None`-defaulted and resolves to `Layout.PLAIN`,
     whose own docstring says plain is the default because the tool is
     generic — so omitting it writes exactly the right value, and asking
     someone who has just typed a host whether their remote is "plain or
     deployer" is a question most cannot answer. A Deployer-shaped remote
     configured without the `deployer:` block is the one case that needs
     `layout: deployer`, and that user adds the line to the block `init`
     wrote.
4. **Ask which layer to write.** The global config
   (`~/.ddev/ddev-sync/config.yaml`) keeps hosts and users out of the
   repository; the project file shares the target with the team. `init` says
   that much when asking, because the template already gives that advice and
   a user running `init` has not necessarily read it.
5. **Resolve a collision.** If the chosen layer already defines a target of
   that name, `init` prints what is there and asks whether to replace it. A
   "no" returns to step 2 for a different name.
6. **Write.** A missing file is created carrying `version: 1`.

Nothing in part A writes a secret, so the local layer is untouched: secrets
arise only from the `explicit` credentials source, which is part B. Exactly
one file changes per run.

### Writing, and the net under it

Two mechanisms, chosen to keep PyYAML's comment-destroying round-trip out of
the picture entirely:

- **A new target is appended.** Where the file has no `targets:` mapping yet —
  the shipped template, where it is commented out — `init` appends a
  `targets:` block. Where one exists, the new target's lines are appended to
  it, located by indentation.
- **A replaced target is cut and re-inserted.** The target's own line under
  `targets:` is found, and everything up to the next line at the same or lesser
  indentation is replaced. No parsing, so no comment anywhere in the file is
  touched.

Text surgery on someone's configuration is only acceptable with a check that
cannot be fooled by its own cleverness, so:

**The whole configuration is parsed before the write and again after it,
through the real loader — `config/loader.py` and `config/targets.py`, not a
reimplementation.** The written target must resolve to exactly what the user
answered, and **every other target must resolve identically to before**. If
either does not hold, the original file is restored from the copy held in
memory and `init` says what it attempted, prints the YAML block it wanted to
write, and asks the user to place it by hand.

That is what makes the surgery safe without reimplementing YAML. A file using
flow-style mappings, anchors, aliases or tabs may defeat the indentation
scan — and when it does, the mismatch is caught, the file is restored, and the
user is handed the block. `init` never has to recognise those constructs.

`config/deployer.py` gains one public function for step 3,
`list_dep_hosts(payload) -> list[str]`, beside `parse_dep_config`. It stays
pure, like the rest of that module; `_hosts` is private today and listing is
the one thing the wizard needs that parsing one host does not give it.

### Verification at the end

`init` builds the execution context for the new target and runs the same
checks `doctor` does — `PreflightOperation().check(...)` rendered with
`render_doctor(...)` — then prints the checklist.

- All green: `init` says where it wrote and exits 0.
- Anything failed: the file stays, the failed rows are the output, and `init`
  exits non-zero so a script notices. It names the file it wrote, so the user
  edits rather than re-answering.

This is the §8 framing honoured as far as it can be: the configuration cannot
be checked before it exists, because the checks need a resolved target, so
`init` checks immediately after and refuses to call the result a success.

## Testing

- The wizard's questions go through thin wrappers, as `cli/prompts.py` already
  does, so tests replace `questionary` rather than a terminal.
- The writer gets a test per case: file absent, `targets:` commented out,
  `targets:` present with another target, and the target already present.
- **The net gets its own tests**, and they are the ones that matter: a file
  whose target block the indentation scan cannot handle must end with the file
  byte-for-byte as it was and the block printed for the user.
- A test that a Deployer import writes a `deployer:` block and not a copied
  host.
- A test that a declined variant confirmation writes nothing at all.
- A test that a failed preflight leaves the file and exits non-zero.

Project gates apply unchanged: 100 % line and branch coverage, no
`# pragma: no cover`, ruff, mypy strict, `subprocess` only in
`transport/runner.py`.

## Out of scope — part B

§8 items 2 to 6, each of which needs a target to exist first and mostly reuses
probes `doctor` already has:

2. Prove the credentials source actually yields credentials, and offer
   `explicit` or `wp_cli` when the default `env` finds no file. This is where
   the local layer and secret writing arrive.
3. Run the preflight table probe for sanitize rules before handing over a
   configuration that fails it.
4. Detect a shipped rule still carrying a placeholder — `wordpress.yaml` ships
   `https://REPLACE-ME.example` — ask for the real production URL and write
   the override as `mode: replace`, because appending leaves the placeholder
   rule running.
5. Check the env strategy against the project: a profile that patches a `.env`
   in a project that has none aborts the sync after the import.
6. Show the detected features and allow switching them off.

Also out of scope: a non-interactive `init` driven by flags. A wizard whose
answers are all supplied on the command line is a configuration file written
twice.

## Corrections after the first implementation

Three claims in this spec did not survive contact with the code, and the
implementer caught all three. They are corrected above and recorded here,
because each is easy to make again.

- **A missing terminal cannot be answered with flags.** The flow said an
  unanswerable question should tell the user to pass flags instead, and this
  spec itself puts a flag-driven `init` out of scope — so there is no flag to
  pass. The error says there is no terminal, that nothing was written, and
  that the installed template carries a commented example to fill in by hand.
- **The flow left out `stage`.** A target without it does not resolve at all,
  so it is asked after the connection.
- **`layout` was listed and should not be asked.** See the manual branch
  above.

Two things the implementation added that this spec had not thought of, both
kept:

- **`init` refuses `--dry-run`, `--yes`, `--target`, `--steps` and the
  feature options**, with exit 2. Without that, `--dry-run` would have written
  a file, which is the one thing that flag promises not to do.
- **Detection is extracted from `build_context` as `detect_project`**, so the
  confirmation question asks about exactly what a real run resolves, rather
  than a second implementation of the same logic.
