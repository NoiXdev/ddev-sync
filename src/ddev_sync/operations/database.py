"""Dump the remote database and stream it straight into a local file.

Credentials are read from the remote at run time and never written anywhere.
`RemoteCredentials` redacts its password in `repr` so that an exception
traceback or a debug log cannot leak a production secret.
"""

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError, confirm, redact
from ddev_sync.transport.ssh import ssh_command

DUMP_FILENAME = "dump.sql"
DUMP_SENTINEL = b"-- Dump completed"

MYSQLDUMP_FLAGS: tuple[str, ...] = (
    "--single-transaction",
    "--quick",
    "--allow-keywords",
    "--triggers",
    "--no-tablespaces",
)

CREDENTIAL_SOURCES = ("env", "explicit", "wp_cli")
_REQUIRED = ("name", "user", "password", "host", "port")


@dataclass(frozen=True)
class RemoteCredentials:
    """Database credentials read from the remote environment.

    `table_prefix` is only ever set by the `wp_cli` source (see
    `_from_wp_cli`) - WordPress's `$table_prefix`, needed to resolve
    `${WP_TABLE_PREFIX}` (operations/placeholders.py) against production
    rather than a local, possibly-regenerated wp-config.php. It stays `None`
    for every other source, which have no such concept.
    """

    name: str
    user: str
    password: str
    host: str
    port: str
    table_prefix: str | None = None

    def __repr__(self) -> str:
        """Redact the password so a traceback cannot leak it."""
        return (
            f"RemoteCredentials(name={self.name!r}, user={self.user!r}, "
            f"password='<REDACTED>', host={self.host!r}, port={self.port!r}, "
            f"table_prefix={self.table_prefix!r})"
        )


_CREDENTIALS_CACHE_KEY = "remote_credentials"


def read_remote_credentials(context: ExecutionContext) -> RemoteCredentials:
    """Read database credentials using the profile's configured source.

    Memoised on `context.cache`: db.dump, the Part 3 preflight table probe,
    and (for a `wp_cli` source) the `${WP_TABLE_PREFIX}` placeholder resolver
    can all ask for this in the same run, and the answer cannot change
    between them - without caching, that is up to three identical ssh round
    trips (or three separate `wp config get` invocations against production)
    for the same data.
    """
    cached = context.cache.get(_CREDENTIALS_CACHE_KEY)
    if isinstance(cached, RemoteCredentials):
        return cached
    credentials = _read_remote_credentials(context)
    # No emptiness check: every source above refuses an empty password, and the
    # scrubber ignores an empty entry itself (`VerboseRunner._scrub`).
    context.secrets.add(credentials.password)
    context.cache[_CREDENTIALS_CACHE_KEY] = credentials
    return credentials


def _read_remote_credentials(context: ExecutionContext) -> RemoteCredentials:
    source = context.config("database.remote_credentials", default="env")
    if source == "explicit":
        return _from_config(context)
    if source == "env":
        return _from_remote_env(context)
    if source == "wp_cli":
        return _from_wp_cli(context)
    raise OperationError(
        f"unknown credential source {source!r}; expected one of {', '.join(CREDENTIAL_SOURCES)}"
    )


def _from_config(context: ExecutionContext) -> RemoteCredentials:
    values = {key: context.config(f"database.credentials.{key}", default="") for key in _REQUIRED}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise OperationError(
            f"database.credentials is missing {', '.join(missing)}; they are "
            "secrets, so set them in .ddev/ddev-sync.local.yaml rather than the "
            "project file, and check that your repository ignores it - nothing "
            "here does that for you"
        )
    return RemoteCredentials(**values)


def _from_remote_env(context: ExecutionContext) -> RemoteCredentials:
    candidates = _env_candidates(context)
    path, contents = _fetch_first_env_file(context, candidates)
    parsed = _parse_env(contents)
    mapping = {
        key: context.config(f"database.env_map.{key}", default=_default_env_key(key))
        for key in _REQUIRED
    }
    values: dict[str, str] = {}
    for field_name, variable in mapping.items():
        if not parsed.get(variable):
            raise OperationError(f"{variable} is not set in {path} on {context.target.host}")
        values[field_name] = parsed[variable]
    return RemoteCredentials(**values)


def _default_env_key(field_name: str) -> str:
    return f"DATABASE_{field_name.upper()}"


