"""The closed catalog of named sanitize operations.

Each entry exists because a SQL statement alone cannot express it - either it
needs several statements, or it needs to shell out to a framework CLI. Adding
an entry here is a deliberate act; profiles cannot invent new ones.
"""

from collections.abc import Sequence

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.sanitize import (
    SANITIZE_OPERATIONS,
    SanitizeRule,
    local_mysql_argv,
)

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
    database = context.config("database.target_database", default="db")
    result = context.runner.run(local_mysql_argv(context, database, sql=sql))
    if not result.ok:
        raise OperationError(f"sanitize rule {rule.name!r} failed: {result.stderr.strip()}")


class ClearMailConfig:
    """Delete mailer settings so a local instance cannot send real email."""

    def run(self, context: ExecutionContext, rule: SanitizeRule) -> None:
        """Delete the configured keys from the configured table."""
        table = _identifier(_required(rule, "table"))
        column = _identifier(_required(rule, "key_column"))
        keys = rule.params.get("keys", [])
        if not isinstance(keys, Sequence) or isinstance(keys, str) or not keys:
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
            ["wp", "search-replace", source, replacement, "--skip-columns=guid", "--quiet"],
            cwd=context.project.docroot_path,
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

_ALREADY_EXISTS = ("already exists", "already in use", "duplicate")


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
        result = context.runner.run(argv, cwd=context.project.root)
        if result.ok:
            self._announce(user, password)
            return
        combined = f"{result.stdout}{result.stderr}".lower()
        if any(marker in combined for marker in _ALREADY_EXISTS):
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
        context.runner.run(["sh", "-c", command], cwd=context.project.root)


SANITIZE_OPERATIONS["clear_mail_config"] = ClearMailConfig()
SANITIZE_OPERATIONS["reset_passwords"] = ResetPasswords()
SANITIZE_OPERATIONS["search_replace"] = SearchReplace()
SANITIZE_OPERATIONS["create_admin"] = CreateAdmin()
SANITIZE_OPERATIONS["flush_cache"] = FlushCache()
