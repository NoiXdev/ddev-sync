"""Rewrite the local environment file so the project points at DDEV.

`patch_keys` replaces values in place. The tool this replaces appended a marked
block on every run, so a file synced ten times carried ten blocks and the last
one silently won. In-place replacement is idempotent and shows up in a diff as
the change it actually is.

`env.pull` adds values that are secrets, so they have their own write path and
their own way through the confirmation diff. They are deliberately not added to
`context.secrets`: nothing here hands one to a command, the remote read is
`sensitive`, and scrubbing a value like `true` or `1` would mangle `-vv` output.
"""

import difflib
import os
import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from ddev_sync.config.env_pull import names_to_read, pull_names
from ddev_sync.operations.base import ExecutionContext, OperationError, confirm
from ddev_sync.operations.env_pull import (
    EXAMPLE_FILE,
    Assignment,
    logical_assignments,
    read_remote_values,
)

# Re-exported (not just used internally) so `tests/operations/test_env.py`'s
# existing `from ddev_sync.operations.env import expand_placeholders` keeps
# working: the substitution logic itself now lives in `placeholders.py`,
# which both this module and `sanitize.py` depend on, rather than either of
# them depending on the other.
from ddev_sync.operations.placeholders import expand_placeholders as expand_placeholders

BLOCK_START = "###################### BEGIN DDEV SYNC OVERRIDES ######################"
BLOCK_END = "####################### END DDEV SYNC OVERRIDES #######################"

STRATEGIES = ("patch_keys", "append_block", "none")

_ASSIGNMENT = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<gap>\s*=\s*)(?P<value>.*)$")

# Presence, not rewriting - see `declared_keys` for why the two are
# deliberately different regexes. Every form here is one phpdotenv's own
# parser accepts as a declaration, read from
# `vendor/vlucas/phpdotenv/src/Parser/EntryParser.php` in the kept
# `mxfeathorizon` fixture (v5, shipped with laravel/framework v13.35.0):
# `splitStringIntoParts` trims " \n\r\t\0\x0B" from both sides of the name
# (:75), so an indented assignment is one; `parseName` strips a leading
# `export` followed by whitespace (:99-101) and then a matching pair of
# quotes around the name (:103-105). `Lines::isCommentOrWhitespace` drops a
# line whose first non-blank character is `#`, so an indented comment is not
# a declaration here either.
_DECLARATION = re.compile(
    r"^[ \t]*(?:export[ \t]+)?['\"]?(?P<key>[A-Za-z_][A-Za-z0-9_]*)['\"]?\s*="
)

_LINE_END = re.compile(r"\r?\n")


def _lines(text: str) -> list[str]:
    """The lines of an env file, as many as a loader sees.

    Not `str.splitlines`, which also ends a line at a form feed, a `\\x85` or a
    `\\u2028`. Those are ordinary characters to phpdotenv and can be part of a
    pulled value: split there, the second half of a value would be a line of its
    own, which no rewrite replaces and no mask hides.
    """
    lines = _LINE_END.split(text)
    if lines[-1] == "":
        lines.pop()
    return lines


def patch_keys(text: str, values: Mapping[str, str]) -> str:
    """Replace each key's value in place, in *every* line that assigns it;
    append keys that are not present.

    Every occurrence, not the first: a `.env` may assign one key twice, and
    phpdotenv takes the **last** assignment in a file (`ImmutableWriter::write`
    refuses only a name that is `isExternallyDefined`, which is false once the
    name is in `$this->loaded`). Rewriting the first and passing the second
    through left the loader reading the value the sync had just decided was
    wrong - so `DB_HOST` and the empty URL keys, which exist to stop a synced
    project reaching production, were defeated by a duplicate line. That is
    what makes "first only" unsafe rather than merely untidy. The most
    plausible way a `.env` acquires a duplicate is the predecessor tool this
    module's own docstring describes, which appended a marked block on every
    run.
    """
    remaining = dict(values)
    lines = _lines(text)
    patched: list[str] = []

    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match is None or match.group("key") not in values:
            patched.append(line)
            continue
        key = match.group("key")
        remaining.pop(key, None)
        patched.append(f"{key}{match.group('gap')}{_requote(match.group('value'), values[key])}")

    patched.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(patched) + "\n" if patched else ""


