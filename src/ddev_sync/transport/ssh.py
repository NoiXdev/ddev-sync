"""Construct argument vectors for ssh, scp and rsync.

Nothing here executes anything. Commands are built as `list[str]` so that the
only place a string is ever handed to a shell is on the far side of ssh, where
`quote_remote` has already escaped every argument. The tool this replaces
interpolated paths and passwords straight into shell strings.

The one thing here that touches the machine is `prepare_control_directory`,
which creates the directory the shared connection's socket lives in.
"""

import contextlib
import hashlib
import os
import shlex
import stat
from collections.abc import Sequence
from pathlib import Path

from ddev_sync.config.models import ResolvedTarget
from ddev_sync.errors import DdevSyncError

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
# `BatchMode=yes` because nobody is there to answer. The engine captures
# ssh's output, but ssh asks for a password, a key's passphrase or a second
# factor on `/dev/tty`, not on stdout - so with a terminal attached the
# question lands somewhere between the step lines and the run looks hung, and
# without one it fails anyway. With it, every such question becomes a refusal
# ssh reports at once ("Permission denied (publickey,password)"), which
# `doctor` can show. Keys come from the agent DDEV runs; one that needs typing
# is not something a sync can use.
#
# A jump host gets all of these too, which `-J` would not do: OpenSSH starts a
# second ssh for the bastion and does not hand it these `-o` options (checked
# on 10.3), so the bastion's key fell back to the default - ask - and a target
# behind one failed on an empty known_hosts. `_jump_flag` therefore spells the
# hop out as the `ProxyCommand` `-J` would have built, with the options added.
SSH_CONNECTION_OPTIONS: tuple[str, ...] = (
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "BatchMode=yes",
    "-o",
    f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
)


# One connection per target for the whole run, the way Deployer's own
# `ssh_multiplexing` does it: the first ssh becomes the master, and every
# later ssh, scp and rsync to the same target runs as a session inside it.
# A sync makes a dozen or more calls - the preflight alone makes three - and
# a server that rate-limits new SSH connections (fail2ban, CrowdSec, `ufw
# limit`) was measured cutting the third one off mid-session and then
# blocking port 22 for the source address. It is also simply faster: one key
# exchange instead of one per call.
#
# The master outlives the call that started it by `ControlPersist`, so the
# gaps between steps do not cost a new connection; an idle one closes by
# itself. A master that has died is noticed by the next client, which then
# connects directly and becomes the new master - so this can make a run
# faster, but never make it fail.
CONTROL_PERSIST_SECONDS = 60

# The socket's directory, as ssh spells it: ssh expands `%i` to the uid, so
# the argv stays the same text for every user while the directory does not.
# `/tmp` and not the home directory, because under the add-on the home is a
# bind mount from the host, and a unix socket on one is not something every
# Docker Desktop file-sharing backend supports. The directory must be private
# - see `prepare_control_directory` for why that is checked, not assumed.
CONTROL_DIRECTORY = "/tmp/ddev-sync-%i"


def control_directory() -> Path:
    """`CONTROL_DIRECTORY` with `%i` expanded the way ssh will expand it."""
    return Path(CONTROL_DIRECTORY.replace("%i", str(os.getuid())))


def prepare_control_directory() -> None:
    """Create the socket directory, or refuse one that is not ours alone.

    ssh does not create it: without it every call would warn and connect on
    its own, which is today's behaviour, not a failure. But `/tmp` is shared,
    and a master's socket is a door to the production shell behind it, so a
    directory another user created, or one others can enter, is refused
    rather than used.
    """
    directory = control_directory()
    with contextlib.suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise DdevSyncError(
            f"{directory} holds this user's shared ssh connections, but it is not a "
            "directory that only this user can enter. Remove it, and run the command again.",
            doc="shared-connection-directory",
        )


