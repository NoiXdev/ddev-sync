"""Dump the remote database and stream it straight into a local file.

Credentials are read from the remote at run time and never written anywhere.
`RemoteCredentials` redacts its password in `repr` so that an exception
traceback or a debug log cannot leak a production secret.
"""

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
    shared = context.remote("shared")
    tests = " ".join(
        f'if [ -f "{shared}/{name}" ]; then echo "{shared}/{name}"; '
        f'cat "{shared}/{name}"; exit 0; fi'
        for name in candidates
    )
    script = f"{tests} exit 9"
    result = context.runner.run(ssh_command(context.target, ["sh", "-c", script]))
    if not result.ok:
        looked = ", ".join(f"{shared}/{name}" for name in candidates)
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


def _from_wp_cli(context: ExecutionContext) -> RemoteCredentials:
    """Read credentials via `wp config get` on the remote.

    Implemented in the task that lands the WordPress profile; until then this
    source is rejected explicitly rather than silently misbehaving.
    """
    raise OperationError("the wp_cli credential source is not available in this build")


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
