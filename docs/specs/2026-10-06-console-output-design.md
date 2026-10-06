# Console output: verbosity, colour, progress — design

**Status:** approved by the owner 2026-10-06, after a design review in
conversation. Written to be implemented as one pass.

## Why

Four requests from the owner, all made while watching a real sync, plus one
defect found beside them. They are one pass over the reporter and the prompt
layer rather than four patches, because they touch the same lines.

1. The default output is too informative. Every step prints its full command
   line twice — once on `..`, once on `OK`:

   ```
     ..  preflight     ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 ssg-kevelaer@web-node-1...
     OK  preflight     ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 ssg-kevelaer@web-node-1...
   ```

2. Colour exists for `doctor` only. `cli/colour.py` was built for it, and
   `doctor_command` already takes a `colour` flag decided at the edge; the
   sync path never uses either.

3. A prompt appears flush against the step line above it, so a question does
   not read as a question.

4. A long `files.sync` prints one `..` line and then nothing, which reads as a
   hang.

**The defect:** `reporter.py`'s `_MARKS` is
`{"ok": "OK  ", "failed": "FAIL", "skipped": "--  "}`. The first and last pad
themselves to four characters; `FAIL` fills the field exactly, so a failed
step prints `FAILdb.import` with no separator. Seen in the owner's own output.

## What is already true

Facts established by reading the code, not assumed:

- **rsync already asks for progress.** `transport/ssh.py::rsync_pull_command`
  passes `--info=progress2`. `SubprocessRunner.run` then captures stdout
  (`capture_output=True`), so the progress is generated and thrown away.
  Nothing is missing but a path to the terminal.
- **`paint_padded` already solves the column problem.** Its docstring is
  precisely about padding the plain text and wrapping the escapes around it,
  so a coloured cell keeps its width. The sync reporter encodes padding in the
  marker strings instead, which is what breaks `FAIL`.
- **The colour decision already happens at the edge.** `cli/main.py` computes
  `colour_enabled(os.environ, sys.stdout)` and hands the boolean to the
  renderer. The run path needs the same wiring, not a new mechanism.
- **There is no verbosity flag of any kind.** The name space is free.
- **`redact(text, secret)` removes one named value.** It is called in exactly
  two places, each with the password the operation just resolved. There is no
  general scrubber, which matters for the levels that print output.
- **Prompts come from two places, not one.** `cli/prompts.py` (`_select`,
  `_checkbox`) for the target and step pickers, and
  `operations/base.py::confirm` for the destructive confirmation — the one in
  the owner's transcript.
- **`ExecutionContext` carries a mutable `cache` field** declared
  `field(default_factory=dict, repr=False, compare=False)`, so a mutable
  companion field is the established pattern in that file.

## Decisions

Each was taken by the owner during the design conversation.

1. **Two lines per step at the default level**, as today, not one line
   rewritten in place. In-place needs cursor control, a second path for
   non-terminals, and it makes the output harder to assert.
2. **A fixed human description per step kind**, not a shortened command line.
   Predictable, and no host name or path can leak into a line that was meant
   to be quiet.
3. **The runner may pass output through** for the progress case, rather than
   growing a line-callback protocol.
4. **Three verbosity levels**, each adding to the one below.
5. **Green, red and yellow**, adding one code to `colour.py`.

## Design

### Step lines

`engine/steps.py` gains `STEP_DESCRIPTIONS: dict[StepKind, str]`, beside the
enum rather than in the reporter, so that a new step kind without a
description is a failure in the module that defines it. All nine kinds are
covered. The text is **English**, like every other string this tool prints;
this project has no i18n layer, and the mockups used in the design
conversation were German only for discussion.

| kind | description |
|---|---|
| `preflight` | `checking the connection, paths and free space` |
| `db.dump` | `dumping the remote database` |
| `db.download` | `fetching the dump` |
| `db.import` | `replacing the local database` |
| `env` | `updating the local env file` |
| `files.sync` | `syncing files from the remote` |
| `s3.sync` | `syncing the S3 bucket` |
| `sanitize` | `removing production data` |
| `setup` | `running the project's setup commands` |

Default output:

```
  ..  preflight     checking the connection, paths and free space
  OK  preflight
  ..  db.dump       dumping the remote database
  OK  db.dump
```

