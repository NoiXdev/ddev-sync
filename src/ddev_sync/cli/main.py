"""The `ddev-sync` command-line entry point."""

from collections.abc import Sequence
from importlib.metadata import version as package_version
from pathlib import Path

import click

from ddev_sync.cli.context import build_context
from ddev_sync.cli.render import render_plan
from ddev_sync.ddev.project import DEFAULT_PROJECT_ROOT
from ddev_sync.engine.plan import build_plan
from ddev_sync.engine.steps import SELECTION_GROUPS, steps_for_selection
from ddev_sync.errors import DdevSyncError
from ddev_sync.profiles.registry import DEFAULT_PROFILE_ROOT


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--target", "target_name", default=None, help="Which sync target to use.")
@click.option("--type", "type_flag", default=None, help="Override the detected project type.")
@click.option(
    "--version",
    "version_flag",
    default=None,
    help="Override the detected framework major version.",
)
@click.option(
    "--steps",
    default=None,
    help=f"Comma-separated: {', '.join(sorted(SELECTION_GROUPS))}.",
)
@click.option("--dry-run", is_flag=True, help="Print the plan and execute nothing.")
@click.option("--project-root", type=click.Path(path_type=Path), default=DEFAULT_PROJECT_ROOT)
@click.option("--home", type=click.Path(path_type=Path), default=None)
@click.option("--profile-root", type=click.Path(path_type=Path), default=DEFAULT_PROFILE_ROOT)
@click.version_option(package_version("ddev-sync"), "--version-info", prog_name="ddev-sync")
def cli(
    target_name: str | None,
    type_flag: str | None,
    version_flag: str | None,
    steps: str | None,
    dry_run: bool,
    project_root: Path,
    home: Path | None,
    profile_root: Path,
) -> None:
    """Pull remote database, files and setup into this DDEV project."""
    context = build_context(
        project_root=project_root,
        home=home if home is not None else Path.home(),
        target_name=target_name,
        type_flag=type_flag,
        version_flag=version_flag,
        profile_root=profile_root,
    )
    selection = (
        sorted(SELECTION_GROUPS)
        if steps is None
        else [part.strip() for part in steps.split(",") if part.strip()]
    )
    plan = build_plan(
        target=context.target,
        project=context.project,
        profile=context.profile,
        merged=context.merged,
        steps=steps_for_selection(selection),
    )
    if not dry_run:
        raise DdevSyncError(
            "execution is not implemented in this build; use --dry-run to inspect the plan"
        )
    click.echo(render_plan(plan), nl=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    try:
        cli.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except DdevSyncError as error:
        click.echo(str(error), err=True)
        return 1
    except ValueError as error:
        click.echo(str(error), err=True)
        return 1
    except click.ClickException as error:
        error.show()
        return error.exit_code
    return 0