def _control_flags(target: ResolvedTarget) -> list[str]:
    """Share one connection between every call to `target` in this run.

    The socket is named after everything that decides where a connection
    lands, not after ssh's own `%C`: that hashes the local host, the target's
    host, port and user, and leaves out a `ProxyCommand`. Two Docker stacks
    are both `sync@127.0.0.1:2222`, behind different jump hosts, and under
    `%C` the second would be handed the first one's connection - a sync from
    the wrong server.
    """
    where = repr(
        (
            target.via_ssh_alias,
            target.user,
            target.host,
            target.port,
            target.jump_host_user,
            target.jump_host,
            target.jump_host_port,
            target.host_key_alias,
        )
    )
    key = hashlib.sha256(where.encode("utf-8")).hexdigest()[:16]
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPersist={CONTROL_PERSIST_SECONDS}",
        "-o",
        f"ControlPath={CONTROL_DIRECTORY}/{key}",
    ]


def remote_endpoint(target: ResolvedTarget) -> str:
    """The `user@host` ssh destination, or the bare alias for alias targets."""
    if target.via_ssh_alias or target.user is None:
        return target.host
    return f"{target.user}@{target.host}"


def quote_remote(argv: Sequence[str]) -> str:
    """Join an argv into one shell-safe string for execution on the remote."""
    return " ".join(shlex.quote(part) for part in argv)


def ssh_command(target: ResolvedTarget, remote_command: Sequence[str]) -> list[str]:
    """Build `ssh [-o ...] [-o ProxyCommand=<jump>] [-p port] endpoint '<command>'`."""
    return [*_ssh_prefix(target), remote_endpoint(target), quote_remote(remote_command)]


def _ssh_prefix(target: ResolvedTarget) -> list[str]:
    prefix = ["ssh", *_connection_flags(target)]
    prefix.extend(_jump_flag(target))
    if target.port is not None:
        prefix.extend(["-p", str(target.port)])
    return prefix


def _connection_flags(target: ResolvedTarget) -> list[str]:
    return [*SSH_CONNECTION_OPTIONS, *_control_flags(target), *_host_key_alias_flag(target)]


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
    """Reach the target through its jump host, with the same options as the target.

    The hop is the command OpenSSH's own `-J` runs - measured as
    `ssh -l deploy -p 22023 -W '[remote]:2222' 127.0.0.1` - plus
    `SSH_CONNECTION_OPTIONS`; see there for why `-J` itself will not do. ssh
    expands `%h` and `%p` to the target before it runs the command, and runs
    it through a shell, which is why the hop is quoted like a remote command.
    No `HostKeyAlias`: that names the target in `known_hosts`, and the bastion
    is recorded under its own name.
    """
    if target.jump_host is None:
        return []
    hop = ["ssh", *SSH_CONNECTION_OPTIONS]
    if target.jump_host_user is not None:
        hop.extend(["-l", target.jump_host_user])
    hop.extend(["-p", str(target.jump_host_port), "-W", "[%h]:%p", target.jump_host])
    return ["-o", f"ProxyCommand={quote_remote(hop)}"]


def scp_download_command(target: ResolvedTarget, remote_path: str, local_path: str) -> list[str]:
    """Build `scp [-o ...] [-o ProxyCommand=<jump>] [-P port] endpoint:remote local`."""
    command = ["scp", *_connection_flags(target)]
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
    parts = ["ssh", *_connection_flags(target), *_jump_flag(target)]
    if target.port is not None:
        parts.extend(["-p", str(target.port)])
    return " ".join(_rsync_word(part) for part in parts)


def _rsync_word(part: str) -> str:
    """`part` as one word of rsync's `-e`, which rsync splits itself.

    rsync splits `-e` on spaces and honours single and double quotes, with a
    doubled quote inside them standing for itself; it knows no backslashes and
    no shell runs in between. The jump host's `ProxyCommand` is the one value
    with spaces in it - and single quotes from `quote_remote` - so it goes in
    double quotes, and anything without either is left exactly as it was.
    """
    if not any(character in part for character in " '\""):
        return part
    return '"' + part.replace('"', '""') + '"'