The `OK` line carries no description: it would repeat the line above it.

`PlannedStep.detail` is **not** touched. The dry run renders it through
`render_plan`, where the full command is the entire point.

### Verbosity

A counting click option, `-v/--verbose`, on the run command. Level 0 is the
default above.

- **`-v`** appends `detail` to both lines — exactly today's output.
- **`-vv`** additionally prints each step's command output, indented under the
  `OK`/`FAIL` line, scrubbed (see below). Today output is shown only for a
  failure, through `outcome.error`.
- **`-vvv`** additionally prints the full argument vector of every command and
  the wall-clock duration of each step.

Nothing is ever printed unscrubbed. The owner was offered a raw level and
declined it, so no level exists at which a production password can reach the
scrollback.

### Scrubbing, and why it needs a registry

`-vv` and `-vvv` print text that `redact` cannot help with on its own: it
removes one value that the caller must already hold, and the reporter holds
none.

`ExecutionContext` gains `secrets: set[str]`, declared the same way as
`cache` (`field(default_factory=set, repr=False, compare=False)`), so two
contexts stay equal regardless of what either has resolved. Whoever resolves a
remote credential adds its password — in practice
`database.py::read_remote_credentials`, which is the single funnel for all
three credential sources. The reporter is handed that set at construction and
scrubs every line it prints at any level.

**The local database password is deliberately not registered.** It is
`LOCAL_DB_PASSWORD = "db"`, a fixed value DDEV documents publicly, and
scrubbing the three-character string `db` would mangle the database name, the
container host `ddev-<project>-db`, and half the output besides. The spec
records this so that nobody later "fixes" the omission.

### Progress

`CommandRunner.run` gains `passthrough: bool = False`. When true the child's
stdout and stderr are inherited rather than captured, so rsync writes its own
progress to the terminal, and `CommandResult.stdout` and `.stderr` come back
empty. The protocol, `SubprocessRunner` and `FakeCommandRunner` all learn it;
the fake records it per invocation so a test can assert which calls used it.

Only `operations/files.py` passes it, and only when stdout is a terminal: in a
CI log rsync's `\r`-separated progress would produce thousands of lines. The
terminal check is made at the edge and handed down, like the colour flag —
never sniffed inside the operation.

This is the one change that weakens an otherwise strict boundary, so it is
documented where it is declared: a passthrough call cannot have its output
inspected, and a test asserts that the files step does not read it.

### Colour and the marker column

`colour.py` gains `YELLOW = "33"`. The reporter takes `colour: bool`,
supplied by `cli/main.py` from the existing
`colour_enabled(os.environ, sys.stdout)` — the same wiring `doctor` uses.

`_MARKS` loses its embedded padding (`"OK  "` becomes `"OK"`), and the format
gives the marker its own width through `paint_padded`. This fixes
`FAILdb.import` and is what `paint_padded` exists for.

- `OK` green, `FAIL` red, `--` (skipped) uncoloured.
- `!` notices yellow, including the warning that unsanitised production data
  is on the machine.

### Prompts

A blank line before every prompt, in both places: `cli/prompts.py`'s `_select`
and `_checkbox`, and `operations/base.py::confirm`. Missing either one leaves
half the questions flush against the line above, which is the complaint.

## Testing

- A snapshot of the step lines at each of the four levels.
- A test that every `StepKind` has an entry in `STEP_DESCRIPTIONS`, so a new
  kind cannot ship without one.
- A test that a registered secret does not appear in the output at `-vv` or
  `-vvv`, and that the literal `db` is not scrubbed.
- A test that `FAIL` is separated from the step name — the regression that
  prompted this.
- A test that `files.sync` passes `passthrough` only when told stdout is a
  terminal, and one that it does not read the result's stdout.
- Colour is passed in, never sniffed, so every renderer test stays a plain
  string comparison.

The project's gates apply unchanged: 100 % line and branch coverage, no
`# pragma: no cover`, ruff, mypy strict, and `subprocess` only in
`transport/runner.py`.

## Out of scope

- Progress for any step other than `files.sync`.
- A configuration key for the default verbosity.
- `--quiet`.
- Any change to `render_plan` or the dry run.
