from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.loader import GLOBAL_LABEL, LOCAL_LABEL, PROJECT_LABEL
from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.s3 import S3Endpoint, S3SyncOperation, rclone_env
from ddev_sync.transport.fake import FakeCommandRunner

REMOTE = {
    "endpoint": "https://s3.example",
    "access_key": "AKIAREMOTE",
    "secret_key": "remote-secret",
    "bucket": "prod-media",
}
LOCAL = {
    "endpoint": "http://ddev-acme-minio:9000",
    "access_key": "ddevminio",
    "secret_key": "ddevminio",
    "bucket": "local-media",
}


def make_context(runner: FakeCommandRunner, root: Path) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="shopware6", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {})]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
        assume_yes=True,
    )


def ok_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["rclone"], stdout="Transferred: 1.2 GiB")
    return runner


def test_copy_is_the_default_subcommand(tmp_path: Path) -> None:
    runner = ok_runner()
    S3SyncOperation().execute(make_context(runner, tmp_path), {"remote": REMOTE, "local": LOCAL})
    assert runner.calls[0][:2] == ("rclone", "copy")


def test_delete_selects_sync_instead_of_copy(tmp_path: Path) -> None:
    runner = ok_runner()
    S3SyncOperation().execute(
        make_context(runner, tmp_path), {"remote": REMOTE, "local": LOCAL, "delete": True}
    )
    assert runner.calls[0][:2] == ("rclone", "sync")


def test_the_buckets_are_addressed_by_alias(tmp_path: Path) -> None:
    runner = ok_runner()
    S3SyncOperation().execute(make_context(runner, tmp_path), {"remote": REMOTE, "local": LOCAL})
    assert "SRC:prod-media" in runner.calls[0]
    assert "DST:local-media" in runner.calls[0]


def test_credentials_travel_in_the_environment_not_the_command_line(tmp_path: Path) -> None:
    runner = ok_runner()
    S3SyncOperation().execute(make_context(runner, tmp_path), {"remote": REMOTE, "local": LOCAL})
    joined = " ".join(runner.calls[0])
    assert "remote-secret" not in joined
    env = runner.invocations[0].env
    assert env is not None
    assert env["RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY"] == "remote-secret"


def test_the_environment_names_both_providers(tmp_path: Path) -> None:
    env = rclone_env(S3Endpoint(**REMOTE, region=""), S3Endpoint(**LOCAL, region=""))
    assert env["RCLONE_CONFIG_SRC_TYPE"] == "s3"
    assert env["RCLONE_CONFIG_DST_PROVIDER"] == "Minio"
    assert env["RCLONE_CONFIG_SRC_ENDPOINT"] == "https://s3.example"


def test_the_path_includes_alpines_sbin_directories(tmp_path: Path) -> None:
    # Alpine (what slim containers typically use) puts /usr/local/sbin,
    # /usr/sbin and /sbin on the default PATH alongside the bin dirs; some
    # tooling rclone shells out to lives there. Pinned PATH must include them
    # too, in the conventional order, or that tooling silently disappears.
    env = rclone_env(S3Endpoint(**REMOTE, region=""), S3Endpoint(**LOCAL, region=""))
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_the_endpoint_repr_redacts_the_secret() -> None:
    endpoint = S3Endpoint(**REMOTE, region="")
    assert "remote-secret" not in repr(endpoint)
    assert "REDACTED" in repr(endpoint)


@pytest.mark.parametrize("side", ["remote", "local"])
def test_a_missing_side_is_rejected(tmp_path: Path, side: str) -> None:
    params: dict[str, Any] = {"remote": REMOTE, "local": LOCAL}
    del params[side]
    with pytest.raises(OperationError) as caught:
        S3SyncOperation().execute(make_context(ok_runner(), tmp_path), params)
    assert side in str(caught.value)


@pytest.mark.parametrize("key", ["endpoint", "access_key", "secret_key", "bucket"])
def test_a_missing_credential_field_names_it(tmp_path: Path, key: str) -> None:
    remote = dict(REMOTE)
    del remote[key]
    with pytest.raises(OperationError) as caught:
        S3SyncOperation().execute(
            make_context(ok_runner(), tmp_path), {"remote": remote, "local": LOCAL}
        )
    message = str(caught.value)
    assert key in message
    assert "remote" in message


def test_a_failing_rclone_reports_its_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["rclone"], returncode=1, stderr="AccessDenied")
    with pytest.raises(OperationError) as caught:
        S3SyncOperation().execute(
            make_context(runner, tmp_path), {"remote": REMOTE, "local": LOCAL}
        )
    assert "AccessDenied" in str(caught.value)


def context_with_layers(
    runner: FakeCommandRunner, root: Path, *layers: LayerDoc
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="shopware6", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers(list(layers)),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
        assume_yes=True,
    )


@pytest.mark.parametrize("key", ["access_key", "secret_key"])
def test_a_secret_committed_to_the_project_config_is_refused(tmp_path: Path, key: str) -> None:
    # Only `key` is overridden from the committed layer; the other secret
    # stays on the (non-committed) base layer, so a test for "secret_key"
    # actually proves that key is checked on its own, not just caught as a
    # side effect of "access_key" always being checked first.
    base = LayerDoc("profile", {"s3": {"remote": dict(REMOTE), "local": dict(LOCAL)}})
    override = LayerDoc(PROJECT_LABEL, {"s3": {"remote": {key: REMOTE[key]}}})
    context = context_with_layers(ok_runner(), tmp_path, base, override)
    with pytest.raises(OperationError) as caught:
        S3SyncOperation().execute(context, {"remote": REMOTE, "local": LOCAL})
    message = str(caught.value)
    assert PROJECT_LABEL in message
    assert f"s3.remote.{key}" in message


@pytest.mark.parametrize("label", [LOCAL_LABEL, GLOBAL_LABEL])
def test_a_secret_from_the_local_or_global_layer_is_fine(tmp_path: Path, label: str) -> None:
    layer = LayerDoc(label, {"s3": {"remote": REMOTE, "local": LOCAL}})
    context = context_with_layers(ok_runner(), tmp_path, layer)
    S3SyncOperation().execute(context, {"remote": REMOTE, "local": LOCAL})


def test_a_secret_with_no_tracked_provenance_is_fine(tmp_path: Path) -> None:
    # Mirrors how every other test in this file calls execute(): params
    # handed directly, with no merged config behind them at all. Provenance
    # absence must not be mistaken for provenance from the committed layer.
    context = context_with_layers(ok_runner(), tmp_path, LayerDoc("profile", {}))
    S3SyncOperation().execute(context, {"remote": REMOTE, "local": LOCAL})


def test_the_secret_never_appears_in_the_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["rclone"], returncode=1, stderr="failed with remote-secret in the log")
    with pytest.raises(OperationError) as caught:
        S3SyncOperation().execute(
            make_context(runner, tmp_path), {"remote": REMOTE, "local": LOCAL}
        )
    assert "remote-secret" not in str(caught.value)
