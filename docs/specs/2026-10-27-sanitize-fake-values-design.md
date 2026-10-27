# Fake values for sanitize — design

**Status:** agreed in chat 2026-10-27, reviewed by a builder check, awaiting approval.

## Purpose

Sanitize rules today make personal data unrecognisable by overwriting it with
something mechanical: `CONCAT('user', id, '@example.invalid')`, `NULL`, or a
constant. That is safe, but a synced project is then hard to work with: lists
show `Guest 0198f118-e664-…`, mail templates render empty names, and a form
prefilled from a record looks broken. The point of syncing production is to
test the application against realistic data, and blank or obviously generated
values defeat that for exactly the screens that show people.

This adds a sanitize rule type that replaces column values with **realistic,
fake values** — names, email addresses, text, numbers, postcodes, places,
phone numbers, IP addresses — so the interface looks the way it does in
production without carrying anyone's data.

The first user is the oktoberfest project, whose `reservations` table holds
guests' names, email addresses and a free-text comment. Today a project rule
there writes `Guest <uuid>`, `reservation<uuid>@example.invalid` and `NULL`.

Success is: after a sync, a developer opening the reservations list sees
plausible names, addresses and comments; the same row looks the same after
the next sync; no value is a real person's; and a project can switch the fake
values, or sanitizing as a whole, off in one line.

## Goals

- A new sanitize operation, `fake`, that sets named columns of a table to
  generated values of a named kind.
- The kinds: `name`, `first_name`, `last_name`, `email`, `text`, `integer`,
  `zip`, `city`, `phone`, `ip`.
- **Stable per row.** A value depends only on the table, the column and the
  row's key, so the same row gets the same fake value on every sync.
  Screenshots stay comparable and nothing jumps around between syncs.
- **Empty stays empty.** A column that is `NULL` stays `NULL`; an empty string
  stays an empty string. Only values that were there are replaced.
- **English seed data** (decided): English first and last names, US-style
  places and five-digit ZIP codes, Lorem Ipsum for text.
- **Safe by construction where the kind allows it:**
  - email addresses end on a configurable domain, default `example.fake`
    (decided);
  - IP addresses come from the documentation ranges, `192.0.2.0/24` (v4) and
    `2001:db8::/32` (v6);
  - phone numbers use North America's fictional `555-01xx` block, e.g.
    `+1 555 0142`.
- **Unique where it matters.** An email address carries a short suffix
  derived from the key (`james.carter.3f2a@example.fake`), so two rows that
  draw the same name do not collide on a unique email column.
- **The email domain is a setting** at four levels, the more specific one
  winning (decided):
  1. built-in default `example.fake`
  2. global, `~/.ddev/ddev-sync/config.yaml`
  3. project, `.ddev/ddev-sync.yaml` (and the local layer)
  4. on the rule itself
  Levels 2 and 3 are the existing config layers; the key is
  `sanitize.email_domain`, and a rule's own `email_domain` overrides it.
- **Three project switches**, present from the start (decided):
  - `sanitize.disable_seeding: true` — no fake values are generated. Every
    `fake` rule still runs, but **masks** instead: its columns are still made
    unrecognisable, just not realistic (decided). See "Masking" below.
  - `sanitize.disable_sanitize: true` — the rules that hide **personal data**
    are skipped; every protective rule keeps running (decided). See
    "Personal-data rules and protective rules" below.
  - `sanitize.disable_sanitize_all: true` — no sanitize rule runs at all, the
    profile's and the protective ones included (decided).
