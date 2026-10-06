from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError, PathEscapesProject
from ddev_sync.operations.files import FilePair, FilesRsyncOperation, validate_path_pairs
from ddev_sync.transport.fake import FakeCommandRunner
from ddev_sync.transport.runner import CommandResult


def make_context(
    runner: FakeCommandRunner, root: Path, *, layout: Layout = Layout.PLAIN, **config: Any
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=root),
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


def ok_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["rsync"], stdout="sent 1,024 bytes")
    return runner


def test_rsync_is_invoked_with_the_resolved_endpoints(tmp_path: Path) -> None:
    runner = ok_runner()
    context = make_context(runner, tmp_path)
    FilesRsyncOperation().execute(
        context, {"remote": "public/fileadmin/", "local": "public/fileadmin/"}
    )
    argv = runner.calls[0]
    assert argv[0] == "rsync"
    assert "deploy@web01.example:/srv/app/public/fileadmin/" in argv
    assert argv[-1] == f"{tmp_path / 'public' / 'fileadmin'}/"


def test_the_local_directory_is_created_before_the_transfer(tmp_path: Path) -> None:
    context = make_context(ok_runner(), tmp_path)
    FilesRsyncOperation().execute(
        context, {"remote": "public/uploads/", "local": "public/uploads/"}
    )
    assert (tmp_path / "public" / "uploads").is_dir()


def test_a_plain_targets_remote_has_no_deployer_prefix(tmp_path: Path) -> None:
    runner = ok_runner()
    FilesRsyncOperation().execute(
        make_context(runner, tmp_path, layout=Layout.PLAIN),
        {"remote": "public/fileadmin/", "local": "public/fileadmin/"},
    )
    assert "deploy@web01.example:/srv/app/public/fileadmin/" in runner.calls[0]


def test_a_deployer_targets_remote_is_rooted_under_shared(tmp_path: Path) -> None:
    runner = ok_runner()
    FilesRsyncOperation().execute(
        make_context(runner, tmp_path, layout=Layout.DEPLOYER),
        {"remote": "public/fileadmin/", "local": "public/fileadmin/"},
    )
    assert "deploy@web01.example:/srv/app/shared/public/fileadmin/" in runner.calls[0]


def test_delete_is_off_unless_asked_for(tmp_path: Path) -> None:
    runner = ok_runner()
    FilesRsyncOperation().execute(make_context(runner, tmp_path), {"remote": "a/", "local": "b/"})
    assert "--delete" not in runner.calls[0]


def test_delete_can_be_requested_per_path(tmp_path: Path) -> None:
    runner = ok_runner()
    FilesRsyncOperation().execute(
        make_context(runner, tmp_path), {"remote": "a/", "local": "b/", "delete": True}
    )
    assert "--delete" in runner.calls[0]


def test_a_failing_rsync_reports_its_stderr(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["rsync"], returncode=23, stderr="rsync: link_stat failed")
    with pytest.raises(OperationError) as caught:
        FilesRsyncOperation().execute(
            make_context(runner, tmp_path), {"remote": "a/", "local": "b/"}
        )
    message = str(caught.value)
    assert "link_stat failed" in message
    assert "23" in message


def test_a_params_mapping_without_remote_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        FilesRsyncOperation().execute(make_context(ok_runner(), tmp_path), {"local": "b/"})
    assert "remote" in str(caught.value)


def test_a_params_mapping_without_local_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        FilesRsyncOperation().execute(make_context(ok_runner(), tmp_path), {"remote": "a/"})
    assert "local" in str(caught.value)


def test_validate_returns_every_configured_pair(tmp_path: Path) -> None:
    context = make_context(
        ok_runner(),
        tmp_path,
        files={
            "paths": [
                {"remote": "public/fileadmin/", "local": "public/fileadmin/"},
                {"remote": "public/uploads/", "local": "public/uploads/", "delete": True},
            ]
        },
    )
    pairs = validate_path_pairs(context)
    assert pairs == [
        FilePair(
            remote="/srv/app/public/fileadmin/",
            local=(tmp_path / "public" / "fileadmin").resolve(),
            delete=False,
        ),
        FilePair(
            remote="/srv/app/public/uploads/",
            local=(tmp_path / "public" / "uploads").resolve(),
            delete=True,
        ),
    ]


