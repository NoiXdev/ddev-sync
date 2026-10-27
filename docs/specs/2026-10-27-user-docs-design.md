# User documentation, docs links in errors, and a production README

Date: 2026-10-27. Status: draft for review.

## Why

ddev-sync is public and at rc.4, and its README points at a user
documentation section in `NoiXdev/noix-docs` that does not exist. Today a user
learns the tool from a long README, the comments in the example config and the
error messages. The errors are good prose but lead nowhere. There are about 220
raise sites, none of which carries a link.

What we want, in the user's words: write the docs, with compose examples and
error hints. Put every Docker tag in the docs with its current state. Make the
README ready for production: less text, badges, and links to the docs.

## Goals

1. A ddev-sync section on docs.noix.dev, in the shape the other products use
   (getting-started / guide / reference). It is in English, like every other
   section.
2. Four compose examples for the docker provider endpoint:
   - the endpoint inside the production stack;
   - the endpoint as a separate compose project beside a stack;
   - the endpoint behind a reverse proxy (Traefik, and a host proxy), with the
     port on loopback only;
   - several stacks on one host, each with its own endpoint and port.
3. A troubleshooting page: message → cause → fix, grouped by area. Each entry
   has a stable anchor.
4. Errors in the tool end with a link to their troubleshooting entry, so that
   whoever reads the error is one click from the explanation.
5. A reference page with every published image tag of `noixdev/ddev-sync` and
   `noixdev/ddev-sync-endpoint`, with its current state.
6. A short README with badges, a quick start and links into the docs. Detail
   moves to the docs.

## Non-goals

- No change to what any error *says*, only an added link line. Rewording
  messages is a separate task.
- No new CLI flags or config keys.
- No translated docs.
- No docs for contributors beyond what CONTRIBUTING.md already has.

## Anti-goals

- **Docs that claim behaviour the code does not have.** Every key, flag and
  default comes from the inventory, which was taken from the code. The keys
  that do not exist (`steps`, `jump`, `proxy`, `--no-color`, `--debug`) are
  not mentioned.
- **A link that 404s.** A broken docs link is worse than none.
- **A tags page that goes stale the day after a release.**
- **Public text with implementation details.** The README and the docs name
  what the user works with (DDEV, Docker, SSH, the supported frameworks), but
  not how the tool is built (language, libraries).

## The docs section

`src/content/docs/ddev-sync/`, registered as a topic in `astro.config.mjs`,
with a hub card in `index.mdx` and an entry in the docs README, as the Printy
commit did.

- **getting-started**
  - `introduction`: what it does, the flow, what it never does to the remote
  - `installation`: requirements, add-on install, `ddev auth ssh`, restart
  - `first-sync`: `ddev sync init`, `doctor`, a dry run, a first sync
  - `updating`: add-on update, pinned versions, `.env.local` overrides
- **guide**
  - `targets`: the server target, layers, where targets belong, `ssh_alias`, jump host
  - `deployer`: reading the connection from Deployer
  - `docker-endpoint`: the docker provider, with the four compose examples
  - `syncing`: steps, `--steps`, `--yes`, `--dry-run`, verbosity, `build`, features
  - `sanitizing`: profile rules, own rules, `extend`/`replace`, the `fake`
    operation and its kinds, `email_domain`, the three switches, `personal_data`
  - `frameworks`: per profile, what it does, what it needs (credentials source,
    `production_url`), and its verified state
- **reference**
  - `configuration`: every key, its default and valid values; the merge order
    of the layers
  - `cli`: every command and flag, with exit codes
  - `docker-images`: the tags (see below)
  - `troubleshooting`: the error catalogue
  - `security`: what the tool can and cannot see or change, in user terms,
    taken from SECURITY.md
  - `changelog`: a link to the release notes

## Docs links in errors

- One registry in the engine, `src/ddev_sync/docs.py`, maps a topic slug to its
  URL: `https://docs.noix.dev/ddev-sync/reference/troubleshooting/#<slug>`.
- `DdevSyncError` gets an optional `doc` attribute. A raise site that has an
  entry passes its slug. Any other error falls back to its area's slug:
  config, connection, preflight, database, files, env, sanitize, deployer,
  docker endpoint, init. The area follows the exception class or the operation
  that raised it.
- The link is rendered in the two places errors reach the user: `main.py`'s
  handler and the reporter's `FAIL` block. It is one indented line after the
  message: `  More: <url>`. Doctor rows that fail carry the link of their row.
  `--help` output and success paths never carry one.
- The shell-side messages (`prepare-sidecar.sh`, the endpoint entrypoint) get
  their link written into the message text itself.
- **Keeping links and anchors in step.** The registry is the list of slugs. A
  unit test checks that every slug used at a raise site is in the registry. The
  troubleshooting page sets each anchor explicitly as `{#slug}`. The docs repo
  gets a small check that each slug exists as an anchor on the page, reading a
  copied slug list (see "Decided with the user").
- The first pass gives specific slugs to the errors a user is most likely to
  meet: host key changed, BatchMode refusal, fail2ban/rate limit, TLS
  certificate, missing table or column, Deployer not found, no ssh agent,
  endpoint UID/GID, no authorized keys, no TTY for init, malformed switch,
  missing `production_url`. That is about 30 entries. The rest link to their
  area.

## Docker tags page

Docker Hub has versioned tags only, and no `latest`. That itself goes on the
page: pin a version, and the add-on pins one for you.

The page shows live badges from shields.io for the newest version and the last
push of each image, so the headline never goes stale. Below them is a table of
every tag with its date, built at docs build time from the Docker Hub API. If
the API cannot be reached during the build, the table falls back to "see Docker
Hub" with a link, and the build does not fail.

## README

About 60 lines:
- a one-line pitch;
- badges: CI, latest release, the two Docker Hub images (version), license,
  DDEV add-on, Discord;
- install in three commands;
- a `ddev sync` example;
- the supported frameworks as one line;
- links to the docs sections;
- the security and contributing pointers;
- the license.

The stale Status block goes. The "What it syncs" table, the supported-types
detail, the dry-run sample and the Safety section move to the docs.
Development stays short and points at CONTRIBUTING.md.

## Decided with the user

1. The README links the NoiX Discord (`https://discord.gg/sHcvJfXhq`, the
   invite macSCP's README already uses), as a badge and in a "Help" line,
   next to the docs and the issue tracker.
2. The supported frameworks are named in the README and the docs: they are
   the product's scope.
3. The docs build fetches the tag list from Docker Hub, with the fallback
   described above.

My call: the docs repo checks a slug list copied from the engine's registry
and committed with the docs (`src/data/ddev-sync-doc-slugs.json`), rather than
fetching it from GitHub at build time - one network dependency in the build
(Docker Hub) is enough. The cost: a slug added to the engine must also be added
to that list and to the page, which the release checklist in CONTRIBUTING.md
will say.

## Left to the builder

The wording of every page, the order of sections within a page, which existing
README and example-config prose is reused, and which errors beyond the first
pass get their own entry.
