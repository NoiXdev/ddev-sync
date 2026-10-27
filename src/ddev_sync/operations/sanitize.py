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
    LOCAL_DB_TLS_OPTION,
    LOCAL_DB_USER,
)
from ddev_sync.operations.fake import parse_columns
from ddev_sync.operations.placeholders import expand_value

# MySQL/MariaDB errno 1146 / SQLSTATE 42S02: "table doesn't exist". Narrow on
# purpose - `tolerate_missing_table` must swallow exactly this failure and no
# other, so a rule with a typo in its SQL, a permissions error, or a dead
# database connection still raises. Observed verbatim from a real MariaDB:
# "ERROR 1146 (42S02) at line 1: Table 'db.tx_scheduler_task' doesn't exist".
_MISSING_TABLE_ERROR = re.compile(r"\b1146\b|\(42S02\)")


def is_missing_table_error(stderr: str) -> bool:
    return _MISSING_TABLE_ERROR.search(stderr) is not None


@dataclass(frozen=True)
class SanitizeRule:
    """One sanitize step: either a SQL statement or a named operation."""

    name: str
    sql: str | None = None
    operation: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    # Deliberately narrower than a general `allow_failure`: this tolerates
    # only a missing table. A sanitize rule that could fail silently for any
    # OTHER reason would undermine the one invariant this module exists to
    # guarantee - a rule that can run either runs or raises loudly, because a
    # rule that fails silently means production data stays on a laptop. The
    # only thing this carves out is a table that genuinely was never there to
    # sanitise, e.g. `tx_scheduler_task` on a TYPO3 project that never
    # required `typo3/cms-scheduler` (a separate, optional Composer package).
    #
    # Allowed on BOTH `sql` and `operation` rules, not just `sql` - narrowed
    # to `sql` only in an earlier version of this comment, on the assumption
    # that an `operation` rule's failure could not be a missing-table error.
    # That assumption does not hold: `reset_passwords`, `clear_mail_config`
    # and `search_replace`'s sql driver (operations/catalog.py) all build and
    # run SQL against a named table exactly like a `sql` rule does, so a 1146
    # is just as possible there. Without this, an `operation` rule whose
    # table happens to be optional (e.g. typo3.yaml's `reset-fe-passwords`
    # against `fe_users`, absent on a backend-only install - see that file's
    # comment) could be named a hard requirement by the Part 3 preflight
    # probe (its `table` param counts, see `preflight.py::_rule_table`) with
    # no way to say otherwise.
    tolerate_missing_table: bool = False
    # The table this rule needs, for the Part 3 preflight probe to check
    # before the dump even starts. Only meaningful (and only accepted) on a
    # `sql` rule: an `operation` rule that takes a `table` param (e.g.
    # `reset_passwords`) already declares its table through that param, so
    # `requires_table` would be redundant there and, if ever read by mistake
    # instead of `params["table"]`, silently wrong.
    requires_table: str | None = None
    # Marks a `sql` rule as one that hides personal data (a rewritten e-mail
    # column, a blanked name) as opposed to a protective one (truncating
    # sessions, dropping tokens). The `sanitize.disable_sanitize*` switches
    # tell the two apart so a developer can keep realistic data without ever
    # switching off protection. Only accepted on `sql` rules: an `operation`
    # rule's nature is fixed by the operation itself (`fake` hides personal
    # data, `reset_passwords` protects), so a marker there could only
    # contradict it.
    personal_data: bool = False

    @property
    def is_personal_data(self) -> bool:
        return entry_is_personal_data(
            {"operation": self.operation, "personal_data": self.personal_data}
        )


def entry_is_personal_data(entry: Mapping[str, object]) -> bool:
    """Whether a rule hides personal data: a `fake` operation or a marked `sql` rule.

    On a raw config entry as well as on a parsed rule, so the `--dry-run` plan
    (which never parses rules) and the run itself cannot disagree about which
    rules a switch skips. `is True`, not truthiness: a malformed marker is
    refused by `_parse_rule`, and must not count as set here.
    """
    return entry.get("operation") == "fake" or entry.get("personal_data") is True


class SanitizeOperationHandler(Protocol):
    """A named sanitize operation that needs more than a SQL statement."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Perform the operation described by `rule`."""


SANITIZE_OPERATIONS: dict[str, SanitizeOperationHandler] = {}

_RESERVED = {
    "name",
    "sql",
    "operation",
    "tolerate_missing_table",
    "requires_table",
    "personal_data",
}


