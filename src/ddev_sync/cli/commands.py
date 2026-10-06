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
from ddev_sync.cli.render import describe_endpoint, render_doctor, render_plan, render_targets
from ddev_sync.cli.reporter import ConsoleReporter
from ddev_sync.config.loader import discover_config_paths, load_user_layers
from ddev_sync.config.merge import MergeResult
from ddev_sync.config.targets import collect_targets, list_target_names
from ddev_sync.engine.executor import Executor
from ddev_sync.engine.plan import build_plan
from ddev_sync.engine.steps import steps_for_selection
from ddev_sync.errors import DdevSyncError, TargetError
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.preflight import PreflightOperation
from ddev_sync.transport.runner import CommandRunner

DOWNLOADS = Path(".ddev") / ".downloads"


def describe_targets(project_root: Path, home: Path) -> dict[str, str]:
    """Map target name to a one-line endpoint description, for the picker."""
    layers = load_user_layers(discover_config_paths(project_root, home))
    collected = collect_targets(layers)
    return {name: describe_endpoint(collected[name].spec) for name in list_target_names(collected)}


def targets_command(project_root: Path, home: Path, echo: Callable[[str], None]) -> int:
    """Print every defined target and which layer defined it."""
    layers = load_user_layers(discover_config_paths(project_root, home))
    echo(render_targets(collect_targets(layers)))
    return 0


def doctor_command(
    context: RunContext,
    echo: Callable[[str], None],
    *,
    runner: CommandRunner,
    colour: bool = False,
) -> int:
    """Run the preflight checks and print them as a checklist.

    `colour` is decided by the caller, which knows the real terminal and
    environment; this function only passes it on to the renderer.
    """
    execution = _execution_context(context, runner=runner, assume_yes=True)
    report = PreflightOperation().check(execution)
    echo(render_doctor(report, context.target, colour=colour))
    # Every check the rendered checklist can show as FAIL has to fail the
    # command: low disk space is one `PreflightOperation.execute` refuses
    # outright, so leaving it out let a script gated on this exit status
    # start a sync that then aborted in preflight.
    # `sanitize_tables_ok`, `web_ok` and `agent_ok` are the conditions that
    # can be checked (not merely skipped) and failed - `is not False` because
    # `None` means nothing to report (see `PreflightReport`'s own docstring)
    # and must not fail the command.
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
    runner: CommandRunner,
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
    execution = _execution_context(context, runner=runner, assume_yes=assume_yes)
    report = Executor(reporter=reporter).run(plan, execution)
    if "sanitize" not in selection:
        reporter.warn_unsanitized(context.target.stage.value)
    return 1 if report.failed else 0


def resolve_selection(
    steps: str | None, assume_yes: bool, *, available: frozenset[str]
) -> list[str]:
    """Turn `--steps` into a selection, asking when it is absent.

    `available` is what the project would actually produce work for (see
    `available_selection_groups`). Offering a step the project has nothing
    for is noise, and accepting one explicitly would report a successful
    sync that did nothing, so the picker, `--yes` and `--steps` all stay
    within it.

    `setup` is deliberately not part of the default selection: it runs
    `composer install` and framework commands that can take minutes, and
    someone who just wants fresh data should not wait for a frontend build
    they did not ask for.
    """
    offered = sorted(available)
    # Spelled out so an empty list reads "Available: none." and not "Available: .".
    offered_text = ", ".join(offered) or "none"
    if steps is not None:
        names = [part.strip() for part in steps.split(",") if part.strip()]
        if not names:
            # `--steps ""` used to select nothing and plan a lone preflight,
            # which reports success having done nothing - the same failure as
            # naming a step with no work, so it is refused the same way.
            raise DdevSyncError(f"--steps names no step. Available: {offered_text}.")
        # Unknown names are checked first, and by the engine's own function,
        # so a typo keeps saying "unknown step" and is never mistaken for a
        # real step this project merely has no work for.
        steps_for_selection(names)
        unavailable = [name for name in names if name not in available]
        if unavailable:
            raise DdevSyncError(
                f"not available for this project: {', '.join(unavailable)} - it has nothing "
                f"to do for them. Available: {offered_text}."
            )
        return names
    if assume_yes:
        return offered
    chosen = choose_steps(offered)
    if chosen is None:
        raise DdevSyncError(
            "no steps selected. Without a terminal there is nothing to ask, so pass "
            f"--steps ({','.join(offered)}) or --yes for all of them."
        )
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
        raise DdevSyncError(
            "no target selected. Without a terminal there is nothing to ask, so pass "
            "--target; `ddev-sync targets` lists the ones this project knows."
        )
    return chosen


def _execution_context(
    context: RunContext, *, runner: CommandRunner, assume_yes: bool
) -> ExecutionContext:
    return ExecutionContext(
        project=context.project,
        target=context.target,
        merged=context.merged,
        runner=runner,
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