def _env_candidates(context: ExecutionContext) -> list[str]:
    node = context.merged.data.get("env", {})
    configured = node.get("file") if isinstance(node, dict) else None
    if isinstance(configured, str):
        return [configured]
    if isinstance(configured, list):
        return [str(entry) for entry in configured]
    return [".env.local", ".env"]


def _fetch_first_env_file(context: ExecutionContext, candidates: list[str]) -> tuple[str, str]:
    """Build a remote probe script that tries each candidate path in turn.

    Each `if ... fi` must be terminated with `;` (or a newline) before the
    next one starts: `fi if` with only a space between them is a shell
    syntax error on both `sh` and `bash`, not something either parses as two
    statements. A `FakeCommandRunner` never executes this string, so the
    bug this leaves behind is invisible to unit tests - only a real shell
    catches it, which is exactly why the integration suite exists.

    Each path is `shlex.quote`d rather than hand-wrapped in `"..."`: a
    double-quoted string still lets the shell expand `$(...)` and `` ` ``
    inside it, so a path containing either would still execute as part of
    the probe. `shlex.quote` is also the only form that survives a literal
    `"` in the path at all.
    """
    paths = [context.remote_data(name) for name in candidates]
    tests = "; ".join(
        f"if [ -f {shlex.quote(path)} ]; then echo {shlex.quote(path)}; "
        f"cat {shlex.quote(path)}; exit 0; fi"
        for path in paths
    )
    script = f"{tests}; exit 9"
    # The output is the whole production .env: every secret in it, not only the
    # database password.
    result = context.runner.run(ssh_command(context.target, ["sh", "-c", script]), sensitive=True)
    if not result.ok:
        looked = ", ".join(paths)
        raise OperationError(
            f"no environment file found on {context.target.host}; looked at: {looked}"
        )
    first_line, _, body = result.stdout.partition("\n")
    return first_line.strip(), body


