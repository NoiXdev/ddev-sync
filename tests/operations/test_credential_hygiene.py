import shlex
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.database import DbDumpOperation, read_remote_credentials
from ddev_sync.operations.preflight import PreflightOperation
from ddev_sync.transport.fake import FakeCommandRunner

HOSTILE = "p@ss'w\"ord$(id)`whoami`;rm -rf /"
HOSTILE_PATH = "/srv/app$(whoami)/`id`/;rm -rf /"
SPACEY_PATH = "/srv/app directory/"

PROFILE = {
    "database": {
        "remote_credentials": "env",
        "env_map": {
            "name": "DB_NAME",
            "user": "DB_USER",
            "password": "DB_PASSWORD",
            "host": "DB_HOST",
            "port": "DB_PORT",
        },
    }
}


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"],
        stdout=(
            "/srv/app/.env\n"
            "DB_NAME=acme\nDB_USER=acme\n"
            f"DB_PASSWORD={HOSTILE}\n"
            "DB_HOST=127.0.0.1\nDB_PORT=3306\n"
        ),
    )
    runner.script_prefix(["ssh"], stdout="x\n-- Dump completed\n")
    return ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", PROFILE)]),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


def test_a_hostile_password_reaches_mysqldump_as_one_argument(
    context: ExecutionContext,
) -> None:
    DbDumpOperation().execute(context, {})
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    remote = runner.calls[1][-1]
    parsed = shlex.split(remote)
    assert f"--password={HOSTILE}" in parsed


def test_the_hostile_password_cannot_start_a_second_command(
    context: ExecutionContext,
) -> None:
    DbDumpOperation().execute(context, {})
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    remote = runner.calls[1][-1]
    # Everything after the program name is an argument; no unquoted separators.
    assert shlex.split(remote)[0] == "mysqldump"
    quoted_password_argument = shlex.quote(f"--password={HOSTILE}")
    assert ";" not in remote.replace(quoted_password_argument, "")


def preflight_context(path: str, tmp_path: Path) -> ExecutionContext:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    return ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path=path,
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {})]),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


def test_a_hostile_deploy_path_reaches_the_probe_as_one_argument(tmp_path: Path) -> None:
    """`quote_remote` only protects the outer `sh -c <script>` argument; the
    probe used to interpolate `context.target.path` straight into the `[ -d
    ... ]` test with no quoting of its own at all, so anything inside the
    script - not just the password elsewhere - needed its own escaping.
    """
    context = preflight_context(HOSTILE_PATH, tmp_path)
    PreflightOperation().execute(context, {})
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    remote = runner.calls[0][-1]
    outer = shlex.split(remote)
    assert outer[:2] == ["sh", "-c"]
    inner = shlex.split(outer[2])
    assert HOSTILE_PATH in inner


def test_a_deploy_path_containing_a_space_does_not_break_the_test_command(
    tmp_path: Path,
) -> None:
    """The likelier damage needs no attacker at all: an unquoted path with a
    space makes `[ -d /srv/app directory/ ]` a malformed test command (too
    many arguments), which exits 2 - so preflight used to report "the deploy
    path does not exist" for a path that is right there, confidently and
    wrongly.
    """
    context = preflight_context(SPACEY_PATH, tmp_path)
    report = PreflightOperation().check(context)
    assert report.remote_path_ok is True
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    remote = runner.calls[0][-1]
    outer = shlex.split(remote)
    inner = shlex.split(outer[2])
    assert SPACEY_PATH in inner


def remote_env_context(path: str, tmp_path: Path, **database: Any) -> ExecutionContext:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"], stdout="x\nDB_NAME=a\nDB_USER=a\nDB_PASSWORD=a\nDB_HOST=a\nDB_PORT=3306\n"
    )
    return ExecutionContext(
        project=DdevProject(name="acme", type="php", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path=path,
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {"database": database})]),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


@pytest.mark.parametrize("path", [HOSTILE_PATH, SPACEY_PATH])
def test_the_env_file_probe_keeps_a_hostile_or_spacey_deploy_path_as_one_argument(
    tmp_path: Path, path: str
) -> None:
    """The env-file probe double-quoted each candidate path (`"{path}"`)
    rather than `shlex.quote`-ing it: double quotes stop word-splitting but
    not `$(...)` or backtick command substitution, and do nothing at all for
    an embedded `"`.
    """
    context = remote_env_context(
        path,
        tmp_path,
        remote_credentials="env",
        env_map={
            "name": "DB_NAME",
            "user": "DB_USER",
            "password": "DB_PASSWORD",
            "host": "DB_HOST",
            "port": "DB_PORT",
        },
    )
    read_remote_credentials(context)
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    remote = runner.calls[0][-1]
    outer = shlex.split(remote)
    assert outer[:2] == ["sh", "-c"]
    inner = shlex.split(outer[2])
    assert any(path in token for token in inner)


@pytest.mark.parametrize("path", [HOSTILE_PATH, SPACEY_PATH])
def test_wp_cli_cd_keeps_a_hostile_or_spacey_code_root_as_one_argument(
    tmp_path: Path, path: str
) -> None:
    context = remote_env_context(path, tmp_path, remote_credentials="wp_cli")
    read_remote_credentials(context)
    runner = context.runner
    assert isinstance(runner, FakeCommandRunner)
    remote = runner.calls[0][-1]
    outer = shlex.split(remote)
    inner = shlex.split(outer[2])
    assert inner[0] == "cd"
    assert path in inner