def parse_rules(context: ExecutionContext) -> list[SanitizeRule]:
    """Read and validate `sanitize.rules` from the merged configuration."""
    return [_parse_rule(entry, context) for entry in context.config_list("sanitize.rules")]


DISABLE_SANITIZE_KEY = "sanitize.disable_sanitize"
DISABLE_SANITIZE_ALL_KEY = "sanitize.disable_sanitize_all"
DISABLE_SEEDING_KEY = "sanitize.disable_seeding"


@dataclass(frozen=True)
class SanitizeSwitches:
    """The project switches that shape the sanitize run.

    `disable_seeding` turns off no rule; it picks constants over seeded names
    in `fake`. It sits here anyway so it is validated at the same moments as
    the other two - preflight, the dry run and the sanitize step - and not
    first inside the `fake` rule after the import, where the rules behind it
    (the password resets) would never run.
    """

    disable_sanitize: bool
    disable_sanitize_all: bool
    disable_seeding: bool


def _switch(context: ExecutionContext, key: str) -> bool:
    """Read one boolean switch; anything but true/false/unset is refused.

    Same shape as `database.remote_tls_options`: a misread switch would
    silently keep or drop protection, so `"yes"` or `1` is an error.
    """
    value = context.setting(key)
    if value is None or value is False:
        return False
    if value is True:
        return True
    raise OperationError(f"{key} must be true or false, got {value!r}")


def sanitize_switches(context: ExecutionContext) -> SanitizeSwitches:
    """All switches, each validated even when another one is set."""
    return SanitizeSwitches(
        disable_sanitize=_switch(context, DISABLE_SANITIZE_KEY),
        disable_sanitize_all=_switch(context, DISABLE_SANITIZE_ALL_KEY),
        disable_seeding=_switch(context, DISABLE_SEEDING_KEY),
    )


def active_rules(context: ExecutionContext) -> list[SanitizeRule]:
    """The rules that actually run, after applying the switches.

    The single place both the sanitize step and preflight read, so a rule that
    is switched off is neither run nor required to have its table. `all` wins
    over the personal-data switch; the latter keeps every protective rule
    (password resets, mail-config clearing, unmarked SQL).
    """
    switches = sanitize_switches(context)
    rules = parse_rules(context)
    if switches.disable_sanitize_all:
        return []
    if switches.disable_sanitize:
        return [rule for rule in rules if not rule.is_personal_data]
    return rules


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
    # No restriction to `sql`-only here: an `operation` rule (reset_passwords,
    # clear_mail_config, search_replace's sql driver) can hit the identical
    # missing-table error - see the field's own docstring above - so both
    # shapes accept the flag.
    tolerate_missing_table = bool(entry.get("tolerate_missing_table", False))
    requires_table = entry.get("requires_table")
    if requires_table is not None and sql is None:
        raise OperationError(
            f"sanitize rule {name!r} sets 'requires_table' but has no 'sql'; an "
            "'operation' rule already declares its table through its own 'table' "
            "parameter"
        )
    personal_data = entry.get("personal_data", False)
    # A real boolean, not bool(): `personal_data: "no"` would be truthy and
    # silently classify a rule the opposite way from what its author meant.
    if not isinstance(personal_data, bool):
        raise OperationError(
            f"sanitize rule {name!r}: 'personal_data' must be true or false, got {personal_data!r}"
        )
    if "personal_data" in entry and operation is not None:
        raise OperationError(
            f"sanitize rule {name!r} sets 'personal_data' but is an 'operation' rule; only "
            "a 'sql' rule can be marked (a 'fake' operation is always personal data)"
        )
    raw_params = {key: value for key, value in entry.items() if key not in _RESERVED}
    if operation == "fake":
        # Validated here, not when the rule runs, so preflight rejects a typo in
        # a kind or a setting before a production dump is downloaded.
        parse_columns(str(name), raw_params.get("columns"))
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
        personal_data=personal_data,
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
        LOCAL_DB_TLS_OPTION,
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
        for rule in active_rules(context):
            if rule.sql is not None:
                self._run_sql(context, database, rule)
            else:
                self._run_operation(context, rule)

    @staticmethod
    def _run_sql(context: ExecutionContext, database: str, rule: SanitizeRule) -> None:
        result = context.runner.run(local_mysql_argv(context, database, sql=rule.sql))
        if result.ok:
            return
        if rule.tolerate_missing_table and is_missing_table_error(result.stderr):
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
