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

from ddev_sync.operations.base import ExecutionContext, OperationError
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
    """Database credentials read from the remote environment."""

    name: str
    user: str
    password: str
    host: str
    port: str

    def __repr__(self) -> str:
        """Redact the password so a traceback cannot leak it."""
        return (
            f"RemoteCredentials(name={self.name!r}, user={self.user!r}, "
            f"password='<REDACTED>', host={self.host!r}, port={self.port!r})"
        )


def read_remote_credentials(context: ExecutionContext) -> RemoteCredentials:
    """Read database credentials using the profile's configured source."""
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
            f"database.credentials is missing {', '.join(missing)}; "
            "set them in .ddev/ddev-sync.yaml"
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
    result = context.runner.run(ssh_command(context.target, ["sh", "-c", script]))
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


_WP_KEYS = ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")


def _from_wp_cli(context: ExecutionContext) -> RemoteCredentials:
    """Read credentials with `wp config get`, in one ssh round trip."""
    remote_root = context.remote_code("")
    gets = " && ".join(f"wp config get {key}" for key in _WP_KEYS)
    script = f"cd {shlex.quote(remote_root)} && {gets}"
    result = context.runner.run(ssh_command(context.target, ["sh", "-c", script]))
    if not result.ok:
        raise OperationError(
            f"could not read wp-config on {context.target.host} via wp-cli in "
            f"{remote_root}: {result.stderr.strip()}"
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < len(_WP_KEYS):
        raise OperationError(
            f"wp config get returned {len(lines)} values, expected "
            f"{len(_WP_KEYS)} ({', '.join(_WP_KEYS)})"
        )
    name, user, password, host_value = lines[: len(_WP_KEYS)]
    host, _, port = host_value.partition(":")
    return RemoteCredentials(
        name=name, user=user, password=password, host=host, port=port or "3306"
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
                f"{_scrub(result.stderr, credentials.password).strip()}"
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


def _scrub(text: str, secret: str) -> str:
    """Remove `secret` from `text`.

    An empty `secret` must be a no-op: `str.replace("", x)` would otherwise
    insert `x` between every character of the message, garbling it exactly
    when the caller most needs to read it.
    """
    return text.replace(secret, "<REDACTED>") if secret else text


LOCAL_DB_USER = "db"
LOCAL_DB_PASSWORD = "db"
LOCAL_DB_PORT = "3306"

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
        strip_definers(dump)
        self._recreate(context, database)
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
    swap in a richer prompt.

    `input()` raises `EOFError` when stdin is closed, which is exactly what a
    non-interactive invocation (a script, a cron job, CI) looks like. Left
    uncaught, `ddev sync --steps=db` from such a context downloads the entire
    production dump and only then dies on a traceback, right at the one
    question that exists to stop a destructive action. Turning it into a
    clean `OperationError` naming `--yes` makes the fix the same one-line
    answer as every other "I can't ask, so tell me how to skip asking" case.
    """
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError as error:
        raise OperationError(
            "cannot confirm a destructive action: no input is available "
            "(stdin is closed). Pass --yes to skip the confirmation prompt."
        ) from error
    return answer in {"y", "yes"}
