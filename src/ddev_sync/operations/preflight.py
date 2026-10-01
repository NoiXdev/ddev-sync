"""Check that a sync can succeed before starting to move gigabytes.

One ssh round trip answers both questions that matter - can we connect, and is
the deploy path there - because two round trips through a jump host is two
chances for the user to wait.
"""

import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.files import validate_path_pairs
from ddev_sync.transport.ssh import ssh_command

MIN_FREE_BYTES = 2 * 1024**3

_OK = "ddev-sync-ok"
_NO_PATH = "ddev-sync-no-path"


@dataclass(frozen=True)
class PreflightReport:
    """What preflight found, for the doctor command to render."""

    ssh_ok: bool
    remote_path_ok: bool
    free_bytes: int


class PreflightOperation:
    """Verifies ssh access, the remote deploy path and local disk space."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Raise unless everything preflight checks is in order.

        `validate_path_pairs` runs first: it is local, network-free
        validation of `files.paths`, and it exists specifically so a bad
        entry - missing a key, or a `local` that escapes the project root -
        is caught before a single byte moves, not mid-transfer after earlier
        pairs have already rsynced.
        """
        validate_path_pairs(context)
        report, stderr = self._probe(context)
        if not report.ssh_ok:
            raise OperationError(self._ssh_help(context, stderr))
        if not report.remote_path_ok:
            raise OperationError(
                f"the deploy path {context.target.path!r} does not exist on "
                f"{context.target.host}; check the target's 'path' setting"
            )
        if report.free_bytes < MIN_FREE_BYTES:
            raise OperationError(
                f"only {report.free_bytes // 1024**2} MB free on the project volume; "
                f"a sync wants at least {MIN_FREE_BYTES // 1024**3} GB"
            )

    def check(self, context: ExecutionContext) -> PreflightReport:
        """Run the probes and report, without raising on a bad result."""
        report, _stderr = self._probe(context)
        return report

    def _probe(self, context: ExecutionContext) -> tuple[PreflightReport, str]:
        """Run the ssh probe once and return the report plus any stderr.

        The stderr is only meaningful when the probe failed to connect at
        all, but it is returned alongside the report - rather than stashed on
        `self` - so this object stays stateless: nothing here depends on
        `check` having been called first.
        """
        quoted_path = shlex.quote(context.target.path)
        probe = f"if [ -d {quoted_path} ]; then echo {_OK}; else echo {_NO_PATH}; fi"
        result = context.runner.run(ssh_command(context.target, ["sh", "-c", probe]))
        if not result.ok:
            return PreflightReport(False, False, self._free_bytes(context)), result.stderr
        output = result.stdout.strip()
        if output not in {_OK, _NO_PATH}:
            raise OperationError(
                f"unexpected output from the remote probe on {context.target.host}: {output}"
            )
        return PreflightReport(True, output == _OK, self._free_bytes(context)), ""

    @staticmethod
    def _ssh_help(context: ExecutionContext, stderr: str) -> str:
        return (
            f"cannot reach {context.target.host} over ssh. "
            "Run 'ddev auth ssh' and try again, or check the target's host, "
            f"user and port.\n{stderr.strip()}"
        )

    @staticmethod
    def _free_bytes(context: ExecutionContext) -> int:
        return shutil.disk_usage(context.project.root).free
