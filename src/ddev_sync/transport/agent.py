"""Which socket of DDEV's ssh agent this process talks to.

The add-on points `SSH_AUTH_SOCK` at the agent's `proxy-socket`, which is what
DDEV v1.25.4 served to a non-root user. DDEV v1.25.5 serves `socket` instead and
leaves the old `proxy-socket` behind in the volume: there, refusing every
connection. A file that exists says nothing, so each candidate is connected to.
"""

import contextlib
import socket
from collections.abc import MutableMapping
from pathlib import Path

# In the order they are tried after the configured one.
AGENT_SOCKETS = ("proxy-socket", "socket")


def answers(path: str) -> bool:
    """Whether something accepts a connection on the unix socket at `path`."""
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as client:
        client.settimeout(2)
        try:
            client.connect(path)
        except OSError:
            return False
    return True


def use_an_answering_agent(environ: MutableMapping[str, str]) -> None:
    """Point `SSH_AUTH_SOCK` at a socket beside the configured one if that one is dead.

    Only siblings in the same directory, and only by DDEV's names: this repairs
    the add-on's own wiring and does not go looking for agents. Left alone when
    nothing answers, so that `doctor` names the socket that was configured.
    """
    configured = environ.get("SSH_AUTH_SOCK", "")
    if not configured or answers(configured):
        return
    directory = Path(configured).parent
    for name in AGENT_SOCKETS:
        candidate = str(directory / name)
        if candidate != configured and answers(candidate):
            environ["SSH_AUTH_SOCK"] = candidate
            return
