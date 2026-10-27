"""The bodies of the `ddev-sync` subcommands.

Kept separate from `main.py` so that the click wiring stays a thin layer: the
functions here take plain values and a `write`/`echo` callback, which is what
lets `tests/cli/test_commands.py` call them without going through Click at all
where that is more convenient.
"""

import contextlib
from collections.abc import Callable
from pathlib import Path

from ddev_sync.cli.context import RunContext
from ddev_sync.cli.prompts import choose_steps, choose_target
from ddev_sync.cli.render import describe_endpoint, render_doctor, render_plan, render_targets
from ddev_sync.cli.reporter import ConsoleReporter
from ddev_sync.cli.verbose import VerboseRunner
from ddev_sync.config.loader import discover_config_paths, load_user_layers
from ddev_sync.config.merge import MergeResult
from ddev_sync.config.targets import collect_targets, list_target_names
from ddev_sync.engine.executor import Executor
from ddev_sync.engine.plan import build_plan
from ddev_sync.engine.steps import steps_for_selection
from ddev_sync.errors import DdevSyncError, TargetError
from ddev_sync.operations.base import ExecutionContext, OperationError, discard_progress
from ddev_sync.operations.preflight import PreflightOperation
from ddev_sync.operations.sanitize import sanitize_switches
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
    # The doctor runs read-only probes, none of which has progress to show.
    # Nor does it narrate them: it prints one table when every probe is in,
    # and a running commentary above that table would be read after the fact,
    # when the waiting it describes is over.
    execution = _execution_context(
        context,
        runner=runner,
        assume_yes=True,
        secrets=set(),
        stdout_is_terminal=False,
        progress=discard_progress,
    )
    report = PreflightOperation().check(execution)
    echo(render_doctor(report, context.target, colour=colour))
    # Every check the rendered checklist can show as FAIL has to fail the
    # command: low disk space is one `PreflightOperation.execute` refuses
    # outright, so leaving it out let a script gated on this exit status
    # start a sync that then aborted in preflight.
    # `credentials_ok`, `sanitize_tables_ok`, `web_ok` and `agent_ok` are the
    # conditions that can be checked (not merely skipped) and failed -
    # `is not False` because `None` means nothing to report (see
    # `PreflightReport`'s own docstring) and must not fail the command.
    return (
        0
        if report.ssh_ok
        and report.remote_path_ok
        and report.path_readable is not False
        and report.free_space_ok
        and report.credentials_ok is not False
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
    colour: bool = False,
    verbose: int = 0,
    stdout_is_terminal: bool = False,
) -> int:
    """Plan and, unless `dry_run`, execute a sync.

    `colour` is decided by the caller, which knows the real terminal and
    environment; this function only passes it on to the reporter. `verbose`
    is the number of `-v` flags: the reporter reads all of it, and from 2 up
    the runner is wrapped so that command output is printed too.
    `stdout_is_terminal` is likewise decided by the caller and handed to the
    operations, which use it to let a long transfer show its progress.
    """
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
        # Before the plan, so the line is read ahead of the "skipped" detail
        # it explains. No secrets exist yet, and none can be in this line.
        # A malformed switch is refused here exactly as a real run refuses it
        # (the OperationError goes to the normal error path); the plan itself
        # reads switches leniently and would preview every rule as active.
        if "sanitize" in selection:
            _warn_sanitize_switches(
                context,
                _execution_context(
                    context,
                    runner=runner,
                    assume_yes=True,
                    secrets=set(),
                    stdout_is_terminal=False,
                    progress=discard_progress,
                ),
                ConsoleReporter(write=echo, colour=colour, verbose=verbose),
            )
        echo(render_plan(plan))
        return 0

    # One set, handed to the reporter, the wrapper and the context: the
    # passwords are registered on the context's side during the run, after
    # the other two exist.
    secrets: set[str] = set()
    reporter = ConsoleReporter(write=echo, colour=colour, verbose=verbose, secrets=secrets)
    if verbose >= 2:
        runner = VerboseRunner(runner, write=echo, secrets=secrets, verbose=verbose)
    execution = _execution_context(
        context,
        runner=runner,
        assume_yes=assume_yes,
        secrets=secrets,
        stdout_is_terminal=stdout_is_terminal,
        progress=_progress_listener(reporter, stdout_is_terminal=stdout_is_terminal),
    )
    report = Executor(reporter=reporter).run(plan, execution)
    if "sanitize" in selection:
        # Already surfaced if malformed: the sanitize step (and preflight,
        # through `active_rules`) read the same switch and failed with this
        # very message in the step list above, and the run exits non-zero.
        with contextlib.suppress(OperationError):
            _warn_sanitize_switches(context, execution, reporter)
    if "sanitize" not in selection:
        reporter.warn_unsanitized(context.target.stage.value)
    return 1 if report.failed else 0


