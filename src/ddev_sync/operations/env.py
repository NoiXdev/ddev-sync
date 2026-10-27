"""Rewrite the local environment file so the project points at DDEV.

`patch_keys` replaces values in place. The tool this replaces appended a marked
block on every run, so a file synced ten times carried ten blocks and the last
one silently won. In-place replacement is idempotent and shows up in a diff as
the change it actually is.
"""

import difflib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError, confirm

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
    lines = text.splitlines()
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
    for line in text.splitlines():
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
        """Apply the profile's `env.set` values using its strategy."""
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
        if not imposed and not defaulted:
            return
        if defaulted and strategy == "append_block":
            # `append_block` writes a block rather than reconciling keys, so
            # "only if absent" has nowhere to land. `profiles/schema.py`
            # rejects this in a profile; this catches a project layer, which
            # that validator never sees.
            raise OperationError("env.default cannot be used with strategy 'append_block'")

        path = self._find_env_file(context, settings)
        original = path.read_text(encoding="utf-8")
        values = expand_placeholders(
            {str(key): str(value) for key, value in imposed.items()}, context
        )
        if defaulted:
            # Across every candidate that exists, not just the written file:
            # a key declared in `.env` while `.env.local` is written would
            # otherwise be overridden through Dotenv's overlay. `set` wins a
            # collision, because it is the stronger statement and a profile may
            # inherit one of the two from a base.
            declared = self._declared_across_candidates(context, settings)
            absent = {
                str(key): str(value)
                for key, value in defaulted.items()
                if str(key) not in declared and str(key) not in values
            }
            values = {**values, **expand_placeholders(absent, context)}
        if not values:
            # Not an optimisation: `patch_keys(text, {})` still normalises a
            # file with no trailing newline or with CRLF endings, so without
            # this return the step would prompt and write with nothing to change.
            return
        rewritten = (
            patch_keys(original, values)
            if strategy == "patch_keys"
            else append_block(original, values)
        )
        if rewritten == original:
            return
        if not context.assume_yes and not confirm_env_change(_diff(path.name, original, rewritten)):
            raise OperationError("env rewrite cancelled by the user")
        path.write_text(rewritten, encoding="utf-8")

    @staticmethod
    def _find_env_file(context: ExecutionContext, settings: dict[str, Any]) -> Path:
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
        configured = settings.get("file", [".env.local", ".env"])
        # `list(configured)` cannot raise here although it would on, say, an
        # integer: `engine/plan.py::_env` is the gate, and it rejects anything
        # that is neither a string nor a list with a ConfigError before a plan
        # containing this step exists at all. Named explicitly because the
        # `[".env.local", ".env"]` default is read in several places with
        # different tolerances - that gate, this method,
        # `_declared_across_candidates` below, which trusts the gate exactly as
        # this method does and reads the same `env.file` key to test presence,
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
        for name in candidates:
            if str(name) in existing:
                return context.local(str(name))
        looked = ", ".join(str(context.local(str(name))) for name in candidates)
        raise OperationError(
            f"no environment file found in the project; looked at: {looked}",
            doc="no-environment-file-in-the-project",
        )

    @staticmethod
    def _declared_across_candidates(
        context: ExecutionContext, settings: dict[str, Any]
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
        configured = settings.get("file", [".env.local", ".env"])
        candidates = [configured] if isinstance(configured, str) else list(configured)
        existing = {entry.name for entry in context.project.root.iterdir() if entry.is_file()}
        declared: set[str] = set()
        for name in candidates:
            if str(name) in existing:
                declared |= declared_keys(context.local(str(name)).read_text(encoding="utf-8"))
        return declared


def _diff(name: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=name,
        tofile=f"{name} (new)",
        lineterm="",
    )
    return "\n".join(lines)
