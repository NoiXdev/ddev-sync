import os
from pathlib import Path

import pytest

from ddev_sync.ddev.project import DdevProject
from ddev_sync.transport.container import web_command


def project(root: str = "/var/www/html") -> DdevProject:
    return DdevProject(name="acme", type="typo3", docroot="public", root=Path(root))


@pytest.fixture(autouse=True)
def fixed_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the ids so the expected argv is a literal, not a computation.

    `os.getuid`/`os.getgid` are what the real call reads; monkeypatching
    them, rather than injecting a string, is what keeps the production path
    and the asserted path the same code.
    """
    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.setattr(os, "getgid", lambda: 20)


def test_a_command_is_wrapped_in_docker_exec_against_the_web_container() -> None:
    argv = web_command(
        project(), ["vendor/bin/typo3", "cache:flush"], workdir=Path("/var/www/html")
    )
    assert argv == [
        "docker",
        "exec",
        "-u",
        "501:20",
        "-w",
        "/var/www/html",
        "ddev-acme-web",
        "vendor/bin/typo3",
        "cache:flush",
    ]


def test_the_workdir_is_passed_as_w_rather_than_a_shell_cd() -> None:
    # `-w` is docker's own working directory, so the command string stays
    # exactly what the profile wrote - no `cd ... && ` prefix to quote, and
    # nothing for a `sh -c` to re-interpret.
    argv = web_command(
        project(),
        ["sh", "-c", "composer install"],
        workdir=Path("/var/www/html/web"),
    )
    assert argv[:7] == [
        "docker",
        "exec",
        "-u",
        "501:20",
        "-w",
        "/var/www/html/web",
        "ddev-acme-web",
    ]
    assert argv[7:] == ["sh", "-c", "composer install"]


def test_the_user_is_the_calling_process_so_written_files_belong_to_the_developer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The sidecar already runs as the developer's uid (compose sets `user:`),
    # so "exec as whoever I am" is correct by construction and needs no
    # DDEV_UID lookup that could disagree with it.
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(os, "getgid", lambda: 1000)
    argv = web_command(project(), ["true"], workdir=Path("/var/www/html"))
    assert argv[2:4] == ["-u", "1000:1000"]


def test_an_empty_argv_is_rejected_rather_than_producing_a_bare_docker_exec() -> None:
    # `docker exec -u ... -w ... container` with no command is a usage error
    # from docker, reported as a confusing non-zero exit far from its cause.
    with pytest.raises(ValueError, match="needs at least one argument"):
        web_command(project(), [], workdir=Path("/var/www/html"))


def test_a_host_project_root_is_translated_to_the_container_mount_point() -> None:
    # Running from a host checkout, `project.root` is a host path that does
    # not exist in the container: `docker exec -w /Users/me/code/acme` fails
    # with "chdir to cwd ... failed", and docker writes that to stdout, so it
    # used to surface as a blank preflight row.
    argv = web_command(
        project(root="/Users/me/code/acme"),
        ["vendor/bin/typo3", "cache:flush"],
        workdir=Path("/Users/me/code/acme"),
    )
    assert argv[4:6] == ["-w", "/var/www/html"]


def test_a_subdirectory_of_the_project_keeps_its_offset_under_the_mount_point() -> None:
    argv = web_command(
        project(root="/Users/me/code/acme"),
        ["sh", "-c", "npm ci"],
        workdir=Path("/Users/me/code/acme/public/theme"),
    )
    assert argv[4:6] == ["-w", "/var/www/html/public/theme"]


def test_a_workdir_outside_the_project_is_rejected_rather_than_silently_mapped() -> None:
    # Every call site passes `project.root`, `project.docroot_path` or a path
    # `ExecutionContext.local` has already confined to the root, so this is a
    # programming error - and guessing a mapping for it would run a command in
    # whatever the container happens to have at that path.
    with pytest.raises(ValueError, match="is not inside the project"):
        web_command(
            project(root="/Users/me/code/acme"),
            ["true"],
            workdir=Path("/etc"),
        )
