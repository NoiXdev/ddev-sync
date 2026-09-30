from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.preflight import MIN_FREE_BYTES, PreflightOperation
from ddev_sync.transport.fake import FakeCommandRunner


def make_context(runner: FakeCommandRunner, root: Path, **config: Any) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
    )


def healthy_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    return runner


def test_a_healthy_remote_passes_without_raising(tmp_path: Path) -> None:
    PreflightOperation().execute(make_context(healthy_runner(), tmp_path), {})


def test_the_probe_runs_over_ssh_to_the_configured_endpoint(tmp_path: Path) -> None:
    runner = healthy_runner()
    PreflightOperation().execute(make_context(runner, tmp_path), {})
    argv = runner.calls[0]
    assert argv[0] == "ssh"
    assert "deploy@web01.example" in argv


def test_the_probe_checks_the_remote_path_in_the_same_round_trip(tmp_path: Path) -> None:
    runner = healthy_runner()
    PreflightOperation().execute(make_context(runner, tmp_path), {})
    remote_command = runner.calls[0][-1]
    assert "/srv/app/" in remote_command
    assert "ddev-sync-ok" in remote_command


def test_check_reports_everything_it_found(tmp_path: Path) -> None:
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.ssh_ok is True
    assert report.remote_path_ok is True
    assert report.free_bytes > 0


def test_a_refused_ssh_connection_explains_ddev_auth_ssh(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=255, stderr="Permission denied (publickey).")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    message = str(caught.value)
    assert "ddev auth ssh" in message
    assert "Permission denied" in message


def test_a_missing_remote_path_names_it(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-no-path\n")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert "/srv/app/" in str(caught.value)
    assert "does not exist" in str(caught.value)


def test_unexpected_probe_output_is_reported_verbatim(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="bash: line 1: syntax error\n")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert "syntax error" in str(caught.value)


def test_the_minimum_free_space_is_two_gigabytes() -> None:
    assert MIN_FREE_BYTES == 2 * 1024**3


def test_low_disk_space_is_refused_with_the_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddev_sync.operations import preflight as preflight_module

    class _FakeUsage:
        free = 1024

    monkeypatch.setattr(
        preflight_module.shutil,
        "disk_usage",
        lambda _path: _FakeUsage(),
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(healthy_runner(), tmp_path), {})
    message = str(caught.value)
    assert "0 MB free" in message
    assert "2 GB" in message
