from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError, PathEscapesProject
from ddev_sync.operations.exec import (
    ExecOperation,
    resolve_operation_reference,
    run_command_entry,
)
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
        assume_yes=True,
    )


def ok_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], stdout="done")
    return runner


def test_a_run_entry_is_executed_through_sh(tmp_path: Path) -> None:
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "composer install"})
    assert runner.calls[0] == ("sh", "-c", "composer install")


def test_commands_run_in_the_project_root_by_default(tmp_path: Path) -> None:
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "true"})
    assert runner.invocations[0].cwd == tmp_path


def test_workdir_is_honoured(tmp_path: Path) -> None:
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "true", "workdir": "public"})
    assert runner.invocations[0].cwd == tmp_path / "public"


def test_a_workdir_outside_the_project_is_rejected(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    with pytest.raises(PathEscapesProject):
        ExecOperation().execute(
            make_context(runner, tmp_path), {"run": "true", "workdir": "../elsewhere"}
        )
    assert runner.calls == []


def test_an_empty_workdir_is_rejected_rather_than_silently_defaulted(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    with pytest.raises(OperationError) as caught:
        ExecOperation().execute(make_context(runner, tmp_path), {"run": "true", "workdir": ""})
    assert "workdir" in str(caught.value)
    assert runner.calls == []


def test_a_failing_command_raises_with_its_output(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], returncode=2, stderr="composer: not found")
    with pytest.raises(OperationError) as caught:
        ExecOperation().execute(make_context(runner, tmp_path), {"run": "composer install"})
    message = str(caught.value)
    assert "composer install" in message
    assert "composer: not found" in message


def test_allow_failure_swallows_a_non_zero_exit(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], returncode=1, stderr="nothing to do")
    ExecOperation().execute(
        make_context(runner, tmp_path), {"run": "optional", "allow_failure": True}
    )


def test_an_entry_with_neither_run_nor_operation_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        ExecOperation().execute(make_context(ok_runner(), tmp_path), {"unexpected": 1})
    assert "run" in str(caught.value)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ddev-sync:flush-cache", "flush_cache"),
        ("ddev-sync:create-admin", "create_admin"),
        ("flush_cache", "flush_cache"),
    ],
)
def test_operation_references_are_normalised(reference: str, expected: str) -> None:
    assert resolve_operation_reference(reference) == expected


def test_an_operation_entry_dispatches_into_the_catalog(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], stdout="")
    config: dict[str, Any] = {"sanitize": {}}
    context = make_context(runner, tmp_path, **config)
    run_command_entry(
        context, {"operation": "ddev-sync:flush-cache", "command": "vendor/bin/typo3 cache:flush"}
    )
    assert runner.calls[0] == ("sh", "-c", "vendor/bin/typo3 cache:flush")


def test_an_unknown_operation_reference_lists_the_catalog(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        run_command_entry(make_context(ok_runner(), tmp_path), {"operation": "ddev-sync:teleport"})
    message = str(caught.value)
    assert "teleport" in message
    assert "flush_cache" in message
