"""Make production data safe to have on a laptop.

Rules are data, not code. What the tool this replaces hardcoded - Shopware's
sales-channel rewrite, TYPO3's admin creation, clearing mailer settings - are
now declarations in a profile, which is why those special cases no longer sit
in the middle of the database module.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    LOCAL_DB_PASSWORD,
    LOCAL_DB_PORT,
    LOCAL_DB_USER,
)


@dataclass(frozen=True)
class SanitizeRule:
    """One sanitize step: either a SQL statement or a named operation."""

    name: str
    sql: str | None = None
    operation: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


class SanitizeOperationHandler(Protocol):
    """A named sanitize operation that needs more than a SQL statement."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Perform the operation described by `rule`."""


SANITIZE_OPERATIONS: dict[str, SanitizeOperationHandler] = {}

_RESERVED = {"name", "sql", "operation"}


def parse_rules(context: ExecutionContext) -> list[SanitizeRule]:
    """Read and validate `sanitize.rules` from the merged configuration."""
    return [_parse_rule(entry) for entry in context.config_list("sanitize.rules")]


def _parse_rule(entry: Mapping[str, Any]) -> SanitizeRule:
    name = entry.get("name")
    if not name:
        raise OperationError(f"a sanitize rule is missing 'name': {dict(entry)}")
    sql = entry.get("sql")
    operation = entry.get("operation")
    if sql is None and operation is None:
        raise OperationError(f"sanitize rule {name!r} has neither 'sql' nor 'operation'")
    if sql is not None and operation is not None:
        raise OperationError(f"sanitize rule {name!r} has both 'sql' and 'operation'; pick one")
    return SanitizeRule(
        name=str(name),
        sql=None if sql is None else str(sql),
        operation=None if operation is None else str(operation),
        params={key: value for key, value in entry.items() if key not in _RESERVED},
    )


def local_mysql_argv(
    context: ExecutionContext, database: str, *, sql: str | None = None
) -> list[str]:
    """Build a `mysql` invocation against the local DDEV database."""
    argv = [
        "mysql",
        f"--user={LOCAL_DB_USER}",
        f"--password={LOCAL_DB_PASSWORD}",
        f"--host={context.project.db_container}",
        f"--port={LOCAL_DB_PORT}",
        database,
    ]
    if sql is not None:
        argv.extend(["--execute", sql])
    return argv


class SanitizeOperation:
    """Runs every configured sanitize rule, in order."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Apply all sanitize rules to the freshly imported database."""
        database = context.config("database.target_database", default="db")
        for rule in parse_rules(context):
            if rule.sql is not None:
                self._run_sql(context, database, rule)
            else:
                self._run_operation(context, rule)

    @staticmethod
    def _run_sql(context: ExecutionContext, database: str, rule: SanitizeRule) -> None:
        result = context.runner.run(local_mysql_argv(context, database, sql=rule.sql))
        if not result.ok:
            raise OperationError(f"sanitize rule {rule.name!r} failed: {result.stderr.strip()}")

    @staticmethod
    def _run_operation(context: ExecutionContext, rule: SanitizeRule) -> None:
        handler = SANITIZE_OPERATIONS.get(str(rule.operation))
        if handler is None:
            known = ", ".join(sorted(SANITIZE_OPERATIONS)) or "none"
            raise OperationError(
                f"sanitize rule {rule.name!r} uses unknown operation "
                f"{rule.operation!r}; available operations: {known}"
            )
        handler.run(context, rule)


# Imported for its registration side effect. Placed at the bottom because
# catalog.py imports SANITIZE_OPERATIONS from this module.
from ddev_sync.operations import catalog as _catalog  # noqa: E402,F401
