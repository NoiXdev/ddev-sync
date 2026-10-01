"""Make production data safe to have on a laptop.

Rules are data, not code. What the tool this replaces hardcoded - Shopware's
sales-channel rewrite, TYPO3's admin creation, clearing mailer settings - are
now declarations in a profile, which is why those special cases no longer sit
in the middle of the database module.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    LOCAL_DB_PASSWORD,
    LOCAL_DB_PORT,
    LOCAL_DB_USER,
)
from ddev_sync.operations.placeholders import expand_value

# MySQL/MariaDB errno 1146 / SQLSTATE 42S02: "table doesn't exist". Narrow on
# purpose - `tolerate_missing_table` must swallow exactly this failure and no
# other, so a rule with a typo in its SQL, a permissions error, or a dead
# database connection still raises. Observed verbatim from a real MariaDB:
# "ERROR 1146 (42S02) at line 1: Table 'db.tx_scheduler_task' doesn't exist".
_MISSING_TABLE_ERROR = re.compile(r"\b1146\b|\(42S02\)")


def _is_missing_table_error(stderr: str) -> bool:
    return _MISSING_TABLE_ERROR.search(stderr) is not None


@dataclass(frozen=True)
class SanitizeRule:
    """One sanitize step: either a SQL statement or a named operation."""

    name: str
    sql: str | None = None
    operation: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    # Deliberately narrower than a general `allow_failure`: this tolerates
    # only a missing table, and only for `sql` rules (see `_parse_rule`). A
    # sanitize rule that could fail silently for any OTHER reason would
    # undermine the one invariant this module exists to guarantee - a rule
    # that can run either runs or raises loudly, because a rule that fails
    # silently means production data stays on a laptop. The only thing this
    # carves out is a table that genuinely was never there to sanitise, e.g.
    # `tx_scheduler_task` on a TYPO3 project that never required
    # `typo3/cms-scheduler` (a separate, optional Composer package).
    tolerate_missing_table: bool = False
    # The table this rule needs, for the Part 3 preflight probe to check
    # before the dump even starts. Only meaningful (and only accepted) on a
    # `sql` rule: an `operation` rule that takes a `table` param (e.g.
    # `reset_passwords`) already declares its table through that param, so
    # `requires_table` would be redundant there and, if ever read by mistake
    # instead of `params["table"]`, silently wrong.
    requires_table: str | None = None


class SanitizeOperationHandler(Protocol):
    """A named sanitize operation that needs more than a SQL statement."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Perform the operation described by `rule`."""


SANITIZE_OPERATIONS: dict[str, SanitizeOperationHandler] = {}

_RESERVED = {"name", "sql", "operation", "tolerate_missing_table", "requires_table"}


def parse_rules(context: ExecutionContext) -> list[SanitizeRule]:
    """Read and validate `sanitize.rules` from the merged configuration."""
    return [_parse_rule(entry, context) for entry in context.config_list("sanitize.rules")]


def _parse_rule(entry: Mapping[str, Any], context: ExecutionContext) -> SanitizeRule:
    name = entry.get("name")
    if not name:
        raise OperationError(f"a sanitize rule is missing 'name': {dict(entry)}")
    sql = entry.get("sql")
    operation = entry.get("operation")
    if sql is None and operation is None:
        raise OperationError(f"sanitize rule {name!r} has neither 'sql' nor 'operation'")
    if sql is not None and operation is not None:
        raise OperationError(f"sanitize rule {name!r} has both 'sql' and 'operation'; pick one")
    tolerate_missing_table = bool(entry.get("tolerate_missing_table", False))
    if tolerate_missing_table and sql is None:
        raise OperationError(
            f"sanitize rule {name!r} sets 'tolerate_missing_table' but has no 'sql'; "
            "it only means anything for a SQL rule that might target a table that "
            "does not exist"
        )
    requires_table = entry.get("requires_table")
    if requires_table is not None and sql is None:
        raise OperationError(
            f"sanitize rule {name!r} sets 'requires_table' but has no 'sql'; an "
            "'operation' rule already declares its table through its own 'table' "
            "parameter"
        )
    raw_params = {key: value for key, value in entry.items() if key not in _RESERVED}
    return SanitizeRule(
        name=str(name),
        # `sql` is expanded exactly like every other parameter below - it
        # used to bypass expand_value entirely (only `raw_params` went
        # through it), which would have left a placeholder written directly
        # into raw SQL (${WP_TABLE_PREFIX}, needed for profiles/wordpress.yaml)
        # as the literal text "${WP_TABLE_PREFIX}" sent to the database.
        sql=None if sql is None else str(expand_value(str(sql), context)),
        operation=None if operation is None else str(operation),
        # Every rule parameter is expanded here, at parse time, rather than
        # inside each `SanitizeOperationHandler` - a sixth operation reading
        # a new parameter name gets the substitution for free instead of
        # having to remember to call it. This is what closes the gap that let
        # `SearchReplace` write the literal string "${DDEV_SITENAME}" into
        # the database: `expand_placeholders` used to run only on the (dead)
        # env-rewrite path, never on sanitize rule parameters.
        params=expand_value(raw_params, context),
        tolerate_missing_table=tolerate_missing_table,
        requires_table=None
        if requires_table is None
        else str(expand_value(str(requires_table), context)),
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
        if result.ok:
            return
        if rule.tolerate_missing_table and _is_missing_table_error(result.stderr):
            return
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
