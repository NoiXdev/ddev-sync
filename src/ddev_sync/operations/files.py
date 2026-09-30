"""Pull user-generated files from the remote with rsync."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.transport.ssh import rsync_pull_command


@dataclass(frozen=True)
class FilePair:
    """One remote directory and where it lands locally."""

    remote: str
    local: Path
    delete: bool


def validate_path_pairs(context: ExecutionContext) -> list[FilePair]:
    """Resolve and check every configured path pair.

    Called before execution so that a path escaping the project root is caught
    while nothing has moved yet, rather than after rsync has already written
    somewhere it should not have.
    """
    return [_pair(context, entry) for entry in context.config_list("files.paths")]


def _pair(context: ExecutionContext, entry: Mapping[str, Any]) -> FilePair:
    remote = entry.get("remote")
    local = entry.get("local")
    if remote is None:
        raise OperationError(f"a files.paths entry is missing 'remote': {dict(entry)}")
    if local is None:
        raise OperationError(f"a files.paths entry is missing 'local': {dict(entry)}")
    return FilePair(
        remote=context.remote(str(remote)),
        local=context.local(str(local)),
        delete=bool(entry.get("delete", False)),
    )


class FilesRsyncOperation:
    """Pulls one configured directory from the remote."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Transfer the directory described by `params`."""
        pair = _pair(context, params)
        pair.local.mkdir(parents=True, exist_ok=True)
        argv = rsync_pull_command(
            context.target,
            pair.remote,
            f"{pair.local}/",
            delete=pair.delete,
        )
        result = context.runner.run(argv)
        if not result.ok:
            raise OperationError(
                f"rsync of {pair.remote} failed (exit {result.returncode}): {result.stderr.strip()}"
            )
