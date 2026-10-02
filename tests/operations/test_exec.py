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
    runner.script_prefix(["docker", "exec"], stdout="done")
    return runner


def workdir_of(argv: tuple[str, ...]) -> str:
    """The directory `docker exec` was told to run in (`-w`)."""
    return argv[argv.index("-w") + 1]


def test_a_run_entry_is_executed_through_sh(tmp_path: Path) -> None:
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "composer install"})
    assert runner.calls[0][-3:] == ("sh", "-c", "composer install")


def test_a_run_entrys_placeholders_are_expanded(tmp_path: Path) -> None:
    # A hook or setup command is documented to share the same ${...}
    # placeholders sanitize rules get (operations/placeholders.py's module
    # docstring, features/resolver.py's module docstring): "${DDEV_SITENAME}
    # ... expanded LATER by operations/placeholders.py". Only sanitize.py
    # actually called expand_value though - run_command_entry (shared by
    # setup.commands and every hook) never did, so a command written with a
    # placeholder silently ran with the literal, unexpanded text.
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "echo ${DDEV_SITENAME}"})
    assert runner.calls[0][-3:] == ("sh", "-c", "echo acme")


def test_an_operation_entrys_placeholders_are_expanded(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="done")
    config: dict[str, Any] = {"sanitize": {}}
    context = make_context(runner, tmp_path, **config)
    run_command_entry(
        context, {"operation": "ddev-sync:flush-cache", "command": "echo ${DDEV_SITENAME}"}
    )
    assert runner.calls[0][-3:] == ("sh", "-c", "echo acme")


def test_commands_run_in_the_project_root_by_default(tmp_path: Path) -> None:
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "true"})
    assert workdir_of(runner.calls[0]) == str(tmp_path)


def test_workdir_is_honoured(tmp_path: Path) -> None:
    runner = ok_runner()
    ExecOperation().execute(make_context(runner, tmp_path), {"run": "true", "workdir": "public"})
    assert workdir_of(runner.calls[0]) == str(tmp_path / "public")


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
    runner.script_prefix(["docker", "exec"], returncode=2, stderr="composer: not found")
    with pytest.raises(OperationError) as caught:
        ExecOperation().execute(make_context(runner, tmp_path), {"run": "composer install"})
    message = str(caught.value)
    assert "composer install" in message
    assert "composer: not found" in message


def test_allow_failure_swallows_a_non_zero_exit(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="nothing to do")
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
    runner.script_prefix(["docker", "exec"], stdout="")
    config: dict[str, Any] = {"sanitize": {}}
    context = make_context(runner, tmp_path, **config)
    run_command_entry(
        context, {"operation": "ddev-sync:flush-cache", "command": "vendor/bin/typo3 cache:flush"}
    )
    assert runner.calls[0][-3:] == ("sh", "-c", "vendor/bin/typo3 cache:flush")


def test_an_unknown_operation_reference_lists_the_catalog(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        run_command_entry(make_context(ok_runner(), tmp_path), {"operation": "ddev-sync:teleport"})
    message = str(caught.value)
    assert "teleport" in message
    assert "flush_cache" in message


def test_a_run_entry_is_executed_in_the_web_container(tmp_path: Path) -> None:
    # `composer install` and `php artisan ...` do not exist in the sidecar
    # image, and must not: the version they need is the project's. They run
    # where the framework lives.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="done")
    context = make_context(runner, tmp_path)
    run_command_entry(context, {"run": "composer install --no-interaction"})
    argv = runner.calls[0]
    assert argv[:2] == ("docker", "exec")
    assert "ddev-acme-web" in argv
    assert argv[-3:] == ("sh", "-c", "composer install --no-interaction")


def test_the_entrys_workdir_becomes_dockers_working_directory(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    context = make_context(runner, tmp_path)
    (tmp_path / "web").mkdir()
    run_command_entry(context, {"run": "pnpm run build", "workdir": "web"})
    argv = runner.calls[0]
    assert argv[argv.index("-w") + 1] == str(tmp_path / "web")


def test_nothing_is_executed_locally_for_a_run_entry(tmp_path: Path) -> None:
    # The regression this guards: a `sh -c` that never leaves the sidecar
    # fails with "composer: not found" on every profile, and the old tests
    # could not see it because they scripted the bare `sh` prefix.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    run_command_entry(make_context(runner, tmp_path), {"run": "php artisan optimize:clear"})
    assert runner.calls[0][:2] == ("docker", "exec")