def _warn_sanitize_switches(
    context: RunContext, execution: ExecutionContext, reporter: ConsoleReporter
) -> None:
    """Say, once per run, that a `sanitize.disable_*` switch is on.

    The caller only calls this when the sanitize step is selected: a run that
    never sanitizes has its own "not sanitized" warning, and a switch that
    changes nothing this run is not news. `disable_sanitize_all` wins when
    both are set, since it is the one that took every rule away. A malformed
    switch raises `OperationError` from `sanitize_switches`.
    """
    switches = sanitize_switches(execution)
    if switches.disable_sanitize_all:
        setting = "disable_sanitize_all"
    elif switches.disable_sanitize:
        setting = "disable_sanitize"
    else:
        return
    # A setting with no recorded source (a layer that did not label it) still
    # gets a line; "config" is vague but true.
    source = context.merged.scalar_sources.get(("sanitize", setting), "config")
    reporter.warn_sanitize_disabled(setting, source, all_rules=switches.disable_sanitize_all)


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
    if not chosen:
        # `None` and `[]` are two different answers and must not share a
        # sentence: `None` is "there was no terminal to ask", while `[]` is
        # Enter pressed at the picker with nothing ticked. questionary's
        # checkbox has no validator, so it returns an empty list happily -
        # which used to plan a lone preflight and report a successful sync
        # that did nothing, the same failure `--steps ""` is refused for
        # above. The primary interactive path is held to the same rule.
        raise DdevSyncError(
            f"no step was ticked, so there is nothing to sync. Available: {offered_text}. "
            "Tick at least one, or pass --yes for all of them."
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


def _progress_listener(
    reporter: ConsoleReporter, *, stdout_is_terminal: bool
) -> Callable[[str], None]:
    """Who hears the lines a long step writes while it waits.

    Off a terminal, nobody: those lines exist to show a reader that a wait is
    still a wait, and a log read after the fact has no such reader - only the
    noise. The choice is made here at the edge rather than inside the reporter
    or the operation, which is the rule `stdout_is_terminal` and the colour
    flag already follow.
    """
    return reporter.progress if stdout_is_terminal else discard_progress


def _execution_context(
    context: RunContext,
    *,
    runner: CommandRunner,
    assume_yes: bool,
    secrets: set[str],
    stdout_is_terminal: bool,
    progress: Callable[[str], None],
) -> ExecutionContext:
    return ExecutionContext(
        project=context.project,
        target=context.target,
        merged=context.merged,
        runner=runner,
        downloads_dir=context.project.root / DOWNLOADS,
        assume_yes=assume_yes,
        keep_dump=_keep_dump(context.merged),
        secrets=secrets,
        stdout_is_terminal=stdout_is_terminal,
        progress=progress,
    )


def _keep_dump(merged: MergeResult) -> bool:
    """Read `defaults.keep_dump`; anything but an explicit mapping means unset."""
    defaults = merged.data.get("defaults")
    if not isinstance(defaults, dict):
        return False
    return bool(defaults.get("keep_dump", False))
