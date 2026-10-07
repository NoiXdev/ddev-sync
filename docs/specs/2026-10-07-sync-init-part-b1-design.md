# `ddev sync init`, part B1: the checks that need no network — design

**Status:** approved by the owner 2026-10-07, after a design conversation.
Part B of `ddev sync init` is split in two; this is the first half.

## Why

`docs/specs/2026-10-07-sync-init-design.md` delivered items 1 and 7 of §8 of
`2026-10-05-ddev-sync-findings-from-the-matrix.md` and put items 2 to 6 out of
scope, under the framing the whole command exists for: **`init` should refuse
to leave behind a configuration that `doctor` would then reject.**

Those five remaining items split cleanly along the line this codebase already
draws between a pure half and an I/O edge. Three of them — 4, 5 and 6 — are
decidable from the filesystem and the merged configuration alone, with no
connection to any remote. Two of them — 2 and 3 — need ssh to the target.

Part B1 is the first three, plus the writer generalisation all of them need
and part B2 inherits. Part B2 is items 2 and 3, and with them the local
layer, secret writing and the `.gitignore` question.

## What is already true

Established by measuring the code, not assumed. Two of these contradict §8.

- **§8 item 3 is already delivered.** It asks that the preflight table probe
  for sanitize rules run before a configuration is handed over.
  `PreflightOperation.check()` already calls `_check_sanitize_tables`
  (`operations/preflight.py:150`), `doctor` renders the row, and part A's
  `init` returns `doctor`'s exit code. A configuration that fails the probe is
  not reported as a success today. Nothing in part B1 or B2 needs to add the
  probe; at most B2 adds an *offer to fix* what it reports.
- **§8 item 4's stated remedy would disable the anonymisation.** It says to
  write the override as `mode: replace`, "because appending leaves the
  placeholder rule running". The first half is right and the second half is
  the problem: `config/merge.py:14` gives lists exactly three modes —
  `extend`, `replace`, `prepend` — and `replace` swaps the **entire**
  `sanitize.rules` list. `profiles/wordpress.yaml` defines four rules;
  replacing the list with a corrected `rewrite-site-url` alone would stop
  `anonymize-users`, `reset-user-passwords` and `local-admin` from ever
  running again. The sync would report success having left production's email
  addresses and password hashes in the local database. The codebase already
  knows this shape: `operations/catalog.py:166` and the `OperationError` at
  `catalog.py:282` both spell out that `mode: replace` means carrying the
  profile's other rules yourself.
- **The placeholder pattern that solves it exists.**
  `operations/placeholders.py` resolves `${WP_TABLE_PREFIX}` from a config key
  first (`WP_TABLE_PREFIX_CONFIG_KEY`, line 26), falls back to a probe, and
  raises a named `OperationError` when neither yields a value (line 144).
  `${SYMFONY_USER_TABLE}` is the same pattern without the probe. Both were
  introduced for the identical failure: a value hardcoded in a profile that is
  wrong for some real installs, failing silently.
- **Expansion reaches every rule parameter.** `operations/sanitize.py`'s
  `_parse_rule` runs `expand_value` over `sql` and over every non-reserved
  parameter, so a placeholder in a rule's `from:` resolves at parse time.
- **An unknown placeholder is silently left verbatim.** `_expand`
  (`placeholders.py`) only substitutes keys present in `_substitutions`
  (line 151). So a placeholder only fails loudly if it is *registered* and its
  resolver raises — which is exactly what the `${WP_TABLE_PREFIX}` resolver
  does, and why registering is not optional.
- **The env step is planned without checking that the file exists.**
  `engine/plan.py::_env` (line 384) emits the step whenever `env.strategy` is
  not `none` and `env.set` is non-empty. The file is looked for first in
  `operations/env.py::_find_env_file` (line 125), which runs mid-sync, after
  the database has been imported. §8 item 5's failure is real and its timing
  is the worst possible one.
- **`_find_env_file` matches against real directory entries, not `is_file()`.**
  Line 152 builds a set from `iterdir()` because `is_file()` folds case on a
  case-insensitive filesystem, which would let a probe for `.env` match a file
  named `.ENV`. Its own docstring says not to "simplify" this back.
