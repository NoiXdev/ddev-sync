"""Dump the remote database and stream it straight into a local file.

Credentials are read from the remote at run time and never written anywhere.
`RemoteCredentials` redacts its password in `repr` so that an exception
traceback or a debug log cannot leak a production secret.
"""

import re
import shlex
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddev_sync.config.models import Provider
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

DATABASE_ENV_FILE_KEY = "database.env_file"

_DEFAULT_ENV_FILES = (".env.local", ".env")

# Where the docker endpoint writes the variables it was told to export, and the
# first line it writes to say so (endpoint/ holds the writing side of this
# contract).
HANDOVER_PATH = "/run/ddev-sync/app.env"
HANDOVER_MARKER = "# ddev-sync endpoint handover v1"


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
            "secrets, so set them in .ddev/ddev-sync/local.yaml rather than the "
            "project file - the add-on's shipped .gitignore already ignores "
            "that path",
            doc="database-credentials-missing",
        )
    return RemoteCredentials(**values)


def _from_remote_env(context: ExecutionContext) -> RemoteCredentials:
    path, contents = _fetch_first_env_file(context, remote_env_paths(context))
    # The handover file is the one place a value may carry quotes, `#` or edge
    # spaces that belong to it, so it must not go through the .env parser.
    parsed = parse_handover(contents, path) if path == HANDOVER_PATH else _parse_env(contents)
    mapping = {
        key: context.config(f"database.env_map.{key}", default=_default_env_key(key))
        for key in _REQUIRED
    }
    values: dict[str, str] = {}
    for field_name, variable in mapping.items():
        if not parsed.get(variable):
            raise OperationError(
                f"{variable} is not set in {path} on {context.target.host}",
                doc="variable-missing-in-the-remote-env-file",
            )
        values[field_name] = parsed[variable]
    return RemoteCredentials(**values)


def _default_env_key(field_name: str) -> str:
    return f"DATABASE_{field_name.upper()}"


def remote_env_candidates(context: ExecutionContext) -> list[str]:
    """The file names to look for on the remote, strongest source first.

    `database.env_file` names the file *on the target*; `env.file` is the
    local project's own, and has historically meant both - this function and
    `operations/env.py::_find_env_file` read the same key, one joining it onto
    `ResolvedTarget.data_root` and the other matching it against the project
    root's entries. The deployer layout's directory difference is handled by
    `data_root`; the filename was not, and a project whose local and remote
    files are named differently had no way to say so.

    The fallback to `env.file` is deliberate rather than tidy: a project that
    set it in order to steer *this* lookup keeps working untouched. Only a
    project that needs the two to differ has to learn the new key.

    Public, unlike the `_`-prefixed function it replaces, because
    `cli/init.py` needs to tell the user which names were looked for.
    """
    for key in (DATABASE_ENV_FILE_KEY, "env.file"):
        configured = context.setting(key)
        if isinstance(configured, str):
            return [configured]
        if isinstance(configured, list):
            return [str(entry) for entry in configured]
    return list(_DEFAULT_ENV_FILES)


def remote_env_paths(context: ExecutionContext) -> list[str]:
    """The remote paths to probe, in order: `remote_env_candidates` on the data root.

    Under `provider: docker` the endpoint's handover file comes last, so a
    mounted `.env` keeps winning and the file only answers when the stack has
    none. A candidate that is already absolute is taken as is: that is how a
    project names the handover path itself in `database.env_file`.
    """
    paths = [
        name if name.startswith("/") else context.remote_data(name)
        for name in remote_env_candidates(context)
    ]
    if context.target.provider == Provider.DOCKER and HANDOVER_PATH not in paths:
        paths.append(HANDOVER_PATH)
    return paths


