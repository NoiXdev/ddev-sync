# env.pull: profile defaults, env.extend and env.pull_all

## Intent

The owner wants a project to bring along more with less listing: a default list
per profile, a way to extend or replace it, and a switch that pulls everything.
`docs/specs/2026-11-04-env-pull-design.md` ruled "pull everything" out as an
anti-goal; this reverses that on the owner's decision, behind a switch that is
off by default.

## The settings

| Key | Layer | Default | Meaning |
|---|---|---|---|
| `env.pull_defaults` | profile | none | Names a profile pulls when the project lists none. |
| `env.pull` | project | not set | The project's list. Replaces the defaults; `[]` takes them away. |
| `env.extend` | project | `false` | `true` adds `env.pull` to the defaults instead. |
| `env.pull_all` | project | `false` | `true` pulls every name the remote has. |

`env.pull: all` is refused with a message naming `env.pull_all`: the owner named
both spellings and chose the switch.

## Decisions

The owner asked to skip the questions, so everything below is Claude's call.

- **Defaults shipped:** Laravel `APP_KEY`, Symfony and Shopware `APP_SECRET`, as
  what data in a synced database is encrypted or signed with. Other profiles
  have none: their settings do not live in a file the sync can write, or the
  name is not fixed.
- **Defaults are on without opting in**, as the owner described them. That is a
  change for existing projects on those profiles: the production key lands in
  the local file on the next database sync. The confirmation diff shows
  `NAME=***`, and `pull: []` turns it off.
- **Defaults are optional.** A project did not ask for them, so a sync that
  worked must keep working: a default the remote lacks or holds with a line
  break is a notice, and so is a remote with no environment file. They are also
  dropped under a strategy or `env.file` a pull cannot use. A name the project
  lists itself stays required, even if it is also a default.
- **`env.pull_all` leaves out** what a list would be refused for (the database
  names, `DDEV_SYNC_*`), what `env.set` keeps, a name that is no plain variable
  name, and what any container holds (`PATH`, `HOSTNAME` and so on, which
  `DDEV_SYNC_EXPORT_ENV: all` hands over). A value with a line break is skipped
  with a notice. A name listed in `env.pull` beside it stays required. No remote
  file is a refusal, since the project asked.
- **The plan warns** on every run with `env.pull_all`, and names no values.
- **`env.extend` covers `env.pull` only**, not `env.set` or `env.default`.
- **The merge is untouched.** Defaults are a key of their own, because a list
  directive decides between layers while `env.extend` is decided at read time.

## Not done

- A list of names to exclude from `env.pull_all`. `env.set` keeps a name local.
