from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import read_remote_credentials
from ddev_sync.transport.fake import FakeCommandRunner

WP_PROFILE: dict[str, Any] = {"database": {"remote_credentials": "wp_cli"}}


def make_context(
    runner: FakeCommandRunner, root: Path, *, layout: Layout = Layout.PLAIN, **config: Any
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="wordpress", docroot="web", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
            layout=layout,
        ),
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
        assume_yes=True,
    )


def wp_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="acme_prod\nacme_user\nsecret\nlocalhost:3307\n")
    return runner


def test_credentials_come_back_in_a_single_round_trip(tmp_path: Path) -> None:
    runner = wp_runner()
    credentials = read_remote_credentials(make_context(runner, tmp_path, **WP_PROFILE))
    assert len(runner.calls) == 1
    assert credentials.name == "acme_prod"
    assert credentials.user == "acme_user"
    assert credentials.password == "secret"


def test_a_host_with_a_port_is_split(tmp_path: Path) -> None:
    credentials = read_remote_credentials(make_context(wp_runner(), tmp_path, **WP_PROFILE))
    assert credentials.host == "localhost"
    assert credentials.port == "3307"


def test_a_host_without_a_port_defaults_to_3306(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="n\nu\np\n127.0.0.1\n")
    credentials = read_remote_credentials(make_context(runner, tmp_path, **WP_PROFILE))
    assert credentials.port == "3306"


def test_wp_config_get_runs_under_the_code_root_of_a_plain_target(tmp_path: Path) -> None:
    runner = wp_runner()
    read_remote_credentials(make_context(runner, tmp_path, layout=Layout.PLAIN, **WP_PROFILE))
    remote = runner.calls[0][-1]
    assert "cd /srv/app/ &&" in remote
    assert "wp config get DB_NAME" in remote


def test_wp_config_get_runs_under_the_code_root_of_a_deployer_target(tmp_path: Path) -> None:
    runner = wp_runner()
    read_remote_credentials(make_context(runner, tmp_path, layout=Layout.DEPLOYER, **WP_PROFILE))
    remote = runner.calls[0][-1]
    assert "cd /srv/app/current/ &&" in remote
    assert "wp config get DB_NAME" in remote


def test_a_failing_wp_config_get_explains_itself(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=1, stderr="Error: wp not found")
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(runner, tmp_path, **WP_PROFILE))
    message = str(caught.value)
    assert "wp" in message
    assert "web01.example" in message


def test_too_few_lines_back_is_reported(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="only_one\n")
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(runner, tmp_path, **WP_PROFILE))
    assert "expected 4" in str(caught.value)
