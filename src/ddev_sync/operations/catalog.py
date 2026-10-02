"""The closed catalog of named sanitize operations.

Each entry exists because a SQL statement alone cannot express it - either it
needs several statements, or it needs to shell out to a framework CLI. Adding
an entry here is a deliberate act; profiles cannot invent new ones.
"""

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.sanitize import (
    SANITIZE_OPERATIONS,
    SanitizeRule,
    is_missing_table_error,
    local_mysql_argv,
)
from ddev_sync.transport.container import web_command

LOCAL_ADMIN_PASSWORD = "Start23!"


def _required(rule: SanitizeRule, key: str) -> str:
    value = rule.params.get(key)
    if value is None or value == "":
        raise OperationError(f"sanitize rule {rule.name!r} ({rule.operation}) is missing {key!r}")
    return str(value)


def _quote(value: str) -> str:
    """Escape a value for a single-quoted SQL literal.

    Backslashes are escaped first and quotes second: reversing the order
    would double-escape a backslash that a quote-escaping pass introduced.
    """
    return value.replace("\\", "\\\\").replace("'", "''")


def _identifier(value: str) -> str:
    """Escape a table or column name for backtick quoting."""
    return value.replace("`", "``")


def _run_sql(context: ExecutionContext, rule: SanitizeRule, sql: str) -> None:
    """Run `sql` for `rule`, honouring `tolerate_missing_table` exactly like
    `SanitizeOperation._run_sql` does for a plain `sql:` rule - the three
    operations that reach here (`ClearMailConfig`, `ResetPasswords`,
    `SearchReplace`'s sql driver) build and run SQL against a named table
    too, so the identical errno 1146 is just as possible for them.
    """
    database = context.config("database.target_database", default="db")
    result = context.runner.run(local_mysql_argv(context, database, sql=sql))
    if result.ok:
        return
    if rule.tolerate_missing_table and is_missing_table_error(result.stderr):
        return
    raise OperationError(f"sanitize rule {rule.name!r} failed: {result.stderr.strip()}")


class ClearMailConfig:
    """Delete mailer settings so a local instance cannot send real email."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Delete the configured keys from the configured table."""
        table = _identifier(_required(rule, "table"))
        column = _identifier(_required(rule, "key_column"))
        keys = rule.params.get("keys", [])
        if not isinstance(keys, list):
            raise OperationError(
                f"sanitize rule {rule.name!r}: 'keys' must be a list, got {type(keys).__name__}"
            )
        if not keys:
            return
        values = ", ".join(f"'{_quote(str(key))}'" for key in keys)
        _run_sql(
            context,
            rule,
            f"DELETE FROM `{table}` WHERE `{column}` IN ({values})",
        )


class ResetPasswords:
    """Overwrite every password hash in a table with one unusable value."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Set `column` to `value` for every row of `table`."""
        table = _identifier(_required(rule, "table"))
        column = _identifier(_required(rule, "column"))
        value = _quote(_required(rule, "value"))
        _run_sql(context, rule, f"UPDATE `{table}` SET `{column}` = '{value}'")


class SearchReplace:
    """Rewrite a URL column, optionally only where it matches a known value."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Replace `from` with `to` in `table`.`column`, or rewrite every row."""
        if str(rule.params.get("driver", "sql")) == "wp_cli":
            self._via_wp_cli(context, rule)
            return
        table = _identifier(_required(rule, "table"))
        column = _identifier(_required(rule, "column"))
        replacement = _quote(_required(rule, "to"))
        source = rule.params.get("from")
        clause = "" if source is None else f" WHERE `{column}` = '{_quote(str(source))}'"
        _run_sql(
            context,
            rule,
            f"UPDATE `{table}` SET `{column}` = '{replacement}'{clause}",
        )

    @staticmethod
    def _via_wp_cli(context: ExecutionContext, rule: SanitizeRule) -> None:
        source = _required(rule, "from")
        replacement = _required(rule, "to")
        result = context.runner.run(
            web_command(
                context.project,
                ["wp", "search-replace", source, replacement, "--skip-columns=guid", "--quiet"],
                workdir=context.project.docroot_path,
            )
        )
        if not result.ok:
            raise OperationError(
                f"sanitize rule {rule.name!r}: wp search-replace failed: {result.stderr.strip()}"
            )