- **Profiles disagree about env in a way that matters.** `laravel.yaml` sets
  `file: [.env]`, `symfony.yaml` and `shopware6.yaml` set
  `file: [.env.local, .env]`, all three with `strategy: patch_keys`.
  `drupal.yaml`, `generic.yaml`, `typo3.yaml` and `wordpress.yaml` set
  `strategy: none`, so they never plan the step at all.
- **A written `features:` key switches detection off entirely.**
  `features/resolver.py:68` — `if requests is not None` takes the explicit
  branch and never reaches `_detected`. `parse_feature_key` returns `None`
  only when no `features:` key was written; an empty list is "none,
  deliberately". So writing the key is not a small act: it freezes this
  project's feature set for good.
- **Detection already explains itself.** `ActivatedFeature.reason` is built at
  `resolver.py:146` as `detected: <description>`, where `_describe` names the
  kind of fact that matched ("composer package filament/filament"), not just
  the value.
- **`cli/prompts.py` already has the multi-select.** `_checkbox` (line 49)
  wraps `questionary.checkbox`, returns `None` with no terminal like every
  other prompt, and treats an empty list as a real third answer.
- **`build_context` resolves the target last, and everything else first.**
  `cli/context.py:100-128` reads the project, merges the user layers, detects,
  resolves the profile, resolves the features, and merges profile + feature +
  user layers into `merged` — and only *then* calls `resolve_target`. Nothing
  above that last call knows a target exists. So the whole of what B1's three
  questions need is already computed in one place, in the right order, by the
  code a real run uses.
- **`config/writer.py` knows exactly one location.** `_targets_line`
  (line 232) finds the line that is exactly `targets:`; `_target_line`
  (line 245) finds a child key under it. Everything else in the module — the
  atomic write, the restore, the verification — is independent of which key
  that is.
- **`ExecutionContext.config` takes any dotted path**
  (`operations/base.py:130`), so a new config key needs no plumbing.
- **`profiles/schema.py::_validate_sanitize` (line 79) checks only `rules`**
  under `sanitize:`, so a sibling key there is not rejected.

## Decisions

Each was taken by the owner during the design conversation.

1. **Part B is split: B1 is everything decidable without a network, B2 is
   everything that probes the remote.** B1 is items 4, 5 and 6 plus the
   writer; B2 is items 2 and 3 plus the local layer and secrets.
2. **Item 4 is solved with a registered `${PRODUCTION_URL}` placeholder, not
   with a `mode: replace` override.** See "What is already true" above for why
   the override would disable the anonymisation, and
   `operations/placeholders.py` for the pattern it follows instead.
3. **Item 5 offers to name the file, and otherwise writes nothing.** A missing
   `.env` in a freshly cloned Laravel project is a temporary state that
   resolves itself; configuration written because of it would be wrong a month
   later. A differently *named* env file is a genuine configuration fact and
   is worth writing. Offering `env.strategy: none` was considered and rejected:
   it is an offer to silence a whole sync step, which is the kind of answer
   taken under time pressure and not explicable six months on.
4. **Item 6 writes `features:` only when the user actually deselects
   something.** Keeping everything writes nothing, so detection stays live.
5. **Part A's `write_target` is not modified.** Its proof — that nothing
   outside `targets` changed — is correct and narrow, and widening it to
   permit project keys would weaken it exactly where it carries weight.
6. **`init` still writes exactly one file per run.** Both writes go to
   `.ddev/ddev-sync.yaml`. The local layer stays untouched until part B2.
7. **`build_context` is split at the seam it already has**, so the new
   questions ask about exactly what a real run resolves. Taken during the
   spec's own review, when the first draft was found to assume `init` had a
   merged configuration it does not have at that point. See "One resolution,
   before any target exists".

## Design

### The flow

Part A's flow is unchanged except that one block of questions is inserted, and
one write is appended:

```
1. Confirm the detected profile          (part A, unchanged)
2. Production URL   ─┐
3. Env file          ├─ new, asked only when there is something to decide
4. Features         ─┘
5. Name, collision, connection, stage    (part A, unchanged)
6. Write the target                      (part A, unchanged)
7. Write the project keys                (new, only when step 2-4 produced any)
8. doctor                                (part A, unchanged)
```

The new questions sit directly after the detection confirmation because they
are about the **project**, not the target: which URL production serves, which
file holds this project's environment, which features this checkout has. Part
A's reason for its own ordering — that someone who declines a collision should
lose one answer, not five — is untouched, because none of these three answers
is discarded by a later decline: they are written in step 7, which a `init`
that ended at step 5 never reaches.

