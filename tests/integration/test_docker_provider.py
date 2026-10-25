"""A real sync through the endpoint a stack would publish.

`provider: docker` resolves into a jump-host target (plan 1) and the far side
is the image this repository builds (task 1). Everything in between has been
proven separately: the translation as an argv, the image as a container, the
jump as a connection. This module is where the three meet - the engine on the
host, ProxyJump through the stand-in Docker host, and the endpoint answering
on that host's loopback address.

The topology models the real one exactly, which is what makes it worth the
runtime: the endpoint shares the jump's network namespace, so `127.0.0.1:22`
on the jump *is* the endpoint - the same thing a published port means - and
the endpoint has no address the host could reach directly. Nothing here can
accidentally bypass the hop.

Two known_hosts entries are involved, and both are only *scrubbed*: the
engine trusts each on first sight - the jump through the options its
`ProxyCommand` carries (see test_jump_host.py), the endpoint under its
`HostKeyAlias`, which is the thing two stacks on one Docker host depend on.
"""

import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ddev_sync.cli.main import main
from tests.integration.paths import PROJECT_DIR

pytestmark = pytest.mark.integration

HERE = Path(__file__).parent
PROFILE_ROOT = HERE.parent.parent / "profiles"
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yaml")]
COMPOSE_ENV = {**os.environ, "DDEV_SYNC_PROJECT_DIR": str(PROJECT_DIR)}

# The jump, as the engine's `host`/`port` name it: an ordinary SSH login on
# the machine the stack runs on.
JUMP_PORT = "22023"
JUMP_HOST_ENTRY = f"[127.0.0.1]:{JUMP_PORT}"
# The endpoint, as the stack publishes it on the jump's loopback address.
SYNC_PORT = "22"
# What `config/targets.py` builds for this target. Spelled out rather than
# imported so a change to the scheme is visible here as a diff: this string is
# what keeps two stacks on one Docker host apart in known_hosts.
ENDPOINT_ALIAS = f"ddev-sync-endpoint-127.0.0.1-{SYNC_PORT}"
# The volume the compose file gives the endpoint for its host key.
HOST_KEY_VOLUME = "ddev-sync-integration_endpoint-hostkeys"

TARGET = (
    "version: 1\n"
    "targets:\n"
    "  production:\n"
    "    provider: docker\n"
    "    host: 127.0.0.1\n"
    "    user: deploy\n"
    f"    port: {JUMP_PORT}\n"
    f"    sync_port: {SYNC_PORT}\n"
    "    path: /srv/app/plain/\n"
    "    stage: production\n"
)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, env=COMPOSE_ENV)


@pytest.fixture
def project(ssh_key: Path, stack: None, known_hosts_for: Callable[[str], None]) -> Iterator[Path]:
    """The same project as `test_end_to_end.py`, differing only in the target.

    Deliberately the same shape - TYPO3, the same remote path, the same stub -
    because the provider is what is under test here, and a project that
    differed in anything else would make a failure ambiguous.
    """
    # Only scrubbed, never registered: the engine records both keys on first
    # sight - the endpoint's under this alias - which is the behaviour under test.
    known_hosts_for(ENDPOINT_ALIAS)
    known_hosts_for(JUMP_HOST_ENTRY)
    root = PROJECT_DIR
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: typo3\ndocroot: public\n", encoding="utf-8"
    )
    (root / ".ddev" / "ddev-sync.yaml").write_text(TARGET, encoding="utf-8")
    (root / ".env").write_text("TYPO3_DB_NAME=placeholder\n", encoding="utf-8")
    # The sanitize step's `local-admin` rule execs this in the web container,
    # as in the sibling module; a stub is enough to prove the rule dispatches.
    (root / "vendor" / "bin").mkdir(parents=True)
    stub = root / "vendor" / "bin" / "typo3"
    stub.write_text('#!/bin/sh\necho "stub typo3: $*"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    yield root


def engine(project: Path, home: Path, *arguments: str) -> int:
    # Annotated rather than returned straight through: `main` is a click
    # entry point, so its return type reaches mypy as `Any`.
    code: int = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(home),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            *arguments,
        ]
    )
    return code