- **The shipped profiles move to fake values** (decided): the profiles'
  anonymize rules (Laravel, Symfony, WordPress, Drupal, TYPO3 and its
  variants, Shopware's customers) become `fake` rules, so a stock project
  gets realistic users without writing a rule.
- The oktoberfest project replaces its SQL rule with a `fake` rule over
  `name`, `email` and `comment`. Not `seating_request`: it is a seat count,
  not personal data, and the app casts it to an enum of 1 to 4, so a faked
  value outside that range would crash the list it is meant to make usable.
  That project's `.ddev/ddev-sync.yaml` lives in its own repository; changing
  it is the last step, after this is released, and is not committed there
  without being asked.

## Non-goals

- Locales other than English. The seed lists sit so that another language can
  be added later; there is no setting for it now.
- A general Faker. The kinds above and their settings, nothing more — no
  dates, no addresses as one string, no company names, no free templates.
- Generating rows. This rewrites values in rows that came from production; it
  never inserts.
- Running PHP, a framework seeder, or anything in the web container. The
  operation runs as SQL against the local database, like every other sanitize
  rule.
- Relationships between columns. `email` does not have to match `name` in the
  same row. (It may, as the builder's choice — see "Left to the builder".)

## Anti-goals

What would make this a failure even if it technically worked:

- **Any path that leaves production values in place.** `disable_seeding`
  masks, it does not skip. A typo in a column name must stop the sync before
  the local database is overwritten, not leave the column untouched. A `fake`
  rule that fails must fail loudly, like every other sanitize rule.
- **Fake values that reach real people.** No generated email may land on a
  deliverable domain by default, no IP on someone's network, no phone number
  outside the fictional block.
- **Values that change on every sync.** Random per run would make the data
  useless for comparing screens.
- **A silent `disable_sanitize`.** It puts production's personal data on a
  laptop; it must be impossible to miss (see below).

## Constraints

- **SQL only, in the local database.** Sanitize runs after the import, in the
  sidecar, through `local_mysql_argv` (`operations/sanitize.py`). The sidecar
  has no PHP; the generation has to be expressible as SQL that the local
  MariaDB (10.8 and later, as DDEV ships it) runs. Locally that is always
  MariaDB or MySQL as DDEV provides them.
- **Rules are data.** A `fake` rule is declared in YAML like every other rule
  (`operations/sanitize.py` module docstring), registered as an operation in
  `operations/catalog.py`'s `SANITIZE_OPERATIONS`.
- **Lists merge by `extend`** (`config/merge.py`): a project's
  `sanitize.rules` appends to the profile's. Nothing about that changes.
- **The preflight probe** (`operations/preflight.py::_required_tables`)
  checks that every table a non-tolerated rule needs exists, before the dump.
  A `fake` rule's `table` counts, as for any operation rule. **Columns are new
  here:** the probe must also check that every column a `fake` rule names
  exists, so a typo stops the sync before the import. That is one more query
  per table over the shared ssh connection.
- **Identifiers are user input.** Table and column names go through the same
  identifier check the other catalog operations use (`catalog.py::_identifier`);
  values are generated, never interpolated from config.
- **`profiles/VERIFIED.md`** records, per profile and per rule, what has been
  proven against a real installation. Converting a profile's anonymize rule
  changes the rule, so its row's claims about that rule no longer hold. **The
  matrix is extended and run, not only adjusted** (decided): every converted
  profile's cell (`tests/matrix/`) asserts the new effect against a real
  install — no production value survives, the faked values have the expected
  shape and domain, `NULL` and empty values stay so, a second sync gives the
  same values — and at least one cell runs with `disable_seeding`,
  `disable_sanitize` and `disable_sanitize_all` to prove what each skips and
  what still runs. The affected `VERIFIED.md` rows are rewritten from those
  runs (`tools/apply_verified_rows.py`).
- Code, comments and docs in English; the test suite keeps its 100 % gate.

## How it looks

A rule in a project's `.ddev/ddev-sync.yaml`, as decided:

```yaml
sanitize:
  # disable_seeding: false    # true: mask instead of generating realistic values
  # disable_sanitize: false   # true: skip the personal-data rules only
  # disable_sanitize_all: false  # true: run no sanitize rule at all
  # email_domain: example.fake
  rules:
    - name: fake-reservations
      operation: fake
      table: reservations
      columns:
        name: name
        email: email
        comment: text
      email_domain: example.fake   # optional; overrides the project's
```

- `columns` maps a column to a kind, in a short form (`email: email`) or a
  long form with settings (`{ type: integer, min: 1, max: 10 }`).
- `key` names the column the values are derived from; default `id`. Any type
  works — integer or UUID.
- `tolerate_missing_table` works as it does for every rule.

Per-kind settings:

| Kind | Example | Settings |
|---|---|---|
| `name` | `James Carter` | — |
| `first_name` | `James` | — |
| `last_name` | `Carter` | — |
| `email` | `james.carter.3f2a@example.fake` | `email_domain` |
| `text` | `Lorem ipsum dolor sit amet, consectetur …` | `length` (a maximum, in characters) |
| `integer` | `7` | `min`, `max` (both required) |
| `zip` | `62704` | — |
| `city` | `Springfield` | — |
| `phone` | `+1 555 0142` | — |
| `ip` | `192.0.2.17`, `2001:db8::17` | `version: 4` (default) or `6` |

An unknown kind, an unknown setting, `integer` without `min`/`max`, or
`min > max` is a configuration error, raised when the rules are read — before
the dump, like the other rule errors.

## Masking (`disable_seeding: true`)

The rule still runs and still respects "empty stays empty"; it writes plain,
obviously generated values instead of realistic ones. These are my calls,
below; they only have to be unrecognisable, stable and type-safe:

| Kind | Masked value |
|---|---|
| `name`, `first_name`, `last_name` | `Name <hash>` / `First <hash>` / `Last <hash>` |
| `email` | `user.<hash>@<email_domain>` |
| `text` | `Lorem ipsum.` |
| `integer` | `min` |
| `zip` | `00000` |
| `city` | `City` |
| `phone` | `+1 555 0100` |
| `ip` | `192.0.2.0` / `2001:db8::` |

`<hash>` is the same short key-derived suffix the email uses.

## Personal-data rules and protective rules

Sanitize rules do two different jobs, and only one of them is what someone
turning sanitize off wants gone (decided):

- **Personal-data rules** make people unrecognisable: every `fake` rule, and
  any `sql` rule marked `personal_data: true`. The profiles mark their
  anonymize rules this way where they are not converted to `fake`.
- **Protective rules** are everything else, and they stop a local copy from
  acting on the real world or from being unusable: clearing Shopware's mailer
  settings, truncating TYPO3's scheduler tasks (which would otherwise send
  real mail), rewriting the site URL, creating the local admin, flushing the
  cache, clearing sessions and queues. **Resetting passwords is protective**
  (my call): a production password hash does not belong on a laptop even when
  someone wants to see real names.

An unmarked rule is protective, so it keeps running under `disable_sanitize`
(my call): a project that forgets the marker keeps its data hidden rather
than losing a protection by accident.

## `disable_sanitize` and `disable_sanitize_all`

`disable_sanitize: true` skips the personal-data rules and runs the rest.
`disable_sanitize_all: true` skips every rule. Either one leaves production's
personal data in the local database, so every sync it applies to says so on
its own warning line — through the reporter's existing warning path — naming
the setting and the file it came from; `disable_sanitize_all` additionally
says that the protective rules did not run either. `--dry-run` shows which
rules are skipped. Both are settings in the merged config, so they are
honoured from any layer; the documented place is the project file. Preflight
still checks the tables and columns of the rules that will run, and only of
those.

## Profiles

Each profile's anonymize rule becomes a `fake` rule over the same table,
faking the email column and, where the table has them, the name columns. The
password-reset rules stay as they are: a password hash is not something to
fake. **Login names are out of scope** (my call): unique username columns
(`users_field_data.name` in Drupal, WordPress's `user_login` and
`user_nicename`, TYPO3's `username`) keep their production values, as they do
today. Only display-name columns are faked; name kinds draw from a few
hundred names and are not unique, so faking a unique column with them would
fail the sync on a duplicate key. Which name columns each framework's user table has is left to the
builder to read from that framework's schema; where the profile does not
know a column for certain, it fakes only the email, as today.

## Left to the builder

- How the SQL derives a stable value: e.g. a hash of table, column and key
  (`CRC32`/`MD5`/`SHA2`) indexing into a seed list with `ELT`, one `UPDATE`
  per rule. Whatever is chosen must run on MariaDB 10.8+ and MySQL 8.
- The seed lists' size and content (a few hundred first and last names, a few
  dozen cities, a Lorem Ipsum word list), as long as they stay English and
  fictional.
- How long the email suffix is, as long as collisions are negligible for
  tables of production size.
- Whether `email` reuses the row's faked name when the same rule also fakes a
  name column.
- How `text` builds sentences within `length`.
- The exact wording of messages, and where the seed lists live in the source
  tree.
- Whether `fake` rules are listed in `--dry-run` with their columns.

## Calls made in writing this (not discussed)

- `first_name` and `last_name` as kinds of their own, beside `name`.
- `length` for `text`, `version` for `ip`, `key` defaulting to `id`.
- The masked values in the table above.
- The column existence check in preflight.
- `disable_sanitize` warns on every run.
- Phone numbers from the `555-01xx` block, IPs from the documentation ranges.
- Login names stay out of the profile conversion; the oktoberfest rule leaves
  `seating_request` alone.
- The `personal_data: true` marker, unmarked rules counting as protective, and
  password resets counting as protective.
