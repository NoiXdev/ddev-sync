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

from collections.abc import Callable
from pathlib import Path

import pytest

from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.transport.runner import SubprocessRunner
from ddev_sync.transport.ssh import rsync_pull_command

pytestmark = pytest.mark.integration

JUMP_PORT = "22023"
# The inner host as the *jump* resolves it: a compose service name on the
# stack's own network, which is the whole point of a jump.
INNER_HOST = "remote"
INNER_PORT = 2222
# How ssh spells a host on a non-default port in `known_hosts`. Written out
# because `ssh-keygen -R remote` does *not* match `[remote]:2222`, which is a
# quiet way to leave residue behind and fail the next run with "REMOTE HOST
# IDENTIFICATION HAS CHANGED" - this test found exactly that about itself.
INNER_HOST_ENTRY = f"[{INNER_HOST}]:{INNER_PORT}"


@pytest.fixture
def both_hops(jump_host_key: None, known_hosts_for: Callable[[str], None]) -> None:
    """Trust both hops, which are not symmetrical.

    The jump's key has to be *registered*, because the ProxyJump child never
    sees the engine's options: it is exec'd as
    `ssh -l deploy -p 22023 -W '[remote]:2222' 127.0.0.1`, measured, so the
    outer `-o StrictHostKeyChecking=accept-new` does not reach it and the
    default policy applies. `jump_host_key` does that once per session, and
    says there why it must not be per test.

    The inner hop needs no such help: it is verified by the outer ssh, which
    does carry the engine's options - so its entry is only scrubbed, which is
    what keeps a regenerated container from failing the next run.
    """
    known_hosts_for(INNER_HOST_ENTRY)


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
    both_hops: None, tmp_path: Path
) -> None:
    destination = tmp_path / "pulled"
    destination.mkdir()

    result = SubprocessRunner().run(
        rsync_pull_command(jumped_target(), "/srv/app/plain/", str(destination))
    )

    assert result.ok, result.stderr
    # The fixture's own file, by name and by content: "something arrived" would
    # also be true of an empty directory listing that happened to be truthy.
    assert (destination / "public" / "fileadmin" / "brochure.pdf").is_file(), (
        f"nothing arrived through the jump host: {result.stderr}"
    )