**Every one of the three is skipped when there is nothing to decide.** A TYPO3
project is asked no WordPress URL; a project whose `.env` exists is asked no
env question; a project where nothing was detected is asked no feature
question. On the common path B1 changes nothing a user sees. This matters
because the alternative — three more questions on every run — would make
`init` worse for the majority to serve a minority, and `init`'s whole value is
that a new user can get through it.

As in part A, a question with no terminal attached returns `None` and is
turned into the existing "there is no terminal" error at once rather than
guessed past.

### One resolution, before any target exists

All three questions need the **merged profile + feature + user configuration**:
item 4 reads `sanitize.rules` to see whether a rule mentions the placeholder,
item 5 reads `env.strategy` and `env.file`, item 6 needs the resolved feature
set. Today that merge exists only inside `build_context`, which part A can
only call *after* the target has been written, because it ends by resolving
one.

But it ends by resolving one — everything above that call is target-free
(`cli/context.py:100-128`). So `build_context` splits in two at that seam:

- `resolve_project(...) -> ProjectResolution` — project, detection, profile,
  `FeatureResolution`, and the merged configuration. No target anywhere.
- `build_context(...)` — calls `resolve_project`, then `resolve_target`, then
  assembles the `RunContext` exactly as now.

This is the same extraction part A already made and recorded as a correction:
`detect_project` was pulled out of `build_context` "so the confirmation
question asks about exactly what a real run resolves, rather than a second
implementation of the same logic". The identical argument decides it here, and
a second merge written by hand inside `init` is exactly the duplicate that
correction exists to prevent.

`init` then calls `resolve_project` **once**, before the name question, and
uses it for all four questions that precede the target: part A's detection
confirmation (which today re-merges the user layers for itself, and stops
doing so) and B1's three. `build_context` is still called at the end, with the
written target, to run `doctor` — unchanged.

The confirmation question is still asked before all three new questions and
before every write — that ordering is unchanged. It is no longer asked before
*everything*: `resolve_project` resolves the profile as part of building
`ProjectResolution`, which `init` calls before the confirmation question runs,
so a profile that does not resolve at all (`ddev sync init --type nonesuch`)
now stops `init` with nothing asked and nothing written, instead of running
the wizard to completion and only then failing at the `doctor` check with "it
could not be checked". Order inside one resolution, not one resolution per
question.

### The second write, and its proof

Part A's `write_target` places one target and then proves on disk that it did
nothing else: the file still loads, the named target is byte-for-byte the
rendered block, no top-level key outside `targets` differs, and no other
target in the file changed. Anything that fails after the first byte restores
the original bytes from memory.

B1 adds `write_settings`, a sibling with the **mirrored** proof:

| | `write_target` (part A) | `write_settings` (B1) |
|---|---|---|
| before | the whole configuration must load | unchanged |
| after | the file still loads | unchanged |
| after | the named target equals the rendered block | each written key, read back, equals what was asked |
| after | no top-level key outside `targets` changed | no top-level key outside the written ones changed |
| after | no *other* target in this file changed | **no** target in this file changed |
| on any failure | restore the original bytes | unchanged |

Two narrow writes rather than one general one. The alternative — teaching
`write_target` which keys outside `targets` are permitted to move — turns a
proof that currently says "nothing" into one that says "nothing except a list
the caller supplied", and a proof with a caller-supplied exemption is a weaker
proof. The shared machinery (`_write`, `_restore`, `_outside_targets`,
`_file_targets`, `_block_end`) is reused unchanged; only the two comparisons
differ, and they differ by being each other's complement.

### Generalising the placement

`place_target(document, name, block)` becomes
`place_entry(document, path, block)`, where `path` is a tuple of keys:

| call | writes |
|---|---|
| `("targets", name)` | part A's target — behaviour identical |
| `("sanitize", "production_url")` | item 4 |
| `("env", "file")` | item 5 |
| `("features",)` | item 6 |

`place_entry` walks the path: for each key it looks for that key's line at the
expected indent within the enclosing block; a key it does not find ends the
walk and the remainder is appended as fresh nested lines; the final key's
existing block, if any, is replaced. `_targets_line` and `_target_line` become
one `_key_line(lines, key, start, indent)`, which is what they already both
are.

