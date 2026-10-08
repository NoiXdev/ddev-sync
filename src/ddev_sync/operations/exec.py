"""Run setup commands and hook commands.

Setup commands and hooks share one implementation on purpose: a developer who
has learned how `allow_failure` behaves in a hook should not have to relearn it
for a setup command.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.placeholders import expand_value
from ddev_sync.operations.sanitize import SANITIZE_OPERATIONS, SanitizeRule
from ddev_sync.transport.container import web_command

_PREFIX = "ddev-sync:"


def resolve_operation_reference(reference: str) -> str:
    """Normalise `ddev-sync:flush-cache` into the catalog key `flush_cache`."""
    name = reference.removeprefix(_PREFIX)
    return name.replace("-", "_")


def run_command_entry(context: ExecutionContext, entry: Mapping[str, Any]) -> None:
    """Run one `{run: ...}` or `{operation: ...}` entry.

    `sanitize.py`'s rules expand `${...}` placeholders at parse time; this is
    the equivalent for `setup.commands` and every hook, both of which share
    this one function (`ExecOperation` and `Executor._dispatch`'s hook path).
    Without this, a command written with `${DDEV_SITENAME}` ran with the
    literal, unexpanded text instead - the same class of gap `sanitize.py`'s
    own `sql:` fix (see its `_parse_rule` docstring) already closed once for
    raw SQL.
    """
    entry = expand_value(dict(entry), context)
    reference = entry.get("operation")
    if reference is not None:
        _run_operation(context, str(reference), entry)
        return

    command = entry.get("run")
    if command is None:
        raise OperationError(f"a command entry needs either 'run' or 'operation': {dict(entry)}")

    workdir = _resolve_workdir(context, entry)
    # `docker exec -w`, not `cwd=`: the command runs in the project's web
    # container (see transport/container.py for why), and `-w` carries the
    # directory without a second layer of shell quoting. `_resolve_workdir`
    # still runs first, so a workdir escaping the project root is rejected
    # before anything is dispatched.
    result = context.runner.run(
        web_command(context.project, ["sh", "-c", str(command)], workdir=workdir)
    )
    if result.ok or bool(entry.get("allow_failure", False)):
        return
    raise OperationError(
        f"command failed (exit {result.returncode}): {command}\n{result.failure_detail()}"
    )


def _resolve_workdir(context: ExecutionContext, entry: Mapping[str, Any]) -> Path:
    """Resolve the entry's `workdir`, defaulting to the project root.

    Reads `workdir` exactly once and treats an explicit empty string as the
    error it is, rather than the `entry.get("workdir", ".") if entry.get(...)
    else ...` phrasing under which `workdir: ""` is falsy and quietly falls
    back to the project root instead of being rejected.
    """
    if "workdir" not in entry:
        return context.project.root
    workdir = str(entry["workdir"])
    if not workdir:
        raise OperationError("a command entry's 'workdir' must not be empty")
    return context.local(workdir)


def _run_operation(context: ExecutionContext, reference: str, entry: Mapping[str, Any]) -> None:
    key = resolve_operation_reference(reference)
    handler = SANITIZE_OPERATIONS.get(key)
    if handler is None:
        known = ", ".join(sorted(SANITIZE_OPERATIONS)) or "none"
        raise OperationError(f"unknown operation {reference!r}; available operations: {known}")
    handler.run(
        context,
        SanitizeRule(
            name=reference,
            operation=key,
            params={k: v for k, v in entry.items() if k != "operation"},
        ),
    )


class ExecOperation:
    """Runs one command entry from a profile's `setup.commands` or a hook."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Run the entry described by `params`."""
        run_command_entry(context, params)
