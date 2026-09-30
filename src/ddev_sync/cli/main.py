"""The `ddev-sync` command-line entry point.

`cli` is a `click.Group` rather than a single command: `ddev-sync` alone runs
an interactive sync, while `ddev-sync targets` and `ddev-sync doctor` are
subcommands. Click only parses a group's own options up to the first
non-option token on the command line; everything after a subcommand name is
handed to that subcommand's own parser instead.

`--project-root`, `--home` and `--profile-root` are declared on the group
*only*, and resolved once into `ctx.obj` (a `_Paths`), which every subcommand
reads via `click.pass_obj`. Declaring them a second time on each subcommand -
an earlier version of this module did - looks harmless but is not: whichever
copy the parser actually consults depends on where the user put the flags
relative to the subcommand name, so `ddev-sync --project-root X targets` and
`ddev-sync targets --project-root X` would silently resolve `X` two different
ways (one of them wrong, with no error). One source per value removes the
ambiguity instead of documenting around it. `--target`, `--type`,
`--framework-version`, `--steps`, `--yes` and `--dry-run` are run-specific -
only the default run path and `doctor` use them - and stay declared on each
of those via `_run_options`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path

import click

from ddev_sync.cli.commands import (
    doctor_command,
    resolve_selection,
    resolve_target_name,
    run_command,
    targets_command,
)
from ddev_sync.cli.context import RunContext, build_context
from ddev_sync.ddev.project import DEFAULT_PROJECT_ROOT
from ddev_sync.engine.steps import SELECTION_GROUPS
from ddev_sync.errors import DdevSyncError, TargetError
from ddev_sync.profiles.registry import DEFAULT_PROFILE_ROOT


@dataclass(frozen=True)
class _Paths:
    """The three filesystem roots, resolved once by the group callback."""

    project_root: Path
    home: Path
    profile_root: Path


def _run_options[F: Callable[..., int | None]](command: F) -> F:
    """Apply the options shared by the default run path and `doctor`."""
    command = click.option(
        "--yes", "assume_yes", is_flag=True, help="Assume every default; skip interactive prompts."
    )(command)
    command = click.option("--dry-run", is_flag=True, help="Print the plan and execute nothing.")(
        command
    )
    command = click.option(
        "--steps", default=None, help=f"Comma-separated: {', '.join(sorted(SELECTION_GROUPS))}."
    )(command)
    command = click.option(
        "--framework-version",
        "framework_version_flag",
        default=None,
        help="Override the detected framework major version.",
    )(command)
    command = click.option(
        "--type", "type_flag", default=None, help="Override the detected project type."
    )(command)
    command = click.option(
        "--target", "target_name", default=None, help="Which sync target to use."
    )(command)
    return command


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--profile-root", type=click.Path(path_type=Path), default=DEFAULT_PROFILE_ROOT)
@click.option("--home", type=click.Path(path_type=Path), default=None)
@click.option("--project-root", type=click.Path(path_type=Path), default=DEFAULT_PROJECT_ROOT)
@_run_options
@click.version_option(package_version("ddev-sync"), "--version", prog_name="ddev-sync")
@click.pass_context
def cli(
    ctx: click.Context,
    target_name: str | None,
    type_flag: str | None,
    framework_version_flag: str | None,
    steps: str | None,
    dry_run: bool,
    assume_yes: bool,
    project_root: Path,
    home: Path | None,
    profile_root: Path,
) -> int | None:
    """Pull remote database, files and setup into this DDEV project."""
    ctx.obj = _Paths(
        project_root=project_root,
        home=home if home is not None else Path.home(),
        profile_root=profile_root,
    )
    if ctx.invoked_subcommand is not None:
        return None
    context = _build_run_context(
        target_name,
        type_flag,
        framework_version_flag,
        ctx.obj.project_root,
        ctx.obj.home,
        ctx.obj.profile_root,
    )
    selection = resolve_selection(steps, assume_yes)
    return run_command(context, selection, dry_run=dry_run, assume_yes=assume_yes, echo=click.echo)


@cli.command("targets")
@click.pass_obj
def targets_cmd(paths: _Paths) -> int:
    """List every defined sync target."""
    return targets_command(paths.project_root, paths.home, click.echo)


@cli.command("doctor")
@_run_options
@click.pass_obj
def doctor_cmd(
    paths: _Paths,
    target_name: str | None,
    type_flag: str | None,
    framework_version_flag: str | None,
    steps: str | None,
    dry_run: bool,
    assume_yes: bool,
) -> int:
    """Run the preflight checks against a target and print a checklist."""
    context = _build_run_context(
        target_name,
        type_flag,
        framework_version_flag,
        paths.project_root,
        paths.home,
        paths.profile_root,
    )
    return doctor_command(context, click.echo)


def _build_run_context(
    target_name: str | None,
    type_flag: str | None,
    framework_version_flag: str | None,
    project_root: Path,
    home: Path,
    profile_root: Path,
) -> RunContext:
    """Assemble the run context, falling back to an interactive target pick.

    `build_context` resolves the target itself and raises `TargetError`
    immediately - never prompting - when `target_name` is `None` and the
    project defines zero or several targets. Retrying only on that specific
    error, instead of resolving the target interactively up front, keeps
    `read_ddev_project`'s "this does not look like a DDEV project" diagnostic
    as the first thing a broken project sees, rather than masking it behind a
    target question the project can never answer correctly anyway.
    """

    def build(name: str | None) -> RunContext:
        return build_context(
            project_root=project_root,
            home=home,
            target_name=name,
            type_flag=type_flag,
            framework_version_flag=framework_version_flag,
            profile_root=profile_root,
        )

    if target_name is not None:
        return build(resolve_target_name(target_name, project_root, home))
    try:
        return build(None)
    except TargetError:
        return build(resolve_target_name(None, project_root, home))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code.

    `cli.main(..., standalone_mode=False)` already turns every `Exit` raised
    inside a callback (e.g. `--version`, or `ctx.exit(...)`) into its exit
    code and returns it directly - it never re-raises `Exit` to this caller,
    which is why there is no `except click.exceptions.Exit` here. The return
    value is propagated rather than discarded: every command callback now
    returns a real exit code (0 success, 1 failure), not `None`.
    """
    try:
        result: int = cli.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except DdevSyncError as error:
        click.echo(str(error), err=True)
        return 1
    except ValueError as error:
        click.echo(str(error), err=True)
        return 1
    except click.ClickException as error:
        error.show()
        return error.exit_code
    return result
