"""The bodies of the `ddev-sync` subcommands.

Kept separate from `main.py` so that the click wiring stays a thin layer: the
functions here take plain values and a `write`/`echo` callback, which is what
lets `tests/cli/test_commands.py` call them without going through Click at all
where that is more convenient.
"""

from collections.abc import Callable
from pathlib import Path

from ddev_sync.cli.context import RunContext
from ddev_sync.cli.prompts import choose_steps, choose_target
from ddev_sync.cli.render import render_doctor, render_plan, render_targets
from ddev_sync.cli.reporter import ConsoleReporter
from ddev_sync.config.loader import discover_config_paths, load_user_layers
from ddev_sync.config.merge import MergeResult
from ddev_sync.config.targets import collect_targets, list_target_names
from ddev_sync.engine.executor import Executor
from ddev_sync.engine.plan import build_plan
from ddev_sync.engine.steps import SELECTION_GROUPS, steps_for_selection
from ddev_sync.errors import DdevSyncError, TargetError
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.preflight import PreflightOperation
from ddev_sync.transport.runner import SubprocessRunner

DOWNLOADS = Path(".ddev") / ".downloads"


def describe_targets(project_root: Path, home: Path) -> dict[str, str]:
    """Map target name to a one-line endpoint description, for the picker."""
    layers = load_user_layers(discover_config_paths(project_root, home))
    collected = collect_targets(layers)
    described: dict[str, str] = {}
    for name in list_target_names(collected):
        spec = collected[name].spec
        host = spec.ssh_alias or spec.host or "?"
        user = "" if spec.ssh_alias or not spec.user else f"{spec.user}@"
        described[name] = f"{user}{host}:{spec.path or '?'}"
    return described


def targets_command(project_root: Path, home: Path, echo: Callable[[str], None]) -> int:
    """Print every defined target and which layer defined it."""
    layers = load_user_layers(discover_config_paths(project_root, home))
    echo(render_targets(collect_targets(layers)))
    return 0


def doctor_command(context: RunContext, echo: Callable[[str], None]) -> int:
    """Run the preflight checks and print them as a checklist."""
    execution = _execution_context(context, assume_yes=True)
    report = PreflightOperation().check(execution)
    echo(render_doctor(report, context.target))
    # Every check the rendered checklist can show as FAIL has to fail the
    # command: low disk space is one `PreflightOperation.execute` refuses
    # outright, so leaving it out let a script gated on this exit status
    # start a sync that then aborted in preflight.
    # `sanitize_tables_ok is False` is the one condition that is checked (not
    # merely skipped) and failed - `None` means nothing to report (see
    # `PreflightReport`'s own docstring) and must not fail the command.
    return (
        0
        if report.ssh_ok
        and report.remote_path_ok
        and report.free_space_ok
        and report.sanitize_tables_ok is not False
        and report.web_ok is not False
        and report.agent_ok is not False
        else 1
    )


def run_command(
    context: RunContext,
    selection: list[str],
    *,
    dry_run: bool,
    assume_yes: bool,
    echo: Callable[[str], None],
    only_feature_setup: bool = False,
) -> int:
    """Plan and, unless `dry_run`, execute a sync."""
    plan = build_plan(
        target=context.target,
        project=context.project,
        profile=context.profile,
        merged=context.merged,
        steps=steps_for_selection(selection),
        features=context.features,
        only_feature_setup=only_feature_setup,
    )
    if dry_run:
        echo(render_plan(plan))
        return 0

    reporter = ConsoleReporter(write=echo)
    execution = _execution_context(context, assume_yes=assume_yes)
    report = Executor(reporter=reporter).run(plan, execution)
    if "sanitize" not in selection:
        reporter.warn_unsanitized(context.target.stage.value)
    return 1 if report.failed else 0


def resolve_selection(steps: str | None, assume_yes: bool) -> list[str]:
    """Turn `--steps` into a selection, asking when it is absent.

    `setup` is deliberately not part of the default selection: it runs
    `composer install` and framework commands that can take minutes, and
    someone who just wants fresh data should not wait for a frontend build
    they did not ask for.
    """
    if steps is not None:
        return [part.strip() for part in steps.split(",") if part.strip()]
    if assume_yes:
        return sorted(SELECTION_GROUPS)
    chosen = choose_steps(sorted(SELECTION_GROUPS))
    if chosen is None:
        raise DdevSyncError("no steps selected")
    return chosen


def resolve_target_name(explicit: str | None, project_root: Path, home: Path) -> str:
    """Return the target name, asking interactively when it is absent."""
    if explicit is not None:
        return explicit
    described = describe_targets(project_root, home)
    names = sorted(described)
    if not names:
        raise TargetError("no sync targets are defined; run 'ddev sync init' to create one")
    chosen = choose_target(names, described)
    if chosen is None:
        raise DdevSyncError("no target selected")
    return chosen


def _execution_context(context: RunContext, *, assume_yes: bool) -> ExecutionContext:
    return ExecutionContext(
        project=context.project,
        target=context.target,
        merged=context.merged,
        runner=SubprocessRunner(),
        downloads_dir=context.project.root / DOWNLOADS,
        assume_yes=assume_yes,
        keep_dump=_keep_dump(context.merged),
    )


def _keep_dump(merged: MergeResult) -> bool:
    """Read `defaults.keep_dump`; anything but an explicit mapping means unset."""
    defaults = merged.data.get("defaults")
    if not isinstance(defaults, dict):
        return False
    return bool(defaults.get("keep_dump", False))
