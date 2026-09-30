"""Map step kinds onto the operations that perform them."""

from collections.abc import Mapping
from typing import Any

from ddev_sync.engine.steps import StepKind
from ddev_sync.operations.base import ExecutionContext, Operation
from ddev_sync.operations.database import DbDumpOperation, DbImportOperation
from ddev_sync.operations.exec import ExecOperation
from ddev_sync.operations.files import FilesRsyncOperation
from ddev_sync.operations.preflight import PreflightOperation
from ddev_sync.operations.sanitize import SanitizeOperation


class NoOpOperation:
    """Does nothing.

    `db.download` exists as a plan step because users expect to see the
    transfer, but the dump is streamed straight into the local file, so there
    is no separate download to perform. Keeping the step visible and empty is
    more honest than hiding a step that the plan output already promised.
    """

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Deliberately does nothing."""


OPERATIONS: dict[StepKind, Operation] = {
    StepKind.PREFLIGHT: PreflightOperation(),
    StepKind.DB_DUMP: DbDumpOperation(),
    StepKind.DB_DOWNLOAD: NoOpOperation(),
    StepKind.DB_IMPORT: DbImportOperation(),
    StepKind.FILES_SYNC: FilesRsyncOperation(),
    StepKind.SANITIZE: SanitizeOperation(),
    StepKind.SETUP: ExecOperation(),
}
