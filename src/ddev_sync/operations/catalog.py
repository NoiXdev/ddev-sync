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
                f"sanitize rule {rule.name!r}: wp search-replace failed: {result.failure_detail()}"
            )


# `typo3` is TYPO3 core's own `backend:user:create` (core in 12.4+: it ran
# against stock typo3/cms-base-distribution 12.4.45 and 13.4.35, and its options
# were read from `--help` on 13.4.35). It takes options, not positionals, and
# `--admin` is what makes the account an administrator. `backend:createadmin` -
# what this driver used to call - is helhum/typo3-console's command, which a
# stock install does not have: there it fails with `Command
# "backend:createadmin" is not defined`. The `console` driver keeps it because
# that is exactly what `typo3cms` provides.
_ADMIN_DRIVERS: dict[str, list[str]] = {
    "typo3": ["vendor/bin/typo3", "backend:user:create"],
    "console": ["vendor/bin/typo3cms", "backend:createadmin"],
    "wp_cli": ["wp", "user", "create"],
    "drush": ["vendor/bin/drush", "user:create"],
}

# A driver whose create command cannot make the account an administrator needs
# a second command to give it the role. Only drush is in this position:
# `typo3` has `--admin`, `wp_cli` has `--role`, and `console`'s
# `backend:createadmin` creates an administrator by definition. Observed
# against drush 13.8.0 on Drupal 11.4.8: `user:create` has no role option (an
# account made with it has only `authenticated`), and `user:role:add <role>
# <names>` takes the role's machine name first and a comma-delimited list of
# user names second. Given a role the user already has, it exits 0 and prints
# `Added administrator role to ddev` again, so a re-sync needs no tolerance
# here; given a role that does not exist it exits 1 (`Unable to load the
# user_role: <role>`), and that is a real failure, because an account without
# the role is exactly what this exists to prevent. It lives here rather than
# in a profile's `setup.commands` so the user name is written once.
_ADMIN_ROLE_COMMANDS: dict[str, list[str]] = {
    "drush": ["vendor/bin/drush", "user:role:add"],
}

# The role the `drush` driver grants, unless the rule's `role` parameter says
# otherwise. `administrator` is the machine name Drupal's `standard` install
# profile creates (checked against a stock install); other install profiles
# may not have such a role, and nothing here has been run against one.
#
# Changing it is not a one-line override. A project's `sanitize.rules` extends
# the profile's list by default, so a second `create_admin` rule with another
# `role` would run *after* the profile's own, which would still fail on the
# missing role first. The only mechanism there is is `mode: replace` on
# `sanitize.rules`, which swaps the whole list - the project then has to carry
# the profile's other rules itself.
_DEFAULT_ADMIN_ROLE = "administrator"

