"""Build the argument vector that runs a command in the DDEV web container.

The sidecar this engine runs in carries `openssh-client`, `rsync`,
`mariadb-client`, `rclone` and `docker-cli` - deliberately no PHP, because
the PHP a project needs is the project's own, not this image's. Every
framework command therefore runs where the framework actually lives, and
this module builds that wrapper exactly as `transport/ssh.py::ssh_command`
builds an ssh one: a finished argv handed to the single `CommandRunner`
port, so nothing here executes anything and all of it is testable without
Docker.

Reaching a sibling container needs `/var/run/docker.sock` mounted into the
sidecar, which is host-root-equivalent access to the daemon - see the base
design's §10. `doctor` (via `operations/preflight.py`) asserts the socket is
there, so a missing mount is a `doctor` row. A sync does not probe it up
front: its own failure surfaces at the first step that needs the container.
"""

import os
from collections.abc import Sequence
from pathlib import Path

from ddev_sync.ddev.project import DdevProject

# DDEV mounts the project root here in every container it starts, and the
# path is not configurable - `docroot` and `composer_root` are relative to
# it. The sidecar's own compose mounts the project at the same path, which
# is why passing a workdir through unchanged worked there and only broke
# when the engine ran from a host checkout.
CONTAINER_PROJECT_ROOT = Path("/var/www/html")


def _current_user() -> str:
    """`uid:gid` of this process, for `docker exec -u`.

    The sidecar already runs as the developer's uid (the compose service sets
    `user:`), so "exec as whoever I am" is correct by construction. Reading
    `DDEV_UID` instead would add a second source of truth that can disagree
    with the process actually writing the files.
    """
    return f"{os.getuid()}:{os.getgid()}"


def web_command(project: DdevProject, argv: Sequence[str], *, workdir: Path) -> list[str]:
    """Wrap `argv` so it runs in `project`'s web container, in `workdir`.

    `workdir` is a path on whichever side the engine is running - the host
    when it runs from a checkout, the sidecar's own mount when it runs as
    the add-on - and is translated onto `CONTAINER_PROJECT_ROOT`, which is
    where the web container has the project. In the sidecar that is an
    identity; from a host checkout it is what makes the command run at all,
    because `docker exec -w /Users/...` fails to chdir.

    The translated path is passed as docker's own `-w` rather than spliced
    into a `cd ... && ...` string: the command keeps the exact text the
    profile wrote, and there is no second layer of shell quoting to get
    wrong.
    """
    if not argv:
        raise ValueError("a web command needs at least one argument")
    return [
        "docker",
        "exec",
        "-u",
        _current_user(),
        "-w",
        str(_container_workdir(project, workdir)),
        project.web_container,
        *argv,
    ]


def _container_workdir(project: DdevProject, workdir: Path) -> Path:
    """Map `workdir` from this side of the mount onto the container's."""
    try:
        offset = workdir.relative_to(project.root)
    except ValueError as error:
        raise ValueError(
            f"{workdir} is not inside the project at {project.root}, "
            "so it has no place in the web container"
        ) from error
    return CONTAINER_PROJECT_ROOT / offset
