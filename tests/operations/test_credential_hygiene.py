import shlex
from pathlib import Path

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.database import DbDumpOperation
from ddev_sync.transport.fake import FakeCommandRunner

HOSTILE = "p@ss'w\"ord$(id)`whoami`;rm -rf /"

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