def _recorded(name: str) -> bool:
    """Whether `known_hosts` holds a key for `name`, hashed entries included.

    `ssh-keygen -F` is ssh's own lookup. A substring search would miss every
    line under `HashKnownHosts yes`, where the name is a salted hash - a
    setting common enough that this machine has it.
    """
    found = _run(["ssh-keygen", "-F", name, "-f", str(Path.home() / ".ssh" / "known_hosts")])
    return found.returncode == 0 and "ssh-" in found.stdout


def endpoint_host_key() -> str:
    """The endpoint's current public host key, read from its volume."""
    read = _run(
        [*COMPOSE, "exec", "-T", "endpoint", "cat", "/etc/ssh/keys/ssh_host_ed25519_key.pub"]
    )
    if read.returncode != 0:
        raise RuntimeError(f"could not read the endpoint's host key:\n{read.stderr}")
    return read.stdout.split()[1]


def local_query(sql: str) -> str:
    result = _run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "local-db",
            "mariadb",
            "--skip-ssl",
            "--user=db",
            "--password=db",
            "db",
            "-N",
            "-e",
            sql,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"local query failed ({sql!r}):\n{result.stderr}")
    return result.stdout.strip()


def test_the_doctor_finds_the_endpoint_the_path_and_its_mounts(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = engine(project, tmp_path / "home", "doctor")
    output = capsys.readouterr().out

    assert code == 0, output
    # The rows that plan 1 added for this provider, against a real endpoint
    # for the first time: the connection is named as the endpoint rather than
    # as the host that answered, and the path is the one inside the container.
    assert "sync endpoint" in output
    assert "path readable" in output
    assert "(in the endpoint)" in output


def test_a_pull_through_the_endpoint_brings_the_files_across(project: Path, tmp_path: Path) -> None:
    code = engine(project, tmp_path / "home", "--steps", "files", "--yes")

    assert code == 0
    # By name and by content: "something arrived" would also be true of an
    # empty directory rsync happened to create.
    brochure = project / "public" / "fileadmin" / "brochure.pdf"
    assert brochure.is_file()
    assert brochure.read_bytes().startswith(b"%PDF")


def test_a_dump_through_the_endpoint_reaches_the_database_by_service_name(
    project: Path, tmp_path: Path
) -> None:
    code = engine(project, tmp_path / "home", "--steps", "db", "--yes")

    assert code == 0
    # The endpoint carries no credentials of its own: these came from the
    # `.env` it mounts, and the host named in that file - a compose service
    # name - resolves because the endpoint sits inside the stack's network.
    # Both halves of the spec's claim, in one assertion.
    assert local_query("SELECT COUNT(*) FROM fe_users") == "2"


def test_a_path_the_session_uid_cannot_read_is_refused_before_anything_moves(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Root-owned and 0700 inside the endpoint, so the session - which runs as
    # the configured uid, not as root - sees a directory that exists and
    # yields nothing. This is the uid mismatch the design calls the likeliest
    # misconfiguration, and `docker compose exec` is root here, which is the
    # only way to create it.
    prepared = _run([*COMPOSE, "exec", "-T", "endpoint", "sh", "-c", "mkdir -p /opt/private"])
    assert prepared.returncode == 0, prepared.stderr
    _run([*COMPOSE, "exec", "-T", "endpoint", "sh", "-c", "chmod 700 /opt/private"])
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        TARGET.replace("path: /srv/app/plain/", "path: /opt/private/"), encoding="utf-8"
    )

    code = engine(project, tmp_path / "home", "--steps", "files", "--yes")
    output = capsys.readouterr().out

    assert code == 1, output
    assert "cannot be read" in output
    # The message has to name the setting that fixes it, and the setting is
    # the endpoint's uid - not compose's `user:`, which an endpoint cannot
    # take at all.
    assert "DDEV_SYNC_UID" in output


def test_the_alias_keeps_two_stacks_on_one_host_apart(
    project: Path, tmp_path: Path, known_hosts_for: Callable[[str], None]
) -> None:
    # Every docker target's endpoint lives at the Docker host's own loopback
    # address, so without the alias every stack on a host would share one
    # known_hosts entry and the second would be refused as a changed key.
    # Here is another stack's entry for that shared address, with a key that
    # is not this endpoint's.
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    known_hosts_for(f"[127.0.0.1]:{SYNC_PORT}")
    foreign = (
        f"[127.0.0.1]:{SYNC_PORT} ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIK7rN3dFvZKLfVPLf7FPQb0JG0lQ9EBhM1w2YXWcVgMa\n"
    )
    with known_hosts.open("a", encoding="utf-8") as handle:
        handle.write(foreign)

    code = engine(project, tmp_path / "home", "doctor")

    assert code == 0
    # Looked up with ssh's own tool, not by substring: `HashKnownHosts yes` is
    # a common setting (and this machine's), under which the name a line is
    # about is a salted hash rather than text.
    assert _recorded(ENDPOINT_ALIAS), "the engine recorded no entry for the alias"
    # The other stack's entry for the shared address is untouched, which is
    # the half that makes the alias worth having.
    assert foreign in known_hosts.read_text(encoding="utf-8")


def test_a_replaced_host_key_is_refused_and_says_where_it_came_from(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert engine(project, tmp_path / "home", "doctor") == 0, capsys.readouterr().out
    # The premise, stated rather than assumed: the engine trusted this
    # endpoint on first sight and wrote it down under the alias. Everything
    # below is about what happens when that record stops matching.
    assert _recorded(ENDPOINT_ALIAS), "nothing was recorded, so nothing can be refused"
    before = endpoint_host_key()

    # A redeployed stack that lost its host-key volume: the endpoint comes
    # back with a key nobody has seen, under an alias everybody trusts.
    _run([*COMPOSE, "rm", "-sf", "endpoint"])
    removed = _run(["docker", "volume", "rm", "-f", HOST_KEY_VOLUME])
    assert removed.returncode == 0, removed.stderr
    restarted = _run([*COMPOSE, "up", "-d", "--wait", "endpoint"])
    assert restarted.returncode == 0, restarted.stderr
    # Dropping the volume is only a premise if it actually rolls the key.
    assert endpoint_host_key() != before

    # A sync rather than the doctor: the doctor renders one clipped line per
    # row, which is the right shape for a table and the wrong one for advice,
    # so the explanation lives on the path that refuses to run.
    code = engine(project, tmp_path / "home", "--steps", "files", "--yes")
    output = capsys.readouterr().out

    # Unconditional, and measured to be safe to assert: a developer's
    # `~/.ssh/config` may well set `StrictHostKeyChecking no` - this machine's
    # does, and plan 1's review found a test that passed only because of it -
    # but the engine puts `accept-new` on the command line, and a command-line
    # `-o` wins over every config file. So the refusal does not depend on who
    # runs this.
    assert code == 1, output
    # `accept-new` refuses a changed key with a message about a possible
    # attack. For this provider the overwhelmingly likely cause is a dropped
    # volume, and the advice has to say so or the reader goes hunting.
    assert "host key" in output.lower()
    assert "volume" in output.lower()


@pytest.mark.parametrize("path", ["/usr/local/bin/ddev-sync-endpoint", "/etc/ssh/sshd_config"])
def test_the_endpoint_the_stack_runs_is_the_one_in_this_tree(
    endpoint_image: str, path: str
) -> None:
    # The stack builds the endpoint from `../../endpoint`, and this module's
    # claims are only about this tree's image if that is true. Compared by the
    # files' own identity rather than by a tag, which compose and the fixture
    # spell differently.
    #
    # Both files, not just the entrypoint: compose builds a service only when
    # its image is *missing*, so an edit to `sshd_config` alone used to leave
    # the stack running the previous image while every test here reported
    # green. `conftest.py` passes `--build` for that reason, and this is the
    # assertion that would notice if it stopped.
    built = _run(["docker", "run", "--rm", "--entrypoint", "sha256sum", endpoint_image, path])
    running = _run([*COMPOSE, "exec", "-T", "endpoint", "sha256sum", path])

    assert built.returncode == 0, built.stderr
    assert running.returncode == 0, running.stderr
    assert built.stdout.split()[0] == running.stdout.split()[0], (
        f"the stack is running a different {path} than this tree builds"
    )


def test_the_endpoint_says_which_host_key_it_started_with() -> None:
    logs = _run([*COMPOSE, "logs", "endpoint"])

    # One line either way, because a developer whose connection was just
    # refused as a changed key needs to know whether this container generated
    # a new one or reused the volume's.
    assert "generated a host key" in logs.stdout or "reusing the host key" in logs.stdout, (
        logs.stdout
    )