def parse_handover(contents: str, path: str) -> dict[str, str]:
    """Parse the file an endpoint writes from its environment, strictly.

    The format is the endpoint's own and not `.env`: one `NAME=value` line per
    variable, the value being everything after the first `=` with nothing
    stripped or unquoted, since a production password may well start with a
    quote or end in a space. Splitting on `"\\n"` alone keeps a stray `\\r` from
    hiding in a value, and the file must end in a newline, so a truncated
    write is refused instead of yielding a cut-off password.
    """
    lines = contents.split("\n")
    if lines.pop() != "" or not lines or lines[0] != HANDOVER_MARKER:
        raise _handover_unreadable(path, "it does not start with the handover marker")
    parsed: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition("=")
        if not separator or "\r" in line:
            raise _handover_unreadable(path, "it has a line that is not NAME=value")
        parsed[name] = value
    return parsed


def _handover_unreadable(path: str, reason: str) -> OperationError:
    return OperationError(
        f"{path} cannot be read as a handover file: {reason}",
        doc="endpoint-handover-file-unreadable",
    )


def _fetch_first_env_file(context: ExecutionContext, paths: list[str]) -> tuple[str, str]:
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
        hint = ""
        if HANDOVER_PATH in paths:
            hint = (
                ". To hand the stack's variables over instead, set DDEV_SYNC_EXPORT_ENV "
                "on the endpoint"
            )
        raise OperationError(
            f"no environment file found on {context.target.host}; looked at: {looked}{hint}",
            doc="no-environment-file-on-the-remote",
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
    # The output is five lines - name, user, password, host, table prefix - so the
    # database password is one of them, among values nobody has registered yet.
    result = context.runner.run(ssh_command(context.target, ["sh", "-c", script]), sensitive=True)
    if not result.ok:
        raise OperationError(
            f"could not read wp-config on {context.target.host} via wp-cli in "
            f"{remote_root}: {result.stderr.strip()}. This is the normal result on "
            "an env-based setup (e.g. Bedrock), where wp-config.php has no static "
            "literal for wp-cli to read back. Set 'database.wp_table_prefix' in "
            ".ddev/ddev-sync.yaml to supply the table prefix directly, and "
            "'database.remote_credentials: explicit' with 'database.credentials' "
            "for the database connection itself.",
            doc="wp-cli-cannot-read-wp-config",
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


TLS_DOC = "tls-certificate-verification-fails"


def certificate_hint(stderr: str) -> str | None:
    """The `verify_server_cert` hint when `stderr` is a certificate failure, else None."""
    lowered = stderr.lower()
    # The phrases the clients print for a certificate failure, not a bare
    # "ssl": a host named `ssl-db` is not a TLS problem.
    if not any(p in lowered for p in ("certificate", "ssl connection error", "tls/ssl error")):
        return None
    # The mysql clients verify the server certificate when the server offers
    # TLS, and a self-signed one fails with a message that never mentions the
    # setting that relaxes it.
    return (
        f"If the server's certificate is self-signed, set '{VERIFY_SERVER_CERT_KEY}: false' "
        "in .ddev/ddev-sync.yaml (only appropriate for a server you trust, such as your own)."
    )


def remote_failure(message: str, stderr: str, *, doc: str | None = None) -> OperationError:
    """The error for a failed remote mysql command, carrying the TLS hint and slug if it fits.

    One place for every remote query (dump, table and column probes,
    credential check), so a self-signed server gets the same hint whichever
    of them meets it first.
    """
    hint = certificate_hint(stderr)
    if hint is None:
        return OperationError(message, doc=doc)
    return OperationError(f"{message}\n{hint}", doc=TLS_DOC)


class DbDumpOperation:
    """Streams `mysqldump` output from the remote straight into a local file."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Dump the remote database into `downloads_dir/dump.sql`."""
        credentials = read_remote_credentials(context)
        destination = context.downloads_dir / DUMP_FILENAME
        argv = ssh_command(
            context.target, _mysqldump_argv(credentials, remote_tls_options(context))
        )
        result = context.runner.run(argv, stdout_path=destination)
        if not result.ok:
            destination.unlink(missing_ok=True)
            stderr = redact(result.stderr, credentials.password).strip()
            message = f"the remote dump failed (exit {result.returncode}): {stderr}"
            raise remote_failure(message, stderr)
        if not dump_is_complete(destination):
            size = destination.stat().st_size
            destination.unlink(missing_ok=True)
            raise OperationError(
                f"the dump is incomplete ({size} bytes, no completion marker) - "
                "the connection probably dropped. Nothing was imported; run the sync again.",
                doc="dump-is-incomplete",
            )


def _mysqldump_argv(credentials: RemoteCredentials, tls: list[str]) -> list[str]:
    return [
        "mysqldump",
        f"--user={credentials.user}",
        f"--password={credentials.password}",
        f"--host={credentials.host}",
        f"--port={credentials.port}",
        *tls,
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
# the remote's own client decides its policy - unless the project turns its
# verification off, see `remote_tls_options` below.
#
# The spelling is the MariaDB client's, which the Dockerfile pins.
LOCAL_DB_TLS_OPTION = "--skip-ssl-verify-server-cert"

VERIFY_SERVER_CERT_KEY = "database.verify_server_cert"

# What `database.verify_server_cert: false` adds to every remote `mysql` and
# `mysqldump`. Opt-in, because it gives up the client's check of the server's
# certificate, and on by default nowhere: the remote client is whatever that
# machine has, and its policy is the operator's.
#
# It exists for a MariaDB client from 11.4 on against a server whose
# certificate it cannot verify. That client verifies by default and, against a
# MariaDB server, does it through the password; against MySQL - measured with
# the endpoint's 11.4 client and Percona MySQL 8.4 on the Docker host - it
# meets the server's own self-signed certificate and refuses with "TLS/SSL
# error: self-signed certificate in certificate chain".
#
# `--loose-` because the remote client may be MySQL's, which has no such
# option and would otherwise refuse to start; with the prefix it is ignored.
REMOTE_SKIP_VERIFY_OPTION = "--loose-skip-ssl-verify-server-cert"


def remote_tls_options(context: ExecutionContext) -> list[str]:
    """The TLS options for a remote `mysql` or `mysqldump`, from the project's setting."""
    verify = context.setting(VERIFY_SERVER_CERT_KEY)
    if verify is None or verify is True:
        return []
    if verify is False:
        return [REMOTE_SKIP_VERIFY_OPTION]
    raise OperationError(f"{VERIFY_SERVER_CERT_KEY} must be true or false, got {verify!r}")


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
            raise OperationError(
                f"{dump} is incomplete; delete it and run the sync again",
                doc="dump-is-incomplete",
            )

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
                "it, and removes that copy again if it can."
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

    Anything that goes wrong takes the half-written copy with it, where it
    can. The dump's *contents* are never at risk - it is only ever read, and
    replaced in one atomic step at the end - but the copy is as large as the
    dump, and the likeliest reason to get here is a disk with no room left on
    it. Leaving a second copy of a 400 MB dump behind, in that situation,
    would be the worst moment to do it. The removal is suppressed rather than
    guarded: it must not become the error the caller sees in place of the real
    one - which is also why the caller's message says the copy is removed "if
    it can" rather than promising it. A directory in the temporary file's
    place, or a read-only parent, defeats both the rewrite and the cleanup.

    One thing the atomic replace does change, unrelated to failure: a
    `dump.sql` that is a symbolic link becomes a regular file, and whatever it
    pointed at is left as it was. That is unchanged behaviour, not something
    this cleanup introduced.
    """
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with path.open("rb") as source, temporary.open("wb") as sink:
            for line in source:
                sink.write(_DEFINER.sub(b"", line))
        temporary.replace(path)
    except BaseException:
        with suppress(OSError):
            temporary.unlink()
        raise


def confirm_destructive(question: str) -> bool:
    """Ask the user to confirm a destructive local action.

    A module-level function rather than a method so that tests can replace it
    without constructing a console, and so the interactive layer in Task 13 can
    swap in a richer prompt. Delegates to `operations/base.py::confirm` - see
    that function for why `EOFError` is handled there rather than here.
    """
    return confirm(question, subject="a destructive action")
