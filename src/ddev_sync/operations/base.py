"""Shared context and the protocol every operation implements."""

from collections.abc import Mapping
from dataclasses import dataclass
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

    def remote(self, relative: str) -> str:
        """Join `relative` onto the target's deploy path."""
        return f"{self.target.path.rstrip('/')}/{relative.lstrip('/')}"

    def local(self, relative: str) -> Path:
        """Join `relative` onto the project root, refusing to escape it.

        rsync and mysql will write wherever they are told. Refusing here is
        cheaper than explaining to someone why their /etc got a fileadmin.
        """
        root = self.project.root.resolve()
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise PathEscapesProject(
                f"path {relative!r} resolves to {candidate}, which is outside "
                f"the project root {root}"
            )
        return candidate


class Operation(Protocol):
    """One unit of work, parameterised from a profile or a hook."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Perform the work, raising OperationError on failure."""