def _parts(lines: Sequence[str], names: Collection[str]) -> list[tuple[Assignment | None, str]]:
    """The lines of a file, each assignment of one of `names` as one part, whole.

    Everything else stays a line of its own, with no assignment beside it, so a
    line inside another name's multi-line value is never taken for one of `names`.
    """
    parts: list[tuple[Assignment | None, str]] = []
    position = 0
    for entry in logical_assignments(lines):
        if entry.name in names:
            parts.extend((None, line) for line in lines[position : entry.first])
            parts.append((entry, "\n".join(lines[entry.first : entry.last + 1])))
            position = entry.last + 1
    parts.extend((None, line) for line in lines[position:])
    return parts


def patch_pulled(text: str, values: Mapping[str, str]) -> str:
    """Put each pulled right-hand side on every assignment of its name, or append it.

    `values` are ready to write, quotes included, so unlike `patch_keys` this
    does not `_requote` and replaces whatever stood behind the `=`, a comment
    too. Every assignment for the reason `patch_keys` gives, and all of it: left
    behind, the continuation lines of a multi-line value are a line phpdotenv
    refuses, so the application would stop at boot and no later run would heal it.
    A name found only inside another name's value is absent. A file nothing
    changes in comes back as it was, because normalising its last newline would
    be a change with no line to show in the diff, and a prompt over nothing.
    """
    lines = _lines(text)
    parts = _parts(lines, values)
    patched = [
        real if entry is None else f"{entry.head}{values[entry.name]}" for entry, real in parts
    ]
    assigned = {entry.name for entry, _ in parts if entry is not None}
    patched.extend(f"{key}={value}" for key, value in values.items() if key not in assigned)
    return text if patched == lines else "\n".join(patched) + "\n"


def mask_pulled(name: str, before: str, after: str, values: Mapping[str, str]) -> str:
    """The diff between two texts, with every assignment of `values`' names as `NAME=***`.

    It takes the texts rather than a diff, because a hunk cannot tell whether it
    starts inside a quote. The diff runs over the real text, with an assignment as
    one element however many lines it spans, and only its output is replaced: so a
    changed value still shows as `-A=***` and `+A=***`, and one that did not change
    as a context line, which the diff prints around a change and which is still a
    secret. With no `values` it is the plain diff.

    The values are what `patch_pulled` was given, because the line it appends for one
    need not read as an assignment: behind a quote never closed, a value holding that
    quote makes the line that open value's closing line.
    """
    old, new = _parts(_lines(before), values), _parts(_lines(after), values)
    shown = {f"{key}={value}": f"{key}=***" for key, value in values.items()}
    shown |= {real: f"{entry.name}=***" for entry, real in (*old, *new) if entry is not None}
    lines = difflib.unified_diff(
        [real for _, real in old],
        [real for _, real in new],
        fromfile=name,
        tofile=f"{name} (new)",
        lineterm="",
    )
    return "\n".join(f"{line[0]}{shown.get(line[1:], line[1:])}" for line in lines)


