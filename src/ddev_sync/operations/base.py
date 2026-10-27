"""Shared context and the protocol every operation implements."""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ddev_sync.config.merge import MergeResult
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import DdevSyncError, InterruptedAtPrompt
from ddev_sync.transport.runner import CommandRunner


class OperationError(DdevSyncError):
    """An operation could not complete."""


class PathEscapesProject(OperationError):
    """A configured local path resolves outside the project root."""


def confirm(question: str, *, subject: str) -> bool:
    """Ask `{question} [y/N]` and return whether the answer was yes.

    Shared by `operations/database.py::confirm_destructive` and
    `operations/env.py::confirm_env_change`, which used to each catch
    `EOFError` themselves - a faithful copy, identical in phrasing and
    structure, which is exactly the shape that drifts the next time one of
    them changes and the other does not. One function, called twice, cannot
    drift.

    `input()` raises `EOFError` when stdin is closed, which is exactly what
    a non-interactive invocation (a script, a cron job, CI) looks like. Left
    uncaught, that is a raw traceback at the one prompt that exists to stop
    a destructive action - after whatever already happened first (a
    downloaded production dump, a diffed env rewrite). `subject` names what
    could not be confirmed, so each call site's error reads as the same
    decision applied again, not two unrelated ones.

    Ctrl-C is re-raised as `InterruptedAtPrompt`. Every caller asks before
    doing its destructive part, so an interrupt here means that part has not
    started - which the executor could not otherwise tell from an interrupt in
    the middle of it, and would report as possible damage that did not happen.
    """
    try:
        # A blank line so the question separates from the step lines above it.
        # Inside the `try`, so a Ctrl-C arriving between the newline and the
        # prompt is still converted to `InterruptedAtPrompt`. Unlike the
        # pickers in `cli/prompts.py` there is no terminal guard here - a
        # closed stdin raises from `input()` below, after this newline has
        # already been written, which costs one blank line in a piped log and
        # is cheaper than a second way of detecting a terminal.
        print()
        answer = input(f"{question} [y/N] ").strip().lower()
    except KeyboardInterrupt as error:
        raise InterruptedAtPrompt from error
    except EOFError as error:
        raise OperationError(
            f"cannot confirm {subject}: no input is available (stdin is "
            "closed). Pass --yes to skip the confirmation prompt.",
            doc="command-line",
        ) from error
    return answer in {"y", "yes"}


def redact(text: str, secret: str) -> str:
    """Remove `secret` from `text`.

    Shared by `operations/database.py` (a failed `mysqldump`'s stderr) and
    `operations/preflight.py` (a failed `SHOW TABLES`'s stderr), which used
    to each carry an identical copy of this - the same risk `confirm` above
    already closed for a different pair of call sites: two faithful copies
    are exactly the shape that silently drifts the next time one changes and
    the other does not.

    An empty `secret` must be a no-op: `str.replace("", x)` would otherwise
    insert `x` between every character of the message, garbling it exactly
    when a caller most needs to read it - a target with no password (an
    empty string, not a missing one) is a real, if unusual, configuration.
    """
    return text.replace(secret, "<REDACTED>") if secret else text


def discard_progress(text: str) -> None:
    """Throw away a progress line, for a context nobody is watching.

    A module-level function rather than a lambda or `None`: it is the default
    of a dataclass field, so it has to be a single shared object, and a caller
    that wanted to silence progress explicitly can name it.
    """
    return None