Two properties are kept deliberately:

- **The scan stays deliberately naive.** Part A's `_targets_line` finds only
  the exact spelling `targets:` — not `targets: {}`, not a quoted key, and it
  mistakes the same text inside a multi-line quoted scalar for the real thing.
  That is acceptable *because* of the verification, not in spite of it, and
  the same bargain holds for every key B1 adds. Do not make the scan cleverer;
  make the proof catch it.
- **`_block_end`'s comment rule is unchanged.** Blank and comment lines after a
  block's last content line belong to whatever follows, so a replacement never
  swallows the note someone wrote above the next key. The shipped
  `.ddev/ddev-sync.yaml` is almost entirely comments, so this is the rule that
  keeps `init` from quietly deleting the file's own documentation.

`env.file` is written as a **child** key, not by replacing `env:` wholesale: a
project that has set its own `env.set` overrides must not lose them because
`init` corrected a filename.

### Item 4: `${PRODUCTION_URL}`

Three changes:

1. `operations/placeholders.py` registers `${PRODUCTION_URL}` in
   `_substitutions`, resolved from a new `sanitize.production_url` config key.
   When the key is unset it raises an `OperationError` naming the key and the
   file to set it in — the shape of the `${WP_TABLE_PREFIX}` failure at
   `placeholders.py:144`. The key sits under `sanitize` rather than `database`
   because a site URL is not a database fact; `_validate_sanitize` already
   permits a sibling of `rules`.
2. `profiles/wordpress.yaml:23` becomes `from: "${PRODUCTION_URL}"`.
3. `init` asks for the URL when `ProjectResolution.merged` contains a sanitize
   rule whose parameters mention `${PRODUCTION_URL}` and
   `sanitize.production_url` is not already set, and writes the answer. The
   check is for the literal placeholder text in the *unexpanded* merged
   configuration — not for the profile being `wordpress` — so a project or a
   future profile that adopts the placeholder is asked the same question
   without anything here learning its name.

**Validation is about emptiness, not quoting.** `SearchReplace._via_wp_cli`
(`operations/catalog.py:110`) passes the value as a list element to
`web_command`, never as a shell string, so there is no injection surface and
`_validated_identifier`'s SQL-identifier rules do not apply. The real hazard is
a degenerate value: `wp search-replace "" ...` matches everywhere in every
column. So the value must be non-empty, carry an `http://` or `https://`
scheme, and contain no whitespace. Both the prompt and the placeholder
resolver enforce this — the prompt so the user is corrected while they are
still typing, the resolver because a hand-edited file never passes the prompt.

#### This changes behaviour for existing WordPress users

Today a WordPress target whose configuration was never hand-edited runs
`rewrite-site-url` against `https://REPLACE-ME.example`: a full `wp
search-replace` pass over the database that matches nothing, after which
production's real URL is still in every row. The sync reports success.

After this change the same configuration fails, with a message naming
`sanitize.production_url` and the file to set it in. That is the honest
outcome and the reason the finding exists — but it is a break, not a pure
addition, and the implementation plan must treat it as one:

- The error must be actionable on its own, since most people will meet it
  without having read this document.
- It surfaces in `doctor` **before** any sync, because `parse_rules` runs
  inside the preflight sanitize-table probe, so a user who runs `doctor` after
  updating the add-on sees it with the database untouched.
- It must be stated in the add-on's release notes.

### Item 5: the env file

`init` reads `env.strategy`, `env.file` and `env.set` from
`ProjectResolution.merged` — the merged value, not the profile's own, so a
project that has already overridden any of the three is judged on what a real
run would use. When the strategy is `none`, or `env.set` is empty, there is no
step and no question; those are `engine/plan.py::_env`'s own two conditions
for emitting nothing, read from the same place it reads them.

Otherwise it compares the configured candidates against the project root's
real directory entries, built from `iterdir()` for the case-folding reason
`_find_env_file:152` documents. If one exists, nothing is asked.

If none exists, `init` says which names it looked for, lists the `.env*`
entries that *are* in the project root, and offers them. A chosen name is
written as `env.file: [<name>]` — a list, matching the profiles' own spelling —
after confirming the file exists. Choosing none writes nothing and prints what
will happen: the sync will stop at the env step, **after** the database has
been imported, and the file should be created first.