def declared_keys(text: str) -> set[str]:
    """Every key an env file declares, by the widest rule a loader would use.

    `_DECLARATION`, not `_ASSIGNMENT`, and the asymmetry is the point rather
    than an oversight. The two regexes answer two different questions:

    - *Presence* decides whether a default is written at all, so it must be
      generous: anything a dotenv loader treats as a declaration. A key this
      function misses gets a default appended beside the line the developer
      wrote, and phpdotenv takes the **last** assignment in a file - within
      one file `ImmutableWriter::write` only refuses a name that is
      `isExternallyDefined`, which is false once the name is in `$this->loaded`
      (`ImmutableWriter.php:53-59` and `:106-109` in the kept `mxfeathorizon`
      fixture), so immutability protects variables defined *before* the load
      and not earlier lines of the same file. So the appended default wins:
      `export QUEUE_CONNECTION=redis` plus an appended `QUEUE_CONNECTION=sync`
      resolves to `sync`, which is exactly the Horizon regression `env.default`
      exists to remove.
    - *Rewriting* must be exact, which is why `patch_keys` keeps the narrow
      `_ASSIGNMENT`: rewriting `export DB_HOST=prod` in place would have to
      either reproduce the `export` prefix and the indent the developer chose
      or silently drop them, and dropping `export` changes what a shell that
      sources the file does. It does not have to: an *imposed* key whose only
      declaration is a form `_ASSIGNMENT` misses is appended at the end of the
      file instead, and by the same last-wins rule above the appended line is
      the one that decides. So the pin holds; what it costs is one duplicate
      line in the file, visible in the confirmation diff.

    A key `patch_keys` cannot rewrite in place is therefore still declared and
    must not receive a default. A commented line is not a declaration - an
    indented one is not either, matching `Lines::isCommentOrWhitespace` - and
    an empty value is one: `REDIS_PASSWORD=` means "no password", not
    "unconfigured".
    """
    found: set[str] = set()
    for line in _lines(text):
        match = _DECLARATION.match(line)
        if match is not None:
            found.add(match.group("key"))
    return found


def _requote(original: str, replacement: str) -> str:
    stripped = original.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return f"{stripped[0]}{replacement}{stripped[0]}"
    return replacement


def append_block(text: str, values: Mapping[str, str]) -> str:
    """Replace the marked override block, or append it when absent."""
    body = "\n".join(f"{key}={value}" for key, value in values.items())
    block = f"{BLOCK_START}\n{body}\n{BLOCK_END}"
    if BLOCK_START in text and BLOCK_END in text:
        head, _, rest = text.partition(BLOCK_START)
        _, _, tail = rest.partition(BLOCK_END)
        return f"{head}{block}{tail}"
    separator = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{separator}\n{block}\n"


def confirm_env_change(diff: str) -> bool:
    """Show what will change in the env file and ask before writing.

    Module-level, like `confirm_destructive`, so tests replace it by dotted
    string rather than constructing a console. Delegates to
    `operations/base.py::confirm` - see that function for why `EOFError` is
    handled there rather than here, and for why this and
    `confirm_destructive` (operations/database.py) share one implementation
    instead of each catching it themselves.
    """
    print(diff)
    print(
        "Check this before continuing - a local app still pointed at production "
        "is the failure this prompt exists to prevent."
    )
    return confirm("Apply these changes?", subject="the env file change")