def _parse_env(contents: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in contents.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        parsed[key.strip()] = _unquote(value.strip())
    return parsed


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


_WP_CREDENTIAL_KEYS = ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")
# `--type` defaults to `all`, which ERRORS - "Found both a constant and a
# variable 'table_prefix' ... Use --type=<type> to disambiguate." - the
# instant a project also happens to define a same-named constant. The DB_*
# keys above need no such disambiguation (they are conventionally constants
# only); `table_prefix` specifically is a documented wp-config.php
# *variable*, so leaving it ambiguous is a real, if unusual, failure mode
# this closes.
_WP_TABLE_PREFIX_GET = "wp config get table_prefix --type=variable"


def _from_wp_cli(context: ExecutionContext) -> RemoteCredentials:
    """Read credentials and the WordPress table prefix, in one ssh round trip.

    `table_prefix` rides along with the credential reads rather than opening
    a second channel for operations/placeholders.py's `${WP_TABLE_PREFIX}`:
    both need exactly one ssh connection to production, and `read_remote_
    credentials`'s own memoisation means db.dump, the Part 3 preflight probe
    and the placeholder resolver share this single result for the whole run.

    Deliberately NOT `wp db prefix`: that returns the *current site's*
    prefix, which differs on a WordPress multisite install. The users table
    is special there - it lives in `wpdb::$global_tables`, not the per-site
    `$tables`, so it is always named from `base_prefix` regardless of which
    site is current (a multisite subsite 2 still shares `wp_users`, never
    `wp_2_users`). The `table_prefix` variable in wp-config.php *is*
    `base_prefix`, so reading it directly - not `wp db prefix` - is the
    correct source for the one table this placeholder serves.
    """
    remote_root = context.remote_code("")
    gets = " && ".join(f"wp config get {key}" for key in _WP_CREDENTIAL_KEYS)
    script = f"cd {shlex.quote(remote_root)} && {gets} && {_WP_TABLE_PREFIX_GET}"
    # The output is the database password, alone on a line.
    result = context.runner.run(ssh_command(context.target, ["sh", "-c", script]), sensitive=True)
    if not result.ok:
        raise OperationError(
            f"could not read wp-config on {context.target.host} via wp-cli in "
            f"{remote_root}: {result.stderr.strip()}. This is the normal result on "
            "an env-based setup (e.g. Bedrock), where wp-config.php has no static "
            "literal for wp-cli to read back. Set 'database.wp_table_prefix' in "
            ".ddev/ddev-sync.yaml to supply the table prefix directly, and "
            "'database.remote_credentials: explicit' with 'database.credentials' "
            "for the database connection itself."
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    expected = (*_WP_CREDENTIAL_KEYS, "table_prefix")
    if len(lines) < len(expected):
        raise OperationError(
            f"wp config get returned {len(lines)} values, expected "
            f"{len(expected)} ({', '.join(expected)})"
        )
    name, user, password, host_value, table_prefix = lines[: len(expected)]
    host, _, port = host_value.partition(":")
    return RemoteCredentials(
        name=name,
        user=user,
        password=password,
        host=host,
        port=port or "3306",
        table_prefix=table_prefix,
    )


class DbDumpOperation:
    """Streams `mysqldump` output from the remote straight into a local file."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Dump the remote database into `downloads_dir/dump.sql`."""
        credentials = read_remote_credentials(context)
        destination = context.downloads_dir / DUMP_FILENAME
        argv = ssh_command(context.target, _mysqldump_argv(credentials))
        result = context.runner.run(argv, stdout_path=destination)
        if not result.ok:
            destination.unlink(missing_ok=True)
            raise OperationError(
                f"the remote dump failed (exit {result.returncode}): "
                f"{redact(result.stderr, credentials.password).strip()}"
            )
        if not dump_is_complete(destination):
            size = destination.stat().st_size
            destination.unlink(missing_ok=True)
            raise OperationError(
                f"the dump is incomplete ({size} bytes, no completion marker) - "
                "the connection probably dropped. Nothing was imported; run the sync again."
            )


def _mysqldump_argv(credentials: RemoteCredentials) -> list[str]:
    return [
        "mysqldump",
        f"--user={credentials.user}",
        f"--password={credentials.password}",
        f"--host={credentials.host}",
        f"--port={credentials.port}",
        *MYSQLDUMP_FLAGS,
        credentials.name,
    ]


def dump_is_complete(path: Path) -> bool:
    """True when the dump ends with mysqldump's completion marker."""
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        try:
            handle.seek(-512, 2)
        except OSError:
            handle.seek(0)
        tail = handle.read()
    return DUMP_SENTINEL in tail


LOCAL_DB_USER = "db"
LOCAL_DB_PASSWORD = "db"
LOCAL_DB_PORT = "3306"
# The MariaDB client in this image - 11.8 - requires a verified TLS connection
# by default, and what that costs depends on the server. Measured with this
# image's client against real DDEV database containers (v1.25.4) and, in the
# last row, a stock `mariadb:11.8`:
#
#   server                      no option   --skip-ssl   this option
#   DDEV mariadb 10.8           fails       plaintext    plaintext
#   DDEV mariadb 10.11          fails       plaintext    plaintext
#   DDEV mariadb 11.8           TLS         plaintext    TLS
#   mariadb:11.8                TLS         plaintext    TLS
#
# So there are two different failures, not one. DDEV's MariaDB 10.x images
# configure no certificate at all, and the client then refuses outright: that
# is the bug this constant exists for, found on a real project running 10.8,
# where it took out the drop, the create, the import and every sanitize rule's
# SQL. DDEV's MySQL images do have one, but self-signed, and there the client
# fails verification instead - a separate review measured that, and 8.0 is
# refused on its authentication plugin regardless, so that project type does
# not work at all.
#
# DDEV's MariaDB 11.x images are the interesting case: they generate a
# certificate, the unflagged client accepts it - the handshake is verified
# through the password, so no CA is needed - and it connects over TLS. That is
# what this option gives up there, and it is the honest cost of it: one argv
# that works everywhere, at the price of the client's own verification on the
# servers that could offer it. `--skip-ssl` was the first choice here and is
# worse on exactly those servers, because it drops to plaintext; it would also
# be refused by a server with `require_secure_transport` on, where this one
# connects.
#
# The remote side is not covered by this. That traffic goes through ssh, and
# the remote's own client decides its policy - see
# `preflight.py::_show_tables_argv`, which deliberately does not pass this.
#
# The spelling is the MariaDB client's, which the Dockerfile pins.
LOCAL_DB_TLS_OPTION = "--skip-ssl-verify-server-cert"

_DEFINER = re.compile(rb"\sDEFINER=`[^`]*`@`[^`]*`")


class DbImportOperation:
    """Drops, recreates and repopulates the local DDEV database."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Import `downloads_dir/dump.sql` into the local database."""
        dump = context.downloads_dir / DUMP_FILENAME
        if not dump.is_file():
            raise OperationError(
                f"{DUMP_FILENAME} is not in {context.downloads_dir}; "
                "the db.dump step has to run before db.import"
            )
        if not dump_is_complete(dump):
            raise OperationError(f"{dump} is incomplete; delete it and run the sync again")

        database = context.config("database.target_database", default="db")
        # `_recreate` first, because it is where the "this will drop the local
        # database" confirmation lives. Rewriting the dump before it opened the
        # longest window in the whole step - the whole file, and with `--yes`
        # no prompt in front of it at all - in which a Ctrl-C was reported as
        # "the local database may be partly replaced with production data"
        # while nothing had been dropped or imported. The documented condition
        # for that sentence is "during the import, once the drop has begun";
        # doing the drop first is what makes it true, rather than widening the
        # hedge to cover a window that should not be in it. Nothing between
        # here and `_import` reads the dump, so the order is free: `_recreate`
        # only runs mysqladmin drop/create, and the completeness check above
        # already ran against the unmodified file.
        self._recreate(context, database)
        try:
            strip_definers(dump)
        except OSError as error:
            # The drop above has already happened, so this is not a failure
            # that left everything as it was - and the rewrite needs as much
            # free space again as the dump occupies, which makes a full disk
            # its likeliest cause. `dump.sql` is unchanged whatever raised: the
            # rewrite builds a separate file and only touches the dump in the
            # final atomic replace.
            raise OperationError(
                f"could not rewrite {dump} to remove DEFINER clauses: {error}. "
                f"The local database {database!r} is empty: the drop runs before "
                "this rewrite, so nothing was imported. The dump itself is "
                "unchanged, but it may still carry DEFINER clauses that fail to "
                "import, so fix the cause and run the sync again rather than "
                "importing the file directly. A full disk is the likeliest cause - "
                "the rewrite writes a second copy beside the dump before replacing "
                "it."
            ) from error
        self._import(context, database, dump)
        if not context.keep_dump:
            dump.unlink(missing_ok=True)

    def _recreate(self, context: ExecutionContext, database: str) -> None:
        if not context.assume_yes and not confirm_destructive(
            f"This will drop the local database {database!r}. Continue?"
        ):
            raise OperationError("import cancelled by the user")
        for verb in ("drop", "create"):
            result = context.runner.run(
                [
                    "mysqladmin",
                    f"--user={LOCAL_DB_USER}",
                    f"--password={LOCAL_DB_PASSWORD}",
                    f"--host={context.project.db_container}",
                    f"--port={LOCAL_DB_PORT}",
                    LOCAL_DB_TLS_OPTION,
                    "--force",
                    verb,
                    database,
                ]
            )
            # `drop` on a database that is not there exits non-zero with --force;
            # that is the normal first-run case, not a failure worth stopping for.
            if not result.ok and verb == "create":
                raise OperationError(
                    f"could not create the local database {database!r}: {result.stderr.strip()}"
                )

    def _import(self, context: ExecutionContext, database: str, dump: Path) -> None:
        result = context.runner.run(
            [
                "mysql",
                f"--user={LOCAL_DB_USER}",
                f"--password={LOCAL_DB_PASSWORD}",
                f"--host={context.project.db_container}",
                f"--port={LOCAL_DB_PORT}",
                LOCAL_DB_TLS_OPTION,
                database,
                "--execute",
                f"source {dump}",
            ]
        )
        if not result.ok:
            raise OperationError(f"importing {dump.name} failed: {result.stderr.strip()}")


def strip_definers(path: Path) -> None:
    """Remove `DEFINER=` clauses, which fail to import as a non-super user.

    Rewritten in chunks rather than read whole: a 400 MB dump through a single
    `read_bytes` is an avoidable memory spike, and DEFINER clauses never span
    a line boundary.
    """
    temporary = path.with_suffix(path.suffix + ".tmp")
    with path.open("rb") as source, temporary.open("wb") as sink:
        for line in source:
            sink.write(_DEFINER.sub(b"", line))
    temporary.replace(path)


def confirm_destructive(question: str) -> bool:
    """Ask the user to confirm a destructive local action.

    A module-level function rather than a method so that tests can replace it
    without constructing a console, and so the interactive layer in Task 13 can
    swap in a richer prompt. Delegates to `operations/base.py::confirm` - see
    that function for why `EOFError` is handled there rather than here.
    """
    return confirm(question, subject="a destructive action")
