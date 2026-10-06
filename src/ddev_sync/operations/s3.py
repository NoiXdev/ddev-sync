"""Mirror a remote S3 bucket into the project's local object storage.

Credentials go into rclone's environment rather than its command line, so they
do not appear in the host's process list while a multi-gigabyte mirror runs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ddev_sync.config.loader import PROJECT_LABEL
from ddev_sync.operations.base import ExecutionContext, OperationError

_REQUIRED = ("endpoint", "access_key", "secret_key", "bucket")
_SECRET_KEYS = ("access_key", "secret_key")


@dataclass(frozen=True)
class S3Endpoint:
    """One side of an S3 mirror."""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = ""

    def __repr__(self) -> str:
        """Redact the secret key."""
        return (
            f"S3Endpoint(endpoint={self.endpoint!r}, access_key={self.access_key!r}, "
            f"secret_key='<REDACTED>', bucket={self.bucket!r}, region={self.region!r})"
        )


def rclone_env(remote: S3Endpoint, local: S3Endpoint) -> dict[str, str]:
    """Build the RCLONE_CONFIG_* environment for a SRC -> DST mirror."""
    return {
        "RCLONE_CONFIG_SRC_TYPE": "s3",
        "RCLONE_CONFIG_SRC_PROVIDER": "Other",
        "RCLONE_CONFIG_SRC_ENDPOINT": remote.endpoint,
        "RCLONE_CONFIG_SRC_ACCESS_KEY_ID": remote.access_key,
        "RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY": remote.secret_key,
        "RCLONE_CONFIG_SRC_REGION": remote.region,
        "RCLONE_CONFIG_DST_TYPE": "s3",
        "RCLONE_CONFIG_DST_PROVIDER": "Minio",
        "RCLONE_CONFIG_DST_ENDPOINT": local.endpoint,
        "RCLONE_CONFIG_DST_ACCESS_KEY_ID": local.access_key,
        "RCLONE_CONFIG_DST_SECRET_ACCESS_KEY": local.secret_key,
        "RCLONE_CONFIG_DST_REGION": local.region,
        # `context.runner.run(..., env=...)` replaces the subprocess's
        # environment rather than extending it (see `transport/runner.py`),
        # so a `PATH` has to be supplied explicitly here or `rclone` itself
        # would not be found on `$PATH` at all. Pinned to the conventional
        # default rather than inherited from the calling process for the same
        # reason credentials go through `RCLONE_CONFIG_*` and not argv: a
        # setup-command's shell (`operations/exec.py`) may have exported all
        # manner of project-specific `PATH` entries by the time this runs,
        # and none of that should leak into what `rclone` can execute.
        # Alpine - what slim containers typically use - puts
        # /usr/local/sbin, /usr/sbin and /sbin on the default PATH alongside
        # the bin dirs; this used to omit all three. Listed here for parity
        # with that conventional default, in its usual order, for the same
        # defence-against-an-inherited-PATH reason given above - not because
        # rclone itself shells out to anything there.
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }


def _endpoint(context: ExecutionContext, params: Mapping[str, Any], side: str) -> S3Endpoint:
    raw = params.get(side)
    if not isinstance(raw, Mapping):
        raise OperationError(f"s3_sync needs a {side!r} block with S3 credentials")
    missing = [key for key in _REQUIRED if not raw.get(key)]
    if missing:
        raise OperationError(f"the {side!r} S3 block is missing {', '.join(missing)}")
    for key in _SECRET_KEYS:
        _reject_committed_secret(context, side, key)
    return S3Endpoint(
        endpoint=str(raw["endpoint"]),
        access_key=str(raw["access_key"]),
        secret_key=str(raw["secret_key"]),
        bucket=str(raw["bucket"]),
        region=str(raw.get("region", "")),
    )


def _reject_committed_secret(context: ExecutionContext, side: str, key: str) -> None:
    """Refuse an S3 secret that came from the committed config layer.

    Spec §10: no credentials live in the add-on's configuration, because
    `.ddev/ddev-sync.yaml` is committed to the repo. The merge tracks
    provenance per value (`context.merged.scalar_sources`), so this can tell
    *where* `s3.<side>.<key>` came from, not just what it is - the local
    local override and the global `~/.ddev/ddev-sync/config.yaml` are both
    fine; only the committed layer is refused.
    """
    if context.merged.scalar_sources.get(("s3", side, key)) == PROJECT_LABEL:
        raise OperationError(
            f"s3.{side}.{key} is set in {PROJECT_LABEL}, which is committed to "
            "the repository; move S3 credentials to .ddev/ddev-sync.local.yaml "
            "or the global config instead - and check that your repository "
            "ignores that path, because nothing here does it for you"
        )


class S3SyncOperation:
    """Mirrors a remote bucket into the local one with rclone."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Copy, or with `delete: true` sync, remote bucket to local."""
        remote = _endpoint(context, params, "remote")
        local = _endpoint(context, params, "local")
        subcommand = "sync" if params.get("delete", False) else "copy"
        result = context.runner.run(
            [
                "rclone",
                subcommand,
                f"SRC:{remote.bucket}",
                f"DST:{local.bucket}",
                "--stats-one-line",
            ],
            env=rclone_env(remote, local),
        )
        if not result.ok:
            message = result.stderr.replace(remote.secret_key, "<REDACTED>").replace(
                local.secret_key, "<REDACTED>"
            )
            raise OperationError(
                f"rclone {subcommand} failed (exit {result.returncode}): {message.strip()}"
            )