_ADMIN_DRIVERS: dict[str, list[str]] = {
    "typo3": ["vendor/bin/typo3", "backend:createadmin"],
    "console": ["vendor/bin/typo3cms", "backend:createadmin"],
    "wp_cli": ["wp", "user", "create"],
    "drush": ["vendor/bin/drush", "user:create"],
}

# Each marker is tied to one real CLI's observed output, not a guess, and
# scoped per driver (`_ADMIN_DRIVERS` already keys by driver, so there is no
# reason for, say, wp-cli's phrasing to also be tolerated from drush):
# - "already exists": typo3's `backend:createadmin` (both the `typo3` and
#   `console` drivers), e.g. "A user with this name already exists".
# - "is already registered": wp-cli's `user create`, verified against
#   wp-cli/entity-command's `src/User_Command.php::create()` (default branch
#   `main`, checked 2026-10-01) - `username_exists()` fails with exactly
#   "The '<login>' username is already registered."
# - "already a user account": drush's `user:create`, verified against
#   drush-ops/drush's `src/Commands/user/UserCreateCommand.php::doExecute()`
#   (default branch `14.x` - drush has no `main`; checked 2026-10-01) -
#   raises "There is already a user account with the name <name>" (or
#   "... with the email <mail>", not reachable here since this driver never
#   passes --mail).
# The tuple used to also carry "already in use" and "duplicate user" -
# neither string appears anywhere in either source; they were invented, not
# observed, and have been replaced with the two verified markers above.
_ALREADY_EXISTS: dict[str, tuple[str, ...]] = {
    "typo3": ("already exists",),
    "console": ("already exists",),
    "wp_cli": ("is already registered",),
    "drush": ("already a user account",),
}


class CreateAdmin:
    """Create a local administrator so the developer can actually log in."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Create the admin user with the documented local password."""
        driver = str(rule.params.get("driver", "typo3"))
        head = _ADMIN_DRIVERS.get(driver)
        if head is None:
            raise OperationError(
                f"sanitize rule {rule.name!r}: unknown create_admin driver "
                f"{driver!r}; expected one of {', '.join(sorted(_ADMIN_DRIVERS))}"
            )
        user = _required(rule, "user")
        password = str(rule.params.get("password", LOCAL_ADMIN_PASSWORD))
        argv = self._argv(driver, head, user, password)
        # The framework CLI lives in the web container, not in this one; the
        # project root is the same absolute path in both, so it passes
        # through as docker's `-w` unchanged.
        result = context.runner.run(
            web_command(context.project, argv, workdir=context.project.root)
        )
        if result.ok:
            self._announce(user, password)
            return
        combined = f"{result.stdout}{result.stderr}".lower()
        # `.get`, not `[driver]`: the driver is validated against
        # `_ADMIN_DRIVERS` above, so a driver added there alone would
        # otherwise take this lookup down with a bare KeyError traceback
        # mid-sync. An empty default tolerates nothing, which is the safe
        # direction - the failure below is reported rather than swallowed.
        if any(marker in combined for marker in _ALREADY_EXISTS.get(driver, ())):
            print(f"  local admin already exists - user: {user}")
            return
        raise OperationError(
            f"sanitize rule {rule.name!r}: creating the admin user failed: {result.stderr.strip()}"
        )

    @staticmethod
    def _argv(driver: str, head: list[str], user: str, password: str) -> list[str]:
        if driver == "wp_cli":
            return [
                *head,
                user,
                f"{user}@example.invalid",
                "--role=administrator",
                f"--user_pass={password}",
            ]
        if driver == "drush":
            return [*head, user, f"--password={password}"]
        return [*head, user, password]

    @staticmethod
    def _announce(user: str, password: str) -> None:
        print(f"  local admin created - user: {user}  password: {password}")


class FlushCache:
    """Run the profile's cache-clearing command.

    Tolerates failure on purpose: a cache that could not be cleared must not
    abort a sync whose database import already succeeded.
    """

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Run the configured command, ignoring a non-zero exit."""
        command = _required(rule, "command")
        context.runner.run(
            web_command(context.project, ["sh", "-c", command], workdir=context.project.root)
        )


SANITIZE_OPERATIONS["clear_mail_config"] = ClearMailConfig()
SANITIZE_OPERATIONS["reset_passwords"] = ResetPasswords()
SANITIZE_OPERATIONS["search_replace"] = SearchReplace()
SANITIZE_OPERATIONS["create_admin"] = CreateAdmin()
SANITIZE_OPERATIONS["flush_cache"] = FlushCache()
