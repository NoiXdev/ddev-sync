"""The `ddev-sync` command-line entry point.

`cli` is a `click.Group` rather than a single command: `ddev-sync` alone runs
an interactive sync, while `ddev-sync targets` and `ddev-sync doctor` are
subcommands. Click only parses a group's own options up to the first
non-option token on the command line; everything after a subcommand name is
handed to that subcommand's own parser instead.

Every option is therefore declared on the group *only*, resolved once into
`ctx.obj` (an `_Options`), and read back out by whichever subcommand needs it
via `click.pass_obj`. Declaring an option a second time on a subcommand -
earlier versions of this module did that, first for all nine options, then
for six of them after round 1 only fixed the three path options - looks
harmless but is not: whichever copy the parser actually consults depends on
where the user put the flag relative to the subcommand name, so
`ddev-sync --target=x doctor` and `ddev-sync doctor --target=x` would
silently resolve `--target` two different ways, one of them wrong, with no
error. One source per value removes the ambiguity instead of documenting
around it - and it has to be *every* option, not just the ones a bug report
happened to name, or the CLI's own options disagree with each other about
where they belong. `--steps` has no meaning past the point the interactive
run itself is bypassed, so it is read directly out of the group callback's
own parameters instead of being threaded into `_Options` - nothing past the
group needs it. `--dry-run` and `--yes` used to be the same way, but `ddev
sync build` (an alias for `--steps=setup --features=node-build`, implemented
by `build_cmd` below) is a second place that needs to run the interactive
sync, so both now travel through `_Options` too, exactly like every other
value a subcommand might consult.

`build` is itself a worked example of the same failure the paragraph above
describes, one level up: combining it with `--steps`, `--features` or
`--no-features` does not get a second, conflicting copy of the value - it
gets silently discarded, which is no better. `_reject_build_conflicts`
refuses the combination outright instead, the same way `--features` and
`--no-features` together are refused - nothing a user typed is ever quietly
thrown away.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
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
from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT
from ddev_sync.profiles.registry import DEFAULT_PROFILE_ROOT

# `build` means exactly `--steps=setup --features=node-build` - nothing more.
# These two constants are the single place that equivalence is spelled out;
# `build_cmd` and `_reject_build_conflicts` below read them instead of
# writing the strings out a second time, so the alias cannot quietly drift
# from the thing it is shorthand for.
_BUILD_STEPS = "setup"
_BUILD_FEATURES = "node-build"
_BUILD_IMPLIES = f"'build' already implies --steps={_BUILD_STEPS} --features={_BUILD_FEATURES}"


@dataclass(frozen=True)
class _Options:
    """Everything a subcommand might need, resolved once by the group callback."""

    project_root: Path
    home: Path
    profile_root: Path
    feature_root: Path
    target_name: str | None
    type_flag: str | None
    framework_version_flag: str | None
    features_flag: str | None
    no_features: bool
    dry_run: bool
    assume_yes: bool


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--profile-root", type=click.Path(path_type=Path), default=DEFAULT_PROFILE_ROOT)
@click.option("--feature-root", type=click.Path(path_type=Path), default=DEFAULT_FEATURE_ROOT)
@click.option("--home", type=click.Path(path_type=Path), default=None)
@click.option("--project-root", type=click.Path(path_type=Path), default=DEFAULT_PROJECT_ROOT)
@click.option(
    "--yes", "assume_yes", is_flag=True, help="Assume every default; skip interactive prompts."
)
@click.option("--dry-run", is_flag=True, help="Print the plan and execute nothing.")
@click.option(
    "--steps", default=None, help=f"Comma-separated: {', '.join(sorted(SELECTION_GROUPS))}."
)
@click.option(
    "--framework-version",
    "framework_version_flag",
    default=None,
    help="Override the detected framework major version.",
)
@click.option("--type", "type_flag", default=None, help="Override the detected project type.")
@click.option("--target", "target_name", default=None, help="Which sync target to use.")
@click.option(
    "--features",
    "features_flag",
    default=None,
    help="Comma-separated feature names; replaces detection for this run.",
)
@click.option(
    "--no-features",
    is_flag=True,
    help="Disable feature detection for this run.",
)
@click.version_option(package_version("ddev-sync"), "--version", prog_name="ddev-sync")
@click.pass_context
def cli(
    ctx: click.Context,
    target_name: str | None,
    type_flag: str | None,
    framework_version_flag: str | None,
    features_flag: str | None,
    no_features: bool,
    steps: str | None,
    dry_run: bool,
    assume_yes: bool,
    project_root: Path,
    home: Path | None,
    profile_root: Path,
    feature_root: Path,
) -> int | None:
    """Pull remote database, files and setup into this DDEV project."""
    if features_flag is not None and no_features:
        raise click.UsageError("--features and --no-features cannot be used together")
    if features_flag is not None and _is_blank_features_flag(features_flag):
        raise click.UsageError(
            "--features named no features; use --no-features to disable detection"
        )
    _reject_build_conflicts(ctx.invoked_subcommand, steps, features_flag, no_features)
    ctx.obj = _Options(
        project_root=project_root,
        home=home if home is not None else Path.home(),
        profile_root=profile_root,
        feature_root=feature_root,
        target_name=target_name,
        type_flag=type_flag,
        framework_version_flag=framework_version_flag,
        features_flag=features_flag,
        no_features=no_features,
        dry_run=dry_run,
        assume_yes=assume_yes,
    )
    if ctx.invoked_subcommand is not None:
        return None
    return _run(ctx.obj, steps=steps, dry_run=dry_run, assume_yes=assume_yes)


def _is_blank_features_flag(features_flag: str) -> bool:
    """True when every comma-separated entry is blank after stripping.

    `--features=` is almost never a deliberate "zero features" - it is what
    `--features=$FEATURES` becomes when a script's environment variable is
    unset, and silently treating that the same as `--no-features` would hide
    exactly the kind of mistake that costs someone an afternoon. The two
    spellings also render differently (the "detection disabled" notice only
    ever comes from `--no-features`/the config kill switch), so letting them
    both mean "no features" would be two spellings, one effect, different
    output - its own small trap. `--no-features` is the one CLI spelling for
    "none"; an empty `--features` is refused instead of silently guessing
    that is what was meant.

    YAML's `features: []` is deliberately NOT tightened to match: a human
    writing `[]` into a config file is unambiguous in a way a shell
    expansion never is, so `parse_feature_key`'s existing "absent means
    detect, empty means none" contract is untouched. Only this CLI flag -
    where "empty" usually means "something upstream went wrong" - gets the
    stricter reading.
    """
    return not any(name.strip() for name in features_flag.split(","))


def _reject_build_conflicts(
    invoked_subcommand: str | None,
    steps: str | None,
    features_flag: str | None,
    no_features: bool,
) -> None:
    """Refuse `build` combined with any option it already fixes.

    `build` means exactly `--steps=setup --features=node-build` (see
    `_BUILD_STEPS`/`_BUILD_FEATURES`); overriding one silently - which is
    what happened before this check existed - is the same failure shape the
    module docstring opens with: an option the user typed, accepted without
    complaint, and then ignored. Refusing the combination, the same way
    `--features` and `--no-features` together are refused, means nothing a
    user typed on the command line is ever quietly thrown away.
    """
    if invoked_subcommand != "build":
        return
    if steps is not None:
        raise click.UsageError(f"--steps cannot be combined with 'build'; {_BUILD_IMPLIES}")
    if features_flag is not None:
        raise click.UsageError(f"--features cannot be combined with 'build'; {_BUILD_IMPLIES}")
    if no_features:
        raise click.UsageError(f"--no-features cannot be combined with 'build'; {_BUILD_IMPLIES}")


@cli.command("targets")
@click.pass_obj
def targets_cmd(options: _Options) -> int:
    """List every defined sync target."""
    return targets_command(options.project_root, options.home, click.echo)


@cli.command("doctor")
@click.pass_obj
def doctor_cmd(options: _Options) -> int:
    """Run the preflight checks against a target and print a checklist."""
    context = _build_run_context(options)
    return doctor_command(context, click.echo)


@cli.command("build")
@click.pass_obj
def build_cmd(options: _Options) -> int:
    """Alias for `--steps=setup --features=node-build`.

    By the time this callback runs, `cli()` has already rejected `build`
    combined with `--steps`, `--features` or `--no-features`
    (`_reject_build_conflicts`), so `options.features_flag` is always `None`
    and `options.no_features` is always `False` here - there is nothing left
    to override, only to fill in. `replace` still states both values
    explicitly rather than relying on that silently, so the alias stays
    self-describing even if that guarantee ever moved. Routing through
    `_run` - the same function the bare `ddev-sync` invocation calls - is
    what keeps this an alias rather than a second implementation that could
    drift from the long form.
    """
    forced = replace(options, features_flag=_BUILD_FEATURES, no_features=False)
    return _run(forced, steps=_BUILD_STEPS, dry_run=options.dry_run, assume_yes=options.assume_yes)


def _run(options: _Options, *, steps: str | None, dry_run: bool, assume_yes: bool) -> int:
    """Plan (and maybe execute) a sync; the one code path both `cli` and `build_cmd` call."""
    context = _build_run_context(options)
    selection = resolve_selection(steps, assume_yes)
    return run_command(context, selection, dry_run=dry_run, assume_yes=assume_yes, echo=click.echo)


def _build_run_context(options: _Options) -> RunContext:
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
            project_root=options.project_root,
            home=options.home,
            target_name=name,
            type_flag=options.type_flag,
            framework_version_flag=options.framework_version_flag,
            profile_root=options.profile_root,
            feature_root=options.feature_root,
            features_flag=options.features_flag,
            no_features=options.no_features,
        )

    if options.target_name is not None:
        return build(resolve_target_name(options.target_name, options.project_root, options.home))
    try:
        return build(None)
    except TargetError:
        return build(resolve_target_name(None, options.project_root, options.home))


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