@dataclass(frozen=True)
class ExecutionContext:
    """Everything an operation needs, and nothing it does not."""

    project: DdevProject
    target: ResolvedTarget
    merged: MergeResult
    runner: CommandRunner
    downloads_dir: Path
    assume_yes: bool = False
    keep_dump: bool = False
    # Mutable on purpose, despite the surrounding frozen dataclass: `frozen`
    # only blocks reassigning a field, not mutating what it points to, and
    # this needs exactly one scratch dict that lives as long as `context`
    # does. Two independent users share it by keying on their own string, not
    # by splitting it into typed fields:
    # - `operations/placeholders.py` memoises a placeholder resolver that
    #   shells out (e.g. `${WP_TABLE_PREFIX}`) so it runs at most once per
    #   sync even though dozens of sanitize rules each expand independently.
    # - `operations/database.py`'s `read_remote_credentials` memoises the
    #   `RemoteCredentials` it reads, since db.dump, the Part 3 preflight
    #   table probe and (for a `wp_cli` source) the WP_TABLE_PREFIX resolver
    #   can all ask for it in the same run - without this it would be up to
    #   three identical ssh round trips, or three wp-cli calls against
    #   production, for data that cannot change between them.
    # `Any`, not `str`, because the second use case caches a dataclass
    # instance, not a string. `compare=False` keeps two otherwise-identical
    # contexts equal regardless of what either has resolved and cached so far.
    cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    # Mutable for the same reason as `cache` above, and `compare=False` for the
    # same reason too. Whatever prints command output at `-vv` scrubs these
    # from every line, and it is handed this very set rather than a copy,
    # because the passwords are resolved during the run, after it was built.
    #
    # The local database password is deliberately absent. It is
    # `database.LOCAL_DB_PASSWORD`, the fixed value DDEV documents publicly,
    # and scrubbing the two characters `db` would mangle the database name,
    # the container host `ddev-<project>-db` and much else besides.
    secrets: set[str] = field(default_factory=set, repr=False, compare=False)
    # Decided at the CLI edge like the colour flag, never sniffed here: an
    # operation that called `sys.stdout.isatty()` itself would be untestable
    # without a pty, and would answer differently in the sidecar than on a
    # host.
    stdout_is_terminal: bool = False
    # Where a long step says what it is about to wait for. An operation calls
    # this and never asks whether anyone is listening: the default discards
    # the text, so `doctor`, the matrix cells and every test that builds a
    # context without one are unaffected. Who listens - and whether a
    # non-terminal run listens at all - is decided at the CLI edge, for the
    # same reason as the flag above. `compare=False` so that two otherwise
    # identical contexts stay equal when only their listener differs.
    progress: Callable[[str], None] = field(default=discard_progress, compare=False, repr=False)

    def setting(self, path: str) -> Any:
        """The raw value at a dotted path in the merged config, or `None`.

        `config` and `config_list` are this walk plus a coercion - a string
        with a default, and a list of mappings. A caller that needs to tell a
        string from a list, as `remote_env_candidates` does, can use neither,
        and a third private copy of the walk is how three readers of one
        default come to disagree (see `operations/env.py:146`).
        """
        node: Any = self.merged.data
        for part in path.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    def config(self, path: str, *, default: str) -> str:
        """Read a dotted path from the merged config, or return `default`."""
        value = self.setting(path)
        return default if value is None else str(value)

    def config_list(self, path: str) -> list[dict[str, Any]]:
        """Read a dotted path expected to hold a list of mappings."""
        value = self.setting(path)
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, dict)]

    def remote_data(self, relative: str) -> str:
        """Join `relative` onto the target's data root - user files, the env file."""
        return f"{self.target.data_root}{relative.lstrip('/')}"

    def remote_code(self, relative: str) -> str:
        """Join `relative` onto the target's code root - where framework CLIs run."""
        return f"{self.target.code_root}{relative.lstrip('/')}"

    def local(self, relative: str) -> Path:
        """Join `relative` onto the project root, refusing to escape it.

        rsync and mysql will write wherever they are told. Refusing here is
        cheaper than explaining to someone why their /etc got a fileadmin.
        """
        return resolve_local_path(self.project.root, relative)


def resolve_local_path(root: Path, relative: str) -> Path:
    """Join `relative` onto `root`, refusing to escape it.

    Used wherever a local path is about to be acted on for real (rsync's
    destination, mysql's dump path, a setup command's workdir): rsync and
    mysql will write wherever they are told, and refusing here is cheaper
    than explaining to someone why their /etc got a fileadmin. `.resolve()`
    follows real symlinks, which is what makes this the authoritative check -
    see `preview_local_path` for the dry-run's equivalent, which deliberately
    does not.
    """
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise PathEscapesProject(
            f"path {relative!r} resolves to {candidate}, which is outside "
            f"the project root {resolved_root}"
        )
    return candidate


def preview_local_path(root: Path, relative: str) -> Path:
    """Join `relative` onto `root` for the *dry-run preview only*, applying
    the same escape check as `resolve_local_path` without touching the
    filesystem.

    `root` is already canonical here - `read_ddev_project` resolves it - so
    the preview renders the resolved project root on every platform. What
    `resolve_local_path`'s `.resolve()` would add on top is following a
    symlink *inside* the configured `local:` suffix, which makes the result
    depend on what happens to be on this machine's disk rather than on the
    configured value: a dry run would then show a target the profile never
    wrote, and one that differs between machines. A dry run is still useful
    protection against the thing spec §1.1 and this fix actually care about -
    a `files.paths` entry whose `local:` escapes the project root with a
    `../` - which plain lexical normalisation (`os.path.normpath`) catches
    exactly as well, with no filesystem access and no symlink surprises. The
    real run still goes through `resolve_local_path`/`ExecutionContext.local`,
    which remains the authoritative check, and which does follow such a
    symlink.
    """
    normalized_root = Path(os.path.normpath(root))
    candidate = Path(os.path.normpath(normalized_root / relative))
    if candidate != normalized_root and normalized_root not in candidate.parents:
        raise PathEscapesProject(
            f"path {relative!r} resolves to {candidate}, which is outside "
            f"the project root {normalized_root}"
        )
    return candidate


class Operation(Protocol):
    """One unit of work, parameterised from a profile or a hook."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Perform the work, raising OperationError on failure."""