class EnvRewriteOperation:
    """Points the local environment file at DDEV."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Apply `env.set`, `env.pull` and `env.default`, in that order of precedence."""
        node = context.merged.data.get("env", {})
        settings: dict[str, Any] = node if isinstance(node, dict) else {}
        strategy = str(settings.get("strategy", "patch_keys"))
        if strategy == "none":
            return
        if strategy not in STRATEGIES:
            raise OperationError(
                f"unknown env strategy {strategy!r}; expected one of {', '.join(STRATEGIES)}"
            )

        raw_set = settings.get("set", {})
        raw_default = settings.get("default", {})
        # A non-mapping block is no block here, never an error - a deliberate
        # divergence from `profiles/schema.py::_validate_env`, which raises
        # ProfileError for the same input. This reads merged configuration and
        # therefore a project's own `ddev-sync.yaml`, which no validator ever
        # sees, and raising here would turn a mistake in a file the user
        # controls into a failed sync *mid-run*, after the database import. A
        # malformed `env.default` is refused earlier instead, at plan time, by
        # `engine/plan.py::_env`, which runs before anything is written; by the
        # time this runs, one has already been rejected. A non-mapping `set` is
        # still tolerated in all of `plan.py`, `cli/init.py::_missing_env_file`
        # and here.
        imposed = raw_set if isinstance(raw_set, dict) else {}
        defaulted = raw_default if isinstance(raw_default, dict) else {}
        # Past the planner, which refuses bad names and any strategy but
        # `patch_keys` beside them, and past preflight, which refuses a name the
        # remote cannot give: `env.pull` rides with the same group as `db.dump`.
        names = pull_names(context.merged.data)
        if not imposed and not defaulted and not names:
            return
        if defaulted and strategy == "append_block":
            # `append_block` writes a block rather than reconciling keys, so
            # "only if absent" has nowhere to land. `profiles/schema.py`
            # rejects this in a profile; this catches a project layer, which
            # that validator never sees.
            raise OperationError("env.default cannot be used with strategy 'append_block'")

        to_create = self._file_to_create(context, settings) if names else None
        if to_create is None:
            path = self._find_env_file(context, settings)
            original = start = path.read_text(encoding="utf-8")
        else:
            # Empty, not the example: the diff shows the file as new, and it is
            # what is compared, so a file to create is always a change.
            path, start = to_create
            original = ""
        values = expand_placeholders(
            {str(key): str(value) for key, value in imposed.items()}, context
        )
        pulled = _read_pulled(context, names_to_read(context.merged.data))
        if defaulted:
            # Across every candidate that exists, not just the written file:
            # a key declared in `.env` while `.env.local` is written would
            # otherwise be overridden through Dotenv's overlay. `set` wins a
            # collision, because it is the stronger statement and a profile may
            # inherit one of the two from a base. A pulled name counts as
            # declared, and so does what the example about to be copied declares.
            declared = (
                self._declared_across_candidates(context, settings)
                | declared_keys(start)
                | set(pulled)
            )
            absent = {
                str(key): str(value)
                for key, value in defaulted.items()
                if str(key) not in declared and str(key) not in values
            }
            values = {**values, **expand_placeholders(absent, context)}
        rewritten = patch_pulled(start, pulled)
        if values:
            # Not an optimisation: `patch_keys(text, {})` still normalises a
            # file with no trailing newline or with CRLF endings, so without
            # this check the step would prompt and write with nothing to change.
            rewritten = (
                patch_keys(rewritten, values)
                if strategy == "patch_keys"
                else append_block(rewritten, values)
            )
        if rewritten == original:
            return
        diff = mask_pulled(path.name, original, rewritten, pulled)
        if not context.assume_yes and not confirm_env_change(diff):
            raise OperationError("env rewrite cancelled by the user")
        if to_create is None:
            path.write_text(rewritten, encoding="utf-8")
        else:
            _create_file(path, rewritten)

    @staticmethod
    def _candidates_and_existing(
        context: ExecutionContext, settings: dict[str, Any]
    ) -> tuple[list[Any], set[str]]:
        """The `env.file` candidates, and the names of the files really in the project."""
        configured = settings.get("file", [".env.local", ".env"])
        # `list(configured)` cannot raise here although it would on, say, an
        # integer: `engine/plan.py::_env` is the gate, and it rejects anything
        # that is neither a string nor a list with a ConfigError before a plan
        # containing this step exists at all. Named explicitly because the
        # `[".env.local", ".env"]` default is read in several places with
        # different tolerances - that gate, this method and its callers
        # `_find_env_file`, `_declared_across_candidates` and `_file_to_create`,
        # `operations/env_pull.py::_local_file_problem`, which trusts the gate
        # exactly as this method does and has to agree with it on what is there,
        # `operations/database.py::remote_env_candidates`, which checks
        # `database.env_file` first, then falls back to this same `env.file`
        # key, before quietly falling back to the default itself, and
        # `cli/init.py::_missing_env_file`, which carries the same gate's
        # guard rather than this method's trust in it, because it runs
        # before any plan does - so "why is this one not defensive" is a
        # question worth answering in place rather than by searching. A reader
        # added later belongs in this list; the list deliberately does not
        # state its own length.
        candidates = [configured] if isinstance(configured, str) else list(configured)
        existing = {entry.name for entry in context.project.root.iterdir() if entry.is_file()}
        return candidates, existing

    @classmethod
    def _file_to_create(
        cls, context: ExecutionContext, settings: dict[str, Any]
    ) -> tuple[Path, str] | None:
        """Where a missing env file goes and the example's text it starts from.

        `None` when a candidate exists, or when there is no example, which
        preflight has refused by now and `_find_env_file` would refuse too. The
        first candidate always exists and is a plain name: the planner refuses an
        empty `env.file`, and one with a slash, beside `env.pull`.

        `context.local` is only the escape check here. It follows a symlink, and
        the path to create must not, or a dangling link's target gets created.
        """
        candidates, existing = cls._candidates_and_existing(context, settings)
        if EXAMPLE_FILE not in existing or any(str(name) in existing for name in candidates):
            return None
        first = str(candidates[0])
        context.local(first)
        example = context.local(EXAMPLE_FILE).read_text(encoding="utf-8")
        return context.project.root / first, example

    @classmethod
    def _find_env_file(cls, context: ExecutionContext, settings: dict[str, Any]) -> Path:
        """Find the first configured candidate that exists, by exact name.

        `Path.is_file()` folds case on a case-insensitive filesystem (the
        macOS default), which would let a probe for ".env" silently match a
        file the developer named ".ENV" and rewrite it without any hint of
        which file moved. Comparing against `iterdir()`'s literal directory
        entries instead makes the match exact - and thus this method's
        result identical - on every platform.

        Do not "simplify" this back to `context.local(name).is_file()` on a
        constructed path: that is exactly the probe that folds case. The
        `is_file()` call below is safe because it runs on an entry that
        `iterdir()` already produced, so its name is never a guess - it
        only excludes directories and other non-files from candidacy.
        """
        candidates, existing = cls._candidates_and_existing(context, settings)
        for name in candidates:
            if str(name) in existing:
                return context.local(str(name))
        looked = ", ".join(str(context.local(str(name))) for name in candidates)
        raise OperationError(
            f"no environment file found in the project; looked at: {looked}",
            doc="no-environment-file-in-the-project",
        )

    @classmethod
    def _declared_across_candidates(
        cls, context: ExecutionContext, settings: dict[str, Any]
    ) -> set[str]:
        """Every key declared by any candidate env file that exists.

        `_find_env_file` returns the one file the step writes. The presence
        test for `env.default` is deliberately wider: Symfony's Dotenv
        overlays `.env.local` on `.env`, so a default written into
        `.env.local` because `.env`'s declaration was not looked at would
        override the project's configured value - the exact surprise
        `env.default` exists to remove.
        """
        # Same candidate resolution and `iterdir()` probe as `_find_env_file`,
        # for the reason its docstring gives: `is_file()` on a constructed path
        # folds case on macOS. Do not replace the probe with one.
        candidates, existing = cls._candidates_and_existing(context, settings)
        declared: set[str] = set()
        for name in candidates:
            if str(name) in existing:
                declared |= declared_keys(context.local(str(name)).read_text(encoding="utf-8"))
        return declared


def _read_pulled(context: ExecutionContext, names: Sequence[str]) -> dict[str, str]:
    """The right-hand sides to write for `names`, with no remote read for no names.

    A value that refers to another variable gets a notice, by name only, because
    it can read differently from the remote if the other variable is not pulled.
    """
    if not names:
        return {}
    remote = read_remote_values(context, names)
    if remote.references:
        context.notice(
            "copied as written, refers to another variable and resolves against the "
            f"local file: {', '.join(remote.references)}"
        )
    return remote.text


def _create_file(path: Path, text: str) -> None:
    """Write a file that was not there, refusing whatever turns out to be.

    Exclusive, because the name was only compared against directory entries: on a
    case-insensitive filesystem `.env` opens a `.ENV` that comparison rightly did
    not take for it, and overwriting that would lose the developer's file. Only
    its owner can read it, because it holds production values.
    """
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as created:
            created.write(text)
    except OSError as error:
        raise OperationError(f"could not create {path}: {error.strerror}") from error
