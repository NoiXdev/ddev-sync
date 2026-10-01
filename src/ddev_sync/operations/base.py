"""Shared context and the protocol every operation implements."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ddev_sync.config.merge import MergeResult
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import DdevSyncError
from ddev_sync.transport.runner import CommandRunner


class OperationError(DdevSyncError):
    """An operation could not complete."""


class PathEscapesProject(OperationError):
    """A configured local path resolves outside the project root."""


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

    def config(self, path: str, *, default: str) -> str:
        """Read a dotted path from the merged config, or return `default`."""
        node: Any = self.merged.data
        for part in path.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(part)
        return default if node is None else str(node)

    def config_list(self, path: str) -> list[dict[str, Any]]:
        """Read a dotted path expected to hold a list of mappings."""
        node: Any = self.merged.data
        for part in path.split("."):
            if not isinstance(node, dict):
                return []
            node = node.get(part)
        if not isinstance(node, list):
            return []
        return [entry for entry in node if isinstance(entry, dict)]

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

    The plan builder has no running project to inspect - `root` may not
    exist yet, and `--dry-run` must render identically wherever it runs.
    `resolve_local_path`'s `.resolve()` follows real symlinks, which makes it
    sensitive to details of the machine it runs on that have nothing to do
    with the configured value: on macOS, `/var` is a symlink to `/private/var`
    and a project rooted there would render as `/private/var/...`; on Linux
    it would not. A dry run is still useful protection against the thing
    spec §1.1 and this fix actually care about - a `files.paths` entry whose
    `local:` escapes the project root with a `../` - which plain lexical
    normalisation (`os.path.normpath`) catches exactly as well, with no
    filesystem access and no platform-dependent symlink surprises. The real
    run still goes through `resolve_local_path`/`ExecutionContext.local`,
    which remains the authoritative check.
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
