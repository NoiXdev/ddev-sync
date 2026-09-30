from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError, PathEscapesProject
from ddev_sync.operations.files import FilePair, FilesRsyncOperation, validate_path_pairs
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
    runner.script_prefix(["rsync"], stdout="sent 1,024 bytes")
    return runner


def test_rsync_is_invoked_with_the_resolved_endpoints(tmp_path: Path) -> None:
    runner = ok_runner()
    context = make_context(runner, tmp_path)
    FilesRsyncOperation().execute(
        context, {"remote": "shared/public/fileadmin/", "local": "public/fileadmin/"}
    )
    argv = runner.calls[0]
    assert argv[0] == "rsync"
    assert "deploy@web01.example:/srv/app/shared/public/fileadmin/" in argv
    assert str(tmp_path / "public" / "fileadmin") in " ".join(argv)


def test_the_local_directory_is_created_before_the_transfer(tmp_path: Path) -> None:
    context = make_context(ok_runner(), tmp_path)
    FilesRsyncOperation().execute(
        context, {"remote": "shared/public/uploads/", "local": "public/uploads/"}
    )
    assert (tmp_path / "public" / "uploads").is_dir()


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
                {"remote": "shared/public/fileadmin/", "local": "public/fileadmin/"},
                {"remote": "shared/public/uploads/", "local": "public/uploads/", "delete": True},
            ]
        },
    )
    pairs = validate_path_pairs(context)
    assert pairs == [
        FilePair(
            remote="/srv/app/shared/public/fileadmin/",
            local=(tmp_path / "public" / "fileadmin").resolve(),
            delete=False,
        ),
        FilePair(
            remote="/srv/app/shared/public/uploads/",
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