# Each marker is tied to one real CLI's observed output, not a guess, and
# scoped per driver (`_ADMIN_DRIVERS` already keys by driver, so there is no
# reason for, say, wp-cli's phrasing to also be tolerated from drush):
# - "is already taken": TYPO3 core's `backend:user:create`, observed against a
#   real 13.4.35 install: a second run with the same username exits 255 and
#   prints `The username "ddev" is already taken. Please use another
#   username.` The earlier marker "already exists" does not occur in that
#   message, so with it a second sync would have failed.
# - "already exists": typo3_console's `backend:createadmin` (the `console`
#   driver), e.g. "A user with this name already exists".
# - "is already registered": wp-cli's `user create`, verified against
#   wp-cli/entity-command's `src/User_Command.php::create()` (default branch
#   `main`, checked 2026-10-01) - `username_exists()` fails with exactly
#   "The '<login>' username is already registered." Observed against wp-cli
#   2.12.0 on WordPress 7.1.2: a second `wp user create ddev ...` exits 1 and
#   prints `Error: The 'ddev' username is already registered.` on stderr.
# - "already a user account": drush's `user:create`, verified against
#   drush-ops/drush's `src/Commands/user/UserCreateCommand.php::doExecute()`
#   (default branch `14.x` - drush has no `main`; checked 2026-10-01) -
#   raises "There is already a user account with the name <name>" (or
#   "... with the email <mail>", not reachable here since this driver never
#   passes --mail). Observed against drush 13.8.0 on Drupal 11.4.8, where the
#   check is `UserCommands::createValidate()` instead: a second
#   `vendor/bin/drush user:create ddev --password=...` exits 1 with nothing on
#   stdout and, on stderr, `In UserCommands.php line 258:` followed by
#   `There is already a user account with the name ddev`.
# The tuple used to also carry "already in use" and "duplicate user" -
# neither string appears anywhere in either source; they were invented, not
# observed, and have been replaced with the two verified markers above.
_ALREADY_EXISTS: dict[str, tuple[str, ...]] = {
    "typo3": ("is already taken",),
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
        # Before anything runs, so a bad `role` does not leave an account made.
        role = self._role(rule) if driver in _ADMIN_ROLE_COMMANDS else None
        argv = self._argv(driver, head, user, password)
        # The framework CLI lives in the web container, not in this one; the
        # project root is translated onto the container's mount point and passed
        # as docker's `-w`.
        result = context.runner.run(
            web_command(context.project, argv, workdir=context.project.root)
        )
        if result.ok:
            self._announce(user, password)
        else:
            combined = f"{result.stdout}{result.stderr}".lower()
            # `.get`, not `[driver]`: the driver is validated against
            # `_ADMIN_DRIVERS` above, so a driver added there alone would
            # otherwise take this lookup down with a bare KeyError traceback
            # mid-sync. An empty default tolerates nothing, which is the safe
            # direction - the failure below is reported rather than swallowed.
            if not any(marker in combined for marker in _ALREADY_EXISTS.get(driver, ())):
                raise OperationError(
                    f"sanitize rule {rule.name!r}: creating the admin user failed: "
                    f"{result.failure_detail()}"
                )
            print(f"  local admin already exists - user: {user}")
        # Also for an account that already existed: it may have been made by an
        # earlier sync, or by hand, without the role.
        self._grant_role(context, rule, driver, user, role)

    @staticmethod
    def _role(rule: SanitizeRule) -> str:
        role = str(rule.params.get("role", _DEFAULT_ADMIN_ROLE))
        if not role.strip():
            raise OperationError(
                f"sanitize rule {rule.name!r}: the 'role' parameter is empty; name the "
                f"machine name of an existing role, or leave it out for "
                f"{_DEFAULT_ADMIN_ROLE!r}"
            )
        return role

    @staticmethod
    def _grant_role(
        context: ExecutionContext, rule: SanitizeRule, driver: str, user: str, role: str | None
    ) -> None:
        # `role` is only set for a driver in `_ADMIN_ROLE_COMMANDS` (see `run`).
        if role is None:
            return
        result = context.runner.run(
            web_command(
                context.project,
                [*_ADMIN_ROLE_COMMANDS[driver], role, user],
                workdir=context.project.root,
            )
        )
        if not result.ok:
            raise OperationError(
                f"sanitize rule {rule.name!r}: giving {user!r} the role {role!r} failed: "
                f"{result.failure_detail()}\n"
                f"The role {role!r} must exist in this site; the default, "
                f"{_DEFAULT_ADMIN_ROLE!r}, is the one Drupal's 'standard' install profile "
                "creates. For another role name, replace the whole sanitize.rules list "
                "(`mode: replace`) in the project's ddev-sync.yaml with the profile's rules "
                "copied over and `role:` set on the create_admin rule - a second "
                "create_admin rule added with the default `extend` mode would run after "
                "this one and not instead of it. The database is already imported and the "
                "rules before this one have run."
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
        if driver == "typo3":
            # Every option is given, so the command never has to prompt; the
            # explicit flag is belt and braces for a container with no tty.
            return [
                *head,
                f"--username={user}",
                f"--password={password}",
                "--admin",
                "--no-interaction",
            ]
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
