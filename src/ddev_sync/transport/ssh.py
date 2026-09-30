"""Construct argument vectors for ssh, scp and rsync.

Nothing here executes anything. Commands are built as `list[str]` so that the
only place a string is ever handed to a shell is on the far side of ssh, where
`quote_remote` has already escaped every argument. The tool this replaces
interpolated paths and passwords straight into shell strings.
"""

import shlex
from collections.abc import Sequence

from ddev_sync.config.models import ResolvedTarget


def remote_endpoint(target: ResolvedTarget) -> str:
    """The `user@host` ssh destination, or the bare alias for alias targets."""
    if target.via_ssh_alias or target.user is None:
        return target.host
    return f"{target.user}@{target.host}"


def quote_remote(argv: Sequence[str]) -> str:
    """Join an argv into one shell-safe string for execution on the remote."""
    return " ".join(shlex.quote(part) for part in argv)


def ssh_command(target: ResolvedTarget, remote_command: Sequence[str]) -> list[str]:
    """Build `ssh [-J jump] [-p port] endpoint '<command>'`."""
    return [*_ssh_prefix(target), remote_endpoint(target), quote_remote(remote_command)]


def _ssh_prefix(target: ResolvedTarget) -> list[str]:
    prefix = ["ssh"]
    prefix.extend(_jump_flag(target))
    if target.port is not None:
        prefix.extend(["-p", str(target.port)])
    return prefix


def _jump_flag(target: ResolvedTarget) -> list[str]:
    if target.jump_host is None:
        return []
    endpoint = target.jump_host
    if target.jump_host_user is not None:
        endpoint = f"{target.jump_host_user}@{endpoint}"
    return ["-J", f"{endpoint}:{target.jump_host_port}"]


def scp_download_command(target: ResolvedTarget, remote_path: str, local_path: str) -> list[str]:
    """Build `scp [-J jump] [-P port] endpoint:remote local`."""
    command = ["scp"]
    command.extend(_jump_flag(target))
    if target.port is not None:
        command.extend(["-P", str(target.port)])
    command.append(f"{remote_endpoint(target)}:{remote_path}")
    command.append(local_path)
    return command


def rsync_pull_command(
    target: ResolvedTarget,
    remote_path: str,
    local_path: str,
    *,
    delete: bool = False,
) -> list[str]:
    """Build an rsync pull, tunnelling through the same ssh options."""
    command = ["rsync", "-a", "--info=progress2"]
    if delete:
        command.append("--delete")
    command.extend(["-e", _rsync_ssh_option(target)])
    command.append(f"{remote_endpoint(target)}:{remote_path}")
    command.append(local_path)
    return command


def _rsync_ssh_option(target: ResolvedTarget) -> str:
    parts = ["ssh", *_jump_flag(target)]
    if target.port is not None:
        parts.extend(["-p", str(target.port)])
    return " ".join(parts)
