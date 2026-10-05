"""Bring a cell's stack up, talk to it, and run the engine against it.

Everything here shells out, which is why it lives under `tests/`: production
code reaches processes only through `transport/runner.py`.

The engine runs as a **container**, which is the point of the matrix. This is
the first place ddev-sync runs the way it will in production - inside a
container, on the project's network, reaching the database by its DDEV alias
and framework commands by `docker exec` into the web container. The
integration suite runs the engine on the host and has to generate mysql
wrapper scripts to compensate (see `tests/integration/conftest.py`); none of
that is needed here.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.matrix.cells import Cell
from tests.matrix.paths import HERE, KEYS_DIR, remote_dir, work_dir

COMPOSE_FILE = HERE / "docker-compose.yaml"
DOCKER_SOCKET = Path("/var/run/docker.sock")
# Where the engine container sees the throwaway ssh identity.
ENGINE_KEY = "/etc/ddev-sync-matrix/id_ed25519"


def _socket_group(image: str) -> str:
    """The gid that owns the docker socket, as a container sees it.

    The engine container has to `docker exec` into the web container, which
    means reading this socket, and a container user's group membership does
    not carry over from the host - the gid must be added explicitly.

    Asked of a container rather than read with `stat` on the host: the two
    disagree wherever the daemon runs in a VM. On Docker Desktop the host
    socket is `staff` (20) while the daemon's own is `root:root` (0660), so a
    host-side gid would grant nothing. On a Linux runner both are the
    `docker` group. This is the same requirement the real add-on's compose
    file will have - the matrix is simply the first place that has to satisfy
    it.
    """
    probe = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{DOCKER_SOCKET}:{DOCKER_SOCKET}",
            "--entrypoint",
            "stat",
            image,
            "-c",
            "%g",
            str(DOCKER_SOCKET),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return probe.stdout.strip()


def _engine_ssh_files() -> tuple[Path, Path]:
    """Write the `passwd` and `ssh_config` the engine container is given.

    `transport/ssh.py` builds a bare `ssh user@host`, trusting that the
    environment supplies an identity and a known-hosts policy - in
    production DDEV's agent forwarding does. A container has neither, and
    ssh additionally refuses to run as a uid with no passwd entry ("No user
    exists for uid"), which is exactly what `--user <host uid>` produces.
    So the harness supplies all three, as files mounted over `/etc`: a
    passwd line for the host uid, and a system-wide ssh config naming the
    key and switching host-key checking off. The stand-in remote regenerates
    its host key every time it is created, so a pinned key would only ever
    be stale; this is a throwaway host on a private network.
    """
    passwd = KEYS_DIR / "engine-passwd"
    passwd.write_text(
        f"root:x:0:0:root:/root:/bin/sh\nmatrix:x:{os.getuid()}:{os.getgid()}::/tmp:/bin/sh\n",
        encoding="utf-8",
    )
    ssh_config = KEYS_DIR / "engine-ssh_config"
    ssh_config.write_text(
        f"Host *\n"
        f"    IdentityFile {ENGINE_KEY}\n"
        "    IdentitiesOnly yes\n"
        "    StrictHostKeyChecking no\n"
        "    UserKnownHostsFile /dev/null\n"
        "    LogLevel ERROR\n",
        encoding="utf-8",
    )
    return passwd, ssh_config


def _env(cell: Cell) -> dict[str, str]:
    return {
        **os.environ,
        "MATRIX_CELL": cell.name,
        "MATRIX_PHP_IMAGE": cell.php_image,
        "MATRIX_PROJECT_DIR": str(work_dir(cell.name)),
        "MATRIX_KEYS_DIR": str(KEYS_DIR),
        "MATRIX_REMOTE_DIR": str(remote_dir(cell.name)),
        "MATRIX_REMOTE_PACKAGES": cell.remote_packages,
        "MATRIX_UID": str(os.getuid()),
        "MATRIX_GID": str(os.getgid()),
    }


@dataclass(frozen=True)
class Stack:
    """A running cell: its containers, its project, and how to drive them."""

    cell: Cell
    image: str

    def compose(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run a compose subcommand for this cell's stack.

        `stdin` exists for SQL that is too big to be an argument: a schema
        dump of a real TYPO3 or Drupal installation is a few hundred
        kilobytes, which is no size for an argument vector that also passes
        through docker's API and a shell.
        """
        return subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            env=_env(self.cell),
        )

    def down(self) -> None:
        """Stop and remove every container, the network and the volumes."""
        self.compose("down", "-v", "--remove-orphans")

    def web(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a command in the web container, where the framework lives."""
        return self.compose("exec", "-T", "web", *argv)

    def sql(self, query: str) -> str:
        """Query the cell's local database. `-N` strips the column headers.

        Raises on a failed query: several assertions read an empty result as
        "no rows", and a query that errored (a missing table, a typo) must
        not be able to pass for that.
        """
        result = self.compose(
            "exec",
            "-T",
            "local-db",
            "mariadb",
            "--user=db",
            "--password=db",
            "db",
            "-N",
            "-e",
            query,
        )
        if result.returncode != 0:
            raise RuntimeError(f"local query failed ({query!r}):\n{result.stderr}")
        return result.stdout.strip()

    def remote_sql(self, query: str) -> subprocess.CompletedProcess[str]:
        """Run one short query against the stand-in production database."""
        return self.compose(
            "exec",
            "-T",
            "remote-db",
            "mariadb",
            "--user=acme",
            "--password=s3cret",
            "acme_prod",
            "-N",
            "-e",
            query,
        )

    def remote_script(self, sql: str) -> subprocess.CompletedProcess[str]:
        """Feed a whole SQL script to the stand-in production database.

        Through stdin rather than `-e`: the schema this loads is dumped from
        a real framework installation and runs to several hundred kilobytes.
        """
        return self.compose(
            "exec",
            "-T",
            "remote-db",
            "mariadb",
            "--user=acme",
            "--password=s3cret",
            "acme_prod",
            stdin=sql,
        )

    def engine(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run the image under test against this cell's project.

        `--project-root` is explicit: the image's default is `/var/www/html`,
        DDEV's mount point, while a cell's project lives at its own path on
        this machine - and at the same path inside every container, which is
        what makes `docker exec -w` work.

        `--network` joins the cell's compose network, so
        `--host=ddev-<cell>-db` resolves and the ssh target is reachable by
        service name. The socket mount plus `--group-add` is what lets the
        engine exec into the web container.
        """
        project = work_dir(self.cell.name)
        network = f"ddev-sync-matrix-{self.cell.name}_default"
        passwd, ssh_config = _engine_ssh_files()
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--group-add",
                _socket_group(self.image),
                "-v",
                f"{DOCKER_SOCKET}:{DOCKER_SOCKET}",
                "-v",
                f"{project}:{project}",
                "-v",
                f"{KEYS_DIR / 'id_ed25519'}:{ENGINE_KEY}:ro",
                "-v",
                f"{passwd}:/etc/passwd:ro",
                "-v",
                f"{ssh_config}:/etc/ssh/ssh_config:ro",
                "-e",
                "HOME=/tmp",
                self.image,
                "--project-root",
                str(project),
                "--home",
                str(project / ".home"),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )


def up(cell: Cell, image: str) -> Stack:
    """Start `cell`'s stack and wait for every service to report healthy."""
    stack = Stack(cell=cell, image=image)
    stack.down()
    result = stack.compose("up", "-d", "--build", "--wait")
    if result.returncode != 0:
        stack.down()
        raise RuntimeError(f"{cell.name}: the stack did not come up:\n{result.stderr}")
    return stack