def test_validate_of_an_empty_files_block_is_empty(tmp_path: Path) -> None:
    assert validate_path_pairs(make_context(ok_runner(), tmp_path)) == []


@pytest.mark.parametrize(
    "escape",
    ["../outside/", "../../etc/", "public/../../../tmp/", "/etc/"],
)
def test_a_local_path_outside_the_project_is_rejected_before_rsync_runs(
    tmp_path: Path, escape: str
) -> None:
    runner = FakeCommandRunner()
    context = make_context(runner, tmp_path)
    with pytest.raises(PathEscapesProject):
        FilesRsyncOperation().execute(context, {"remote": "a/", "local": escape})
    assert runner.calls == []


@pytest.mark.parametrize("escape", ["../outside/", "/etc/"])
def test_validation_catches_the_escape_before_any_step_executes(
    tmp_path: Path, escape: str
) -> None:
    context = make_context(
        ok_runner(), tmp_path, files={"paths": [{"remote": "a/", "local": escape}]}
    )
    with pytest.raises(PathEscapesProject) as caught:
        validate_path_pairs(context)
    assert escape in str(caught.value)


def test_a_path_that_only_looks_like_an_escape_is_allowed(tmp_path: Path) -> None:
    runner = ok_runner()
    context = make_context(runner, tmp_path)
    FilesRsyncOperation().execute(context, {"remote": "a/", "local": "public/../public/uploads/"})
    assert len(runner.calls) == 1


PAIR = {"remote": "public/fileadmin/", "local": "public/fileadmin/"}


def test_rsync_asks_for_passthrough_when_stdout_is_a_terminal(tmp_path: Path) -> None:
    # rsync is already given `--info=progress2`; the only reason the progress
    # was never seen is that the runner captured it.
    runner = ok_runner()
    context = replace(make_context(runner, tmp_path), stdout_is_terminal=True)
    FilesRsyncOperation().execute(context, PAIR)
    assert runner.invocations[0].passthrough is True


def test_rsync_does_not_ask_for_passthrough_without_a_terminal(tmp_path: Path) -> None:
    # In a CI log rsync's `\r`-separated progress becomes thousands of lines.
    runner = ok_runner()
    context = make_context(runner, tmp_path)
    FilesRsyncOperation().execute(context, PAIR)
    assert runner.invocations[0].passthrough is False


def test_a_failed_passthrough_rsync_still_reports_its_stderr(tmp_path: Path) -> None:
    # Passthrough inherits stdout only. stderr stays captured precisely so
    # this message keeps working.
    runner = FakeCommandRunner()
    runner.script_prefix(["rsync"], returncode=23, stderr="rsync: link_stat failed")
    context = replace(make_context(runner, tmp_path), stdout_is_terminal=True)
    with pytest.raises(OperationError) as caught:
        FilesRsyncOperation().execute(context, PAIR)
    assert "link_stat failed" in str(caught.value)


class _StdoutIsTheTerminals(CommandResult):
    """A result whose stdout may not be read, as a passthrough call's cannot.

    The fake already returns `""` for a passthrough call, which a caller that
    did read it would take for "rsync printed nothing" and carry on.
    """

    @property
    def stdout(self) -> str:
        raise AssertionError("stdout was handed to the terminal; there is nothing to read")

    @stdout.setter
    def stdout(self, _value: str) -> None:
        """Accept the dataclass constructor's assignment and discard it."""


class _TrappedStdoutRunner(FakeCommandRunner):
    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        result = super().run(argv, **kwargs)
        return _StdoutIsTheTerminals(result.argv, result.returncode, "", result.stderr)


def test_a_successful_passthrough_rsync_does_not_read_stdout(tmp_path: Path) -> None:
    runner = _TrappedStdoutRunner()
    runner.script_prefix(["rsync"])
    context = replace(make_context(runner, tmp_path), stdout_is_terminal=True)
    FilesRsyncOperation().execute(context, PAIR)


def test_a_failed_passthrough_rsync_does_not_read_stdout_either(tmp_path: Path) -> None:
    runner = _TrappedStdoutRunner()
    runner.script_prefix(["rsync"], returncode=23, stderr="rsync: link_stat failed")
    context = replace(make_context(runner, tmp_path), stdout_is_terminal=True)
    with pytest.raises(OperationError) as caught:
        FilesRsyncOperation().execute(context, PAIR)
    assert "link_stat failed" in str(caught.value)