`.env.example` appears in the list, because hiding a file that is really there
is its own kind of lie, but it is never preselected: patching the example is
almost never what anyone wants, and the near-universal fix for the state that
produces it is to copy it to `.env`.

### Item 6: features

`ProjectResolution.features` is the `FeatureResolution` a real run would get,
and `ActivatedFeature.reason` already explains each entry
(`resolver.py:146`). Nothing is asked when it activated nothing, and nothing
is asked when the user layers already define a `features:` key — in that case
the resolution's entries came from that key rather than from detection, so
offering to deselect them would be offering to rewrite an answer the user has
already given. That condition is read from the user layers directly, not
inferred from `reason`'s text.

Otherwise `init` lists the detected features with their reasons and offers
them through `_checkbox`, all preselected. Then:

- **Everything kept → nothing written.** Detection stays live, so a feature
  added to this project next month is still found. This is the whole reason
  the rule exists; see `resolver.py:68`.
- **Something deselected → `features:` is written as a bare list of the kept
  names.** A bare name reproduces detection exactly: `_from_request` builds
  `resolve_settings(feature, Request(name, {}, source))`, which is the same
  call `_detected` makes. Deselecting everything writes `features: []`, which
  `parse_feature_key` already reads as "none, deliberately" — distinct from
  the absent key.

The price of writing the key — that this project's feature set is now
frozen — is real, and `init` says so in the line it prints after writing,
naming the key and the file, so the user can delete it later to get detection
back.

## Testing

Everything in B1 is testable without a network; that is what the split was
for. The suite covers:

- `place_entry` for each of the four path shapes, against a document that has
  the key, one that does not, one where the key is nested under an existing
  parent, and one whose comments must survive.
- `write_settings`'s proof: each of its four refusals, and that every refusal
  leaves the original bytes on disk.
- That part A's `write_target` behaviour is unchanged by the generalisation —
  its existing tests must pass untouched.
- That `build_context` is unchanged by the `resolve_project` extraction — its
  existing tests must pass untouched, and `resolve_project` gets its own tests
  for the four values it returns.
- `${PRODUCTION_URL}` resolving from the key, and raising with a message that
  names the key when it is absent.
- The URL validation, including the empty string and a value with no scheme.
- The env comparison against a directory containing `.ENV` but not `.env`,
  which is the case `iterdir()` exists for.
- The feature question writing nothing when everything is kept, and the exact
  list when something is not.
- That each of the three questions is skipped when its precondition is absent.

Project gates apply unchanged: 100 % line and branch coverage,
`# pragma: no cover` forbidden, ruff, ruff format, mypy strict over `src`,
`tests` and `tools`, `subprocess` confined to `transport/runner.py`.
TDD: the failing test first.

## Out of scope — part B2

§8 items 2 and 3, and everything they drag in:

- Prove the credentials source actually yields credentials, and offer
  `explicit` or `wp_cli` when the default `env` finds no file. Note that this
  is wider than §8 states: `drupal.yaml` and `generic.yaml` default to
  `remote_credentials: explicit`, and nothing writes `database.credentials`
  today, so a Drupal target is red after `init` until someone edits the file
  by hand.
- Offer a correction when the sanitize-table probe fails. The probe itself is
  already run (see "What is already true"); only the offer is missing.
- Writing `.ddev/ddev-sync.local.yaml`, which the add-on does not ship, and
  with it the first secret `init` ever writes — and therefore the question of
  whether `init` should touch `.gitignore`.

Also out of scope, as in part A: a non-interactive `init` driven by flags.

## Corrections after the first implementation

Four claims in this spec did not survive contact with the code. Three were
already known when implementation began and are corrected in place as well as
recorded here; the fourth was found by reading the finished code against this
document.

- **"The confirmation question is still asked first" was too strong, and has
  been corrected above.** `init` now calls `resolve_project` — which resolves
  the profile itself, not merely its name — before the confirmation question
  runs. So a profile that does not exist at all (`ddev sync init --type
  nonesuch`) now stops `init` before the confirmation question, before any of
  the other three questions, and before any write. Before this change, the
  same mistake ran the whole wizard to completion, wrote the target, and only
  then failed inside `build_context`'s `doctor` check with "it could not be
  checked" — a typo in a flag used to buy itself a target written into the
  user's file before anything noticed. The guarantee that actually survives is
  narrower than "first": the confirmation precedes all three of B1's new
  questions and every write, which is all the design ever needed from it. The
  new ordering is strictly better than the old one; the sentence describing it
  was simply wrong.

