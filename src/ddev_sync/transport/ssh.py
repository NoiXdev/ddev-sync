"""Construct argument vectors for ssh, scp and rsync.

Nothing here executes anything. Commands are built as `list[str]` so that the
only place a string is ever handed to a shell is on the far side of ssh, where
`quote_remote` has already escaped every argument. The tool this replaces
interpolated paths and passwords straight into shell strings.
"""

import shlex
from collections.abc import Sequence

from ddev_sync.config.models import ResolvedTarget

# How long ssh may take to establish a connection before it gives up. Without
# one, an unreachable host costs the operating system's TCP timeout - about 75
# seconds on the machine this was found on - and `doctor` runs its probes one
# after another, so that wait is paid again for every probe. Ten seconds is
# the trade between the two ways to be wrong: a slow but reachable host (a
# loaded bastion, a satellite link) still completes a handshake well inside
# it, because a TCP connect needs one round trip plus a retransmit or two,
# while a user whose host is simply down sees the failure in the time it takes
# to read a sentence rather than watching nothing happen for over a minute.
CONNECT_TIMEOUT_SECONDS = 10

# `accept-new` and not `no`, and the difference is the whole point: it
# accepts a host ssh has never seen without asking, but still REFUSES a host
# whose key has changed - which is the signal a man-in-the-middle produces.
# `no` (usually paired with `UserKnownHostsFile=/dev/null`) switches that
# detection off entirely. For a tool that moves production databases and
# reads the production database's own credentials over the same channel,
# that would trade away the protection to save one confirmation prompt. Under
# the DDEV add-on `known_hosts` persists, so trust-on-first-use costs exactly
# one silent acceptance per host. Do not "simplify" this to `no`.
#
# One exception, and a real one: `-J` starts a second ssh for the jump host,
# and OpenSSH does not hand that one these `-o` options (checked on 10.3). The
# bastion's own key therefore follows the ssh default - ask - so a target
# behind a jump host, with an empty known_hosts and no terminal, still fails
# with "Host key verification failed." on the bastion's key. The documentation
# says so and names the workaround (an ssh_config entry for the bastion). The
# connect timeout is unaffected: the outer ssh gives up waiting for the banner.
SSH_CONNECTION_OPTIONS: tuple[str, ...] = (
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
)


def remote_endpoint(target: ResolvedTarget) -> str:
    """The `user@host` ssh destination, or the bare alias for alias targets."""
    if target.via_ssh_alias or target.user is None:
        return target.host
    return f"{target.user}@{target.host}"


def quote_remote(argv: Sequence[str]) -> str:
    """Join an argv into one shell-safe string for execution on the remote."""
    return " ".join(shlex.quote(part) for part in argv)


def ssh_command(target: ResolvedTarget, remote_command: Sequence[str]) -> list[str]:
    """Build `ssh [-o ...] [-J jump] [-p port] endpoint '<command>'`."""
    return [*_ssh_prefix(target), remote_endpoint(target), quote_remote(remote_command)]


def _ssh_prefix(target: ResolvedTarget) -> list[str]:
    prefix = ["ssh", *SSH_CONNECTION_OPTIONS, *_host_key_alias_flag(target)]
    prefix.extend(_jump_flag(target))
    if target.port is not None:
        prefix.extend(["-p", str(target.port)])
    return prefix


def _host_key_alias_flag(target: ResolvedTarget) -> list[str]:
    """Record this destination in `known_hosts` under a name of its own.

    For a target whose address is its name this is nothing. It exists for one
    whose address is shared - a Docker endpoint, which every stack reaches at
    the Docker host's own loopback - where without it the second stack is
    refused as a changed host key. Read as a field rather than decided here:
    the transport does not know what a provider is, and must not learn.
    """
    if target.host_key_alias is None:
        return []
    return ["-o", f"HostKeyAlias={target.host_key_alias}"]


def _jump_flag(target: ResolvedTarget) -> list[str]:
    if target.jump_host is None:
        return []
    endpoint = target.jump_host
    if target.jump_host_user is not None:
        endpoint = f"{target.jump_host_user}@{endpoint}"
    return ["-J", f"{endpoint}:{target.jump_host_port}"]


def scp_download_command(target: ResolvedTarget, remote_path: str, local_path: str) -> list[str]:
    """Build `scp [-o ...] [-J jump] [-P port] endpoint:remote local`."""
    command = ["scp", *SSH_CONNECTION_OPTIONS, *_host_key_alias_flag(target)]
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
    # rsync splits `-e` on spaces and ssh receives the pieces as separate
    # arguments, so every option above is written without a space inside its
    # value. Keep it that way: a value with a space would be torn in two.
    parts = ["ssh", *SSH_CONNECTION_OPTIONS, *_host_key_alias_flag(target), *_jump_flag(target)]
    if target.port is not None:
        parts.extend(["-p", str(target.port)])
    return " ".join(parts)
