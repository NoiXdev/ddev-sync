"""The first real connection through a jump host this project has made.

Until now `jump_host` was exercised only as an argv: five test files assert
the command line `transport/ssh.py` builds and none of them ever connected.
That matters beyond the field itself, because `provider: docker` resolves into
exactly this shape — the Docker host becomes the jump and the sync endpoint is
reached from it.

**What this proves, and what it does not.** It proves the hop: that `ssh -J`
reaches a second host and that an rsync pull works through it. It does *not*
prove the docker translation end to end — the stack's inner host answers as
`remote:2222` from the jump container, not as `127.0.0.1`, so a literal
`provider: docker` target would not match this topology. The translation is
pinned as argv in `tests/transport/test_ssh.py`, and run for real by the
matrix cell in plan 2.

rsync rather than a bare ssh call on purpose: `_rsync_ssh_option` builds its
own `-e` string and splits it on spaces, which is the one place the jump flag
could be mangled without any unit test noticing.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.transport.runner import SubprocessRunner
from ddev_sync.transport.ssh import rsync_pull_command

pytestmark = pytest.mark.integration

JUMP_PORT = "22023"
JUMP_HOST_ENTRY = f"[127.0.0.1]:{JUMP_PORT}"
# The inner host as the *jump* resolves it: a compose service name on the
# stack's own network, which is the whole point of a jump.
INNER_HOST = "remote"
INNER_PORT = 2222
# How ssh spells a host on a non-default port in `known_hosts`. Written out
# because `ssh-keygen -R remote` does *not* match `[remote]:2222`, which is a
# quiet way to leave residue behind and fail the next run with "REMOTE HOST
# IDENTIFICATION HAS CHANGED" - this test found exactly that about itself.
INNER_HOST_ENTRY = f"[{INNER_HOST}]:{INNER_PORT}"


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


@pytest.fixture
def known_hosts_for_both_hops(stack: None) -> Iterator[None]:
    """Trust both hops for this test only, then forget them.

    The suite's own `known_host` fixture covers the published remote; this
    connection meets two different identities — the jump on its published
    port, and the inner host under the name the jump resolves. Both are
    regenerated whenever a container is recreated, so an entry left behind
    would fail a later run as "REMOTE HOST IDENTIFICATION HAS CHANGED", which
    from ssh's point of view is indistinguishable from an attack. Scrubbed
    before as well as after, in case an interrupted run never reached its own
    cleanup.
    """
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.touch(exist_ok=True)
    for entry in (JUMP_HOST_ENTRY, INNER_HOST_ENTRY):
        _run(["ssh-keygen", "-R", entry, "-f", str(known_hosts)])
    try:
        yield
    finally:
        for entry in (JUMP_HOST_ENTRY, INNER_HOST_ENTRY):
            _run(["ssh-keygen", "-R", entry, "-f", str(known_hosts)])


def jumped_target() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host=INNER_HOST,
        user="deploy",
        port=INNER_PORT,
        jump_host="127.0.0.1",
        jump_host_user="deploy",
        jump_host_port=int(JUMP_PORT),
        path="/srv/app/plain/",
        stage=Stage.PRODUCTION,
    )


def test_a_pull_through_a_jump_host_brings_the_files_across(
    known_hosts_for_both_hops: None, tmp_path: Path
) -> None:
    destination = tmp_path / "pulled"
    destination.mkdir()

    result = SubprocessRunner().run(
        rsync_pull_command(jumped_target(), "/srv/app/plain/", str(destination))
    )

    assert result.ok, result.stderr
    assert sorted(path.name for path in destination.iterdir()), (
        f"nothing arrived through the jump host: {result.stderr}"
    )
