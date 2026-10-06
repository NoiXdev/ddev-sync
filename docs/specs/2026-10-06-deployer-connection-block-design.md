# A `deployer:` block in a sync target — design

**Status:** approved 2026-10-06, implemented, and amended afterwards: see
[Amendments](#amendments), which supersede the text above where they differ.

## Goal

Let a sync target say "my connection details come from this project's Deployer
configuration" instead of repeating them, and resolve them at run time so they
cannot drift.

## Why

A project that is deployed by a tool already states, somewhere, which host it
deploys to, as which user and into which directory — the same three things a
sync target needs. Asking for them again is asking a question the project can
already answer, and an answer that is copied is an answer that goes stale.

This was verified against a real project (`t3-13-ssg`, TYPO3 13, deployed with
Deployer 7.5) before the design was written. `vendor/bin/dep config
--format=json` returns the **resolved** configuration keyed by host alias, and
it contains exactly what a target needs.

## Scope

**In:** the configuration schema, its validation, resolution in the web
container, and where the resolved values surface (`targets`, the dry-run plan,
`doctor`).

**Out, deliberately:** `ddev sync init`. The wizard is a separate piece of work
with its own checklist, and it is not a prerequisite — a `deployer:` block is
hand-writable, which is how this will first be tested. When `init` is built it
will *offer* this block rather than reinventing it.

Also out: any second deployment tool. The nested-block shape was chosen partly
so that a second one is a new block name rather than an enum to maintain, but
nothing here anticipates one.

## Decisions, and what they rule out

**Both forms stay available.** A target may carry literal `host`/`user`/`port`/
`path`, exactly as today, or a `deployer:` block. Per target, the author's
choice. Existing configurations are untouched.

**A `deployer:` block is strict: no fallback, ever.** It means Deployer is the
only source for the four connection values. Literal values beside it are a
configuration error, rejected when the configuration is loaded. A failed
resolution fails the run with a reason — it never substitutes a value from
somewhere else.

This is the decision the rest of the design hangs on, and it exists because
this project has twice shipped a silent fallback that was right by accident on
one machine and wrong on another: `${DDEV_SYNC_DOCKER_GID:-0}`, which is the
correct group under Docker Desktop and the wrong one on Linux, and WordPress's
`rewrite-site-url` rule whose shipped placeholder keeps running as a no-op so a
forgotten override produces a green sync whose local site still points at
production. A stale literal standing in for a failed resolution would be the
same defect a third time, in a tool that drops databases.

**Resolution always runs in the web container.** Not on the host, and not in
the sync sidecar — the sidecar has no PHP by design, so `vendor/bin/dep` is
visible there but not executable. The web container is where framework commands
already run, for the same reason: Deployer is a Composer dependency of the
project, pinned by the project (`^7.5` in the verified case), so the right PHP
and the right Deployer version are the project's own, not whatever the host
happens to have.

This corrects the discovery order written in
`2026-10-05-ddev-sync-findings-from-the-matrix.md` §9, which listed
`vendor/bin/dep` → a `dep` on `PATH` → the web container. That order assumed
the engine runs where the host's binaries are. Under the add-on it does not.

**The layout is derived, not assumed.** See below — this is where the real
`dep` output forced the design.

## Configuration shape

```yaml
targets:
  production:
    deployer:
      host: web-node-1.noidee.network   # Deployer's host alias
    stage: production
```

`deployer.host` is Deployer's **host alias**, which is not necessarily a
hostname — in the verified project the two happen to coincide, and a design
that relies on that would break on a project whose hosts are named
`production` and `staging`. It may be omitted when `dep config` reports exactly
one host.

### Validation, all at load time

| Condition | Result |
|---|---|
| `deployer:` together with any of `host`, `user`, `port`, `path` | Rejected, naming the offending keys and saying that a `deployer:` block owns all four |
| `deployer:` together with `layout` | Rejected: the layout is derived from Deployer's own output |
| `deployer:` together with `ssh_alias` | Rejected: two mechanisms for the same job |
| `deployer:` together with `jump_host`, `jump_host_user`, `jump_host_port` | **Allowed** |
| `stage` missing | Rejected, as today |
| `deployer:` present but empty | Allowed — means "the single host, whatever it is called". Both `deployer:` with no value (which YAML parses as null) and `deployer: {}` count as empty |

The `jump_host` exception is deliberate and is the one place the strictness
bends. Deployer carries no canonical field for a bastion, so there is nothing
for the block to supply and therefore no conflict possible. Rejecting it would
mean a jump-host target could not use this feature at all.

## Resolution

**Command:** `dep config --format=json`, run in the web container through the
existing `web` execution place, with the project root as the working directory.

**Candidates, in order:** `vendor/bin/dep`, then a `dep` on the container's
`PATH`. This is about finding the tool, not about substituting values, so it
does not violate the no-fallback rule. When neither exists, resolution fails
and the message names both places it looked.

**Keys read:** `hostname` → `host`, `remote_user` → `user`, `port` → `port`,
`deploy_path` → `path`. Nothing else.

### What the real output forced

The verified `dep config` output for `t3-13-ssg` is the fixture this design was
written against, and three things in it would break a naive reader:

```json
{
  "web-node-1.noidee.network": {
    "hostname": "web-node-1.noidee.network",
    "remote_user": "ssg-kevelaer",
    "port": null,
    "deploy_path": "/home/ssg-kevelaer/htdocs/deployer",
    "current_path": "/home/ssg-kevelaer/htdocs/deployer/current",
    "release_path": { "error": "The \"release_path\" (…/release) does not exist." },
    "shared_path": null
  }
}
```

1. **`port` is JSON `null`, not absent.** A null means "not set", and the
   resolver must therefore **omit the key entirely** rather than pass `None`
   along. The reason is specific: `config/targets.py:154` fills a missing port
   with `flattened.setdefault("port", 22)`, and `setdefault` does not replace a
   key that is present with the value `None`. Passing the null through would
   leave `port=None`, `_ssh_prefix` would omit `-p` altogether, and the target
   would behave subtly differently from an otherwise identical literal one —
   which prints `-p 22`. Omitting the key makes the two paths produce byte-identical
   commands. A null must also never become the string `"None"`; this project has
   that coercion defect recorded already for a null `docroot:`, and this is the
   same trap one layer out.
2. **A key's value can be an error object.** `release_path` resolved to
   `{"error": …}` while the command itself exited zero. So a value that is not
   a string or null is not a crash and not a usable value: for the four keys
   read, it fails resolution with that error text; for keys not read, it is
   ignored.
3. **`shared_path` is null even though the remote plainly has a `shared`
   directory.** So the layout cannot be derived from it.

### Deriving the layout

From `current_path` and `deploy_path` only: when `current_path` equals
`deploy_path` joined with `current`, the remote has Deployer's release layout
and the target's layout is `deployer`; otherwise `plain`. Both values must be
usable strings for the derivation to run; if either is null or an error object,
resolution fails rather than guessing a layout — picking the wrong one sends
every file path to the wrong place.

### Caching

Resolved once per run and reused, following the per-run cache the engine
already uses for remote database credentials. `dep config` evaluates the
project's recipe, so repeating it per step would be both slow and a
second chance to produce a different answer mid-run.

## Failure modes

Every one of these fails the run. Each message's job is to make the next action
obvious, and to include the tool's own words where it has any, the way
`doctor`'s ssh row now carries ssh's stderr.

| Failure | The message must say |
|---|---|
| No `dep` found | Both places looked in, and that Deployer belongs in the project's `require-dev` |
| `dep config` exits non-zero | That the project's recipe failed to evaluate, with dep's stderr |
| Output is not parseable JSON | The same, with what was received |
| `deployer.host` given but not in the output | The alias asked for, and every alias found |
| `deployer.host` omitted and the output has several hosts | Every alias found, and that one must be named |
| A read key is missing, null where a value is required, or an error object | Which key, and dep's error text when there is one |
| `current_path`/`deploy_path` unusable | That the layout could not be determined |

## Where the resolved values surface

**`ddev sync targets`** shows the resolved endpoint and marks that it was
resolved rather than written, so the listing does not imply the config contains
a hostname it does not contain.

**The dry-run plan** resolves, and shows the real endpoint. This refines a
documented promise: the dry run currently "touches nothing and connects to
nothing". `dep config` contacts no remote and writes no file, so the promise
holds in substance, but it does execute the project's own PHP in the web
container. The documentation will say so for `deployer:` targets. The
alternative — printing an unresolved placeholder — was rejected because
checking *where a sync will connect* is the dry run's main purpose, and it
would be unavailable for exactly these targets.

**`doctor`** gains a conditional row, shown only when the target has a
`deployer:` block, following the pattern of the existing conditional rows
(`sanitize tables`, `web container`, `ssh agent`). It reports whether
resolution succeeded, and on failure carries the reason. It is a separate row
because "we could not work out where to connect" is a different failure from
"we could not connect", and collapsing them into the ssh row would send a user
looking at their key.

The row comes before the ssh row, since a failed resolution means the ssh check
could not run at all.

## Testing

**The fixture is real.** The captured `dep config` output above, including the
null port and the `release_path` error object, becomes a test fixture. An
invented fixture would have had neither, and the implementation would have been
written against a shape that does not occur.

Required coverage, under the repository's 100 % line-and-branch gate with
`# pragma: no cover` forbidden:

- Every validation rejection in the table above, including the allowed
  `jump_host` combination.
- Every failure mode in the table above.
- `port: null` producing the engine's default rather than a `"None"` string.
- An error object in each of the four read keys.
- Layout derivation: the `current` case, the plain case, and the unusable case.
- The alias cases: one host with no `host:`, several hosts with no `host:`,
  a named alias present, a named alias absent.
- Caching: one resolution per run, asserted by counting invocations.

**End to end**, by hand against `t3-13-ssg`, whose add-on is installed and
whose `doctor` is green: replace the literal target with a `deployer:` block
and confirm the dry run and `doctor` agree with the literal version, and that
`targets` shows the declaration rather than the resolution (see Amendment 1).
That is the acceptance evidence for the whole feature, and it is available
because the add-on was exercised against a real production host first. It has
been done: the dry run resolved through `dep config` and printed the same
endpoint and `deployer`-layout paths the literal target did, and `doctor` was
fully green.

## Not in this design

- **`ddev sync init`**, with its own checklist (§8 of the matrix findings) and
  its offer to write either shape.
- **A second deployment tool.** The block shape leaves room; nothing here
  prepares for one.
- **Importing database credentials.** Deployer does not have them: in the
  verified output `db_name`, `db_user` and `db_password` are unresolved
  interactive prompts (`{"ask": " DB name: "}`). Credentials continue to come
  from the credentials source, and this feature does not touch that.
- **Inferring `stage` from the alias.** Aliases are not stage names — in the
  verified project the alias is a hostname. `stage` stays literal and required.

## Amendments

Writing the plan and then the code disproved three statements above. They are
left in place so the reasoning stays readable, and corrected here, because a
spec that silently disagrees with the code is worse than no spec.

### 1. `ddev sync targets` shows the declaration, not the resolution

*Replaces "Where the resolved values surface", first paragraph.*

Both target listings perform no I/O, and must not start to: they read the
configuration and nothing else, so they never run `dep config` and never
contact anything remote. A listing that depended on resolution would depend on
the project's own `deploy.php` evaluating cleanly, and a broken recipe would
then hide the very configuration needed to fix it. For a `deployer:` target the
listing therefore shows the block as written - `from deployer`, plus the alias
when one is given - and does not show a hostname, because the configuration
does not contain one.

This is a property of the listing, not a promise that a stopped project stays
stopped: invoked as `ddev sync targets`, the command is a DDEV service command,
and DDEV starts the project to run one. That happens for every `ddev sync`
invocation and is DDEV's behaviour, not the listing's.

The resolved endpoint appears where work is already being done: in the dry-run
header, and through `doctor`, which fails for such a target when resolution
fails.

### 2. There is no `doctor` row

*Replaces "Where the resolved values surface", the `doctor` paragraphs.*

The spec gave `doctor` a conditional `deployer config` row. `doctor_command`
receives a `RunContext` that is already built, and resolution happens inside
`build_context`, so a failed resolution raises before `doctor` is reached. The
row could only ever say `OK`: a `bool` whose `False` branch is unreachable, and
which no test could legitimately cover under this repository's 100 % branch
rule with `# pragma: no cover` forbidden. A row that cannot fail does not
report anything, so it was dropped rather than kept as decoration.

The distinction the row was meant to draw - "we could not work out where to
connect" versus "we could not connect" - is kept, by the error itself: a failed
resolution aborts the run, `doctor` included, with a `DeployerError` whose
message names the cause.

### 3. The `extends` chain follows a precedence rule, not a flat rejection

*Amends the validation table, which was written for one spec in isolation.*

The codebase already resolves `host` versus `ssh_alias` across an `extends`
chain by letting the stronger link win. `deployer:` is a third alternative for
the same job and follows the same rule: whichever of `host`, `ssh_alias` and
`deployer` the stronger link sets wins, and the others are dropped. A flat
rejection would have made it impossible to extend a literal target with a
`deployer:` one, or the reverse, which is the very thing `extends` is for.

Only a combination **with `deployer:`** is ever rejected: `host` together with
`ssh_alias` stays allowed, with the alias winning, as before. The rejection
applies **within one spec**, and across layers that define the same target name
(which merge into one spec). Two consequences:

- When `deployer:` wins, the weaker links' `user`, `port`, `path` and `layout`
  are dropped with the weaker `host`. That is the one place a value disappears
  without an error naming it, and it has to: keeping a base's `port: 2222`
  would let a Deployer host that states no port connect on a port the block
  never asked for.
- A literal `user`, `port`, `path` or `layout` set by a link **stronger** than
  the `deployer:` block contradicts it exactly as one beside it would, and is
  rejected.