- **The `${PRODUCTION_URL}` change touched six files, not the three this spec
  named.** "Item 4: `${PRODUCTION_URL}`" lists `operations/placeholders.py`,
  `profiles/wordpress.yaml` and `init` as "three changes". Registering the
  placeholder makes `parse_rules` raise for any WordPress configuration that
  lacks `sanitize.production_url`, and that reached three more places that had
  to change alongside it:
  - `tests/profiles/test_wordpress.py`: three existing tests reach
    `parse_rules` through the module's shared `context_for` helper, and all
    three needed `sanitize.production_url` added to the layer it builds, or
    they would now fail on the very configuration they exist to exercise. A
    fourth test asserted on the old `REPLACE-ME` literal in `rule["from"]` and
    had to be rewritten to assert `${PRODUCTION_URL}` instead.
  - `tests/matrix/cells/mxwordpress/ddev-sync.yaml`: its per-project
    `sanitize.rules` override — written to supply the real production URL —
    became a second, duplicate `rewrite-site-url` rule sitting beside the
    shipped one now that the shipped rule resolves the value itself. It was
    replaced with a `sanitize.production_url` key.
  - `profiles/VERIFIED.md`'s wordpress row asserted that the shipped
    `rewrite-site-url` rule "runs as a no-op by design" and that the profile
    "needs a per-project `sanitize.rules` override" for the production URL.
    Both became false the moment the rule started resolving a real value, and
    the row was corrected to describe the placeholder path instead, noting
    that the matrix run it cites predates the change.

- **The plan's draft commit message overstated what `mode: replace` is for.**
  It said overriding `sanitize.rules` instead of registering the placeholder
  was rejected because the list "can only be replaced wholesale" — read
  plainly, that says there is no way to add to it, which is false:
  `config/merge.py`'s `DEFAULT_LIST_MODE` is `extend`. What is true, and what
  this spec's own "What is already true" section already said correctly, is
  narrower: extending runs a *second*, duplicate `rewrite-site-url` rule
  beside the placeholder one rather than fixing it — the duplicate did the
  real work, the shipped rule still matched nothing — and only `mode: replace`
  removes the placeholder rule, at the cost of repeating `anonymize-users`,
  `reset-user-passwords` and `local-admin` in the override to keep them
  running. The commit message that shipped (`86da624`) already uses this
  corrected wording; this entry exists because the plan's draft did not.

- **A fourth check, with nothing to correct.** The brief asked specifically
  whether two implementation details were missing from this spec:

  - `_key_line`'s parent/leaf distinction. The code's docstring for
    `_key_line` (`config/writer.py`) explains that a **parent** key must be a
    bare `key:` — the walk descends into the block it opens — while a
    **leaf** may carry its value inline (`production_url: https://...`, or a
    flow-style list), because every setting `init` writes is a leaf, and
    without that distinction a second run could never find its own key again:
    it would append a duplicate and the strict loader would then refuse the
    file. This spec's "Generalising the placement" section does not describe
    that distinction — it only restates part A's "deliberately naive" rule for
    `_targets_line` (an exact bare spelling) and says `_targets_line` and
    `_target_line` "become one `_key_line`", without saying that the merge
    also had to add the inline-leaf case part A never needed. This is missing
    from the spec and is recorded here as the fourth correction.
  - Flow-style list rendering. `render_setting` writes every list value in
    flow style (`features: [a, b]`), and its docstring gives the reason: a
    block-style list's items sit at the key's own indent (see
    `_block_end`'s sequence-item rule), so a one-line value is what makes a
    rewrite of the key unable to strand part of the old value in the file.
    This spec does show `env.file: [<name>]` as a flow-style example in
    "Item 5: the env file", so the *shape* is not undocumented — but the
    general rule ("every list this module writes is flow-style, and why") is
    not stated, only the one instance. Folded into the point above rather than
    counted as a separate, fifth correction, since both are the same gap:
    this spec describes `place_entry`'s walk and part A's naive scan in
    detail, but not the two properties `_key_line` and `render_setting` needed
    to add to make settings — as opposed to targets — reliably rewritable.
