"""Validate a merged profile before anything acts on it.

Catching a malformed profile at load time rather than at step four of a sync
is the difference between "typo3@12.yaml line 14" and "KeyError: 'local' while
transferring fileadmin".
"""

from typing import Any

from ddev_sync.errors import ProfileError
from ddev_sync.operations.database import CREDENTIAL_SOURCES
from ddev_sync.operations.env import STRATEGIES
from ddev_sync.operations.sanitize import SANITIZE_OPERATIONS

TOP_LEVEL = frozenset({"name", "detect", "env", "database", "files", "sanitize", "setup", "hooks"})


def validate_profile(data: dict[str, Any]) -> None:
    """Raise `ProfileError` unless `data` is a well-formed profile."""
    unknown = sorted(set(data) - TOP_LEVEL)
    if unknown:
        raise ProfileError(
            f"unknown profile key(s): {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(TOP_LEVEL))}"
        )
    if not data.get("name"):
        raise ProfileError("a profile needs a 'name'")

    _validate_env(data.get("env"))
    _validate_database(data.get("database"))
    _validate_files(data.get("files"))
    _validate_sanitize(data.get("sanitize"))
    _validate_commands(data.get("setup"), "setup.commands")


def _validate_env(env: Any) -> None:
    if env is None:
        return
    if not isinstance(env, dict):
        raise ProfileError("'env' must be a mapping")
    strategy = env.get("strategy", "patch_keys")
    if strategy not in STRATEGIES:
        raise ProfileError(f"env.strategy {strategy!r} is not one of {', '.join(STRATEGIES)}")


def _validate_database(database: Any) -> None:
    if database is None:
        return
    if not isinstance(database, dict):
        raise ProfileError("'database' must be a mapping")
    source = database.get("remote_credentials", "env")
    if source not in CREDENTIAL_SOURCES:
        raise ProfileError(
            f"database.remote_credentials {source!r} is not one of {', '.join(CREDENTIAL_SOURCES)}"
        )


def _validate_files(files: Any) -> None:
    if files is None:
        return
    if not isinstance(files, dict):
        raise ProfileError("'files' must be a mapping")
    paths = files.get("paths", [])
    if not isinstance(paths, list):
        raise ProfileError("files.paths must be a list")
    for position, entry in enumerate(paths):
        if not isinstance(entry, dict):
            raise ProfileError(f"files.paths[{position}] must be a mapping")
        for side in ("remote", "local"):
            if not entry.get(side):
                raise ProfileError(f"files.paths[{position}] is missing {side!r}")


def _validate_sanitize(sanitize: Any) -> None:
    if sanitize is None:
        return
    if not isinstance(sanitize, dict):
        raise ProfileError("'sanitize' must be a mapping")
    rules = sanitize.get("rules", [])
    if not isinstance(rules, list):
        raise ProfileError("sanitize.rules must be a list")
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ProfileError(f"sanitize.rules[{position}] must be a mapping")
        if not rule.get("name"):
            raise ProfileError(f"sanitize.rules[{position}] is missing 'name'")
        operation = rule.get("operation")
        if operation is not None and operation not in SANITIZE_OPERATIONS:
            raise ProfileError(
                f"sanitize rule {rule['name']!r} uses unknown operation "
                f"{operation!r}; available: {', '.join(sorted(SANITIZE_OPERATIONS))}"
            )
        if operation is None and not rule.get("sql"):
            raise ProfileError(f"sanitize rule {rule['name']!r} has neither 'sql' nor 'operation'")


def _validate_commands(block: Any, label: str) -> None:
    if block is None:
        return
    if not isinstance(block, dict):
        raise ProfileError(f"'{label.split('.')[0]}' must be a mapping")
    commands = block.get("commands", [])
    if not isinstance(commands, list):
        raise ProfileError(f"{label} must be a list")
    for position, entry in enumerate(commands):
        if not isinstance(entry, dict):
            raise ProfileError(f"{label}[{position}] must be a mapping")
        if entry.get("run") is None and entry.get("operation") is None:
            raise ProfileError(f"{label}[{position}] needs either 'run' or 'operation'")
