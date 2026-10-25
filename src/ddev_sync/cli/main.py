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
sync build` (an alias for `--steps=setup --features=node-build
--only-feature-setup`, implemented by `build_cmd` below) is a second place
that needs to run the interactive sync, so both now travel through
`_Options` too, exactly like every other value a subcommand might consult.

`build` is itself a worked example of the same failure the paragraph above
describes, one level up: combining it with `--steps`, `--features`,
`--no-features` or `--only-feature-setup` does not get a second, conflicting
copy of the value - it gets silently discarded, which is no better.
`_reject_build_conflicts` refuses the combination outright instead, the same
way `--features` and `--no-features` together are refused - nothing a user
typed is ever quietly thrown away.
"""

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import partial
from importlib.metadata import version as package_version
from pathlib import Path

import click

from ddev_sync.cli.colour import colour_enabled
from ddev_sync.cli.commands import (
    doctor_command,
    resolve_selection,
    resolve_target_name,
    run_command,
    targets_command,
)
from ddev_sync.cli.context import RunContext, build_context
from ddev_sync.cli.init import init_command
from ddev_sync.cli.waiting import Waiting, waiting_enabled
from ddev_sync.ddev.project import DEFAULT_PROJECT_ROOT
from ddev_sync.engine.plan import available_selection_groups
from ddev_sync.engine.steps import SELECTION_GROUPS
from ddev_sync.errors import AmbiguousTargetError, DdevSyncError, SyncInterrupted
from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT
from ddev_sync.profiles.registry import DEFAULT_PROFILE_ROOT
from ddev_sync.transport.runner import CommandRunner, SubprocessRunner
from ddev_sync.transport.ssh import prepare_control_directory

# `build` means exactly `--steps=setup --features=node-build
# --only-feature-setup` - nothing more. These three constants are the single
# place that equivalence is spelled out; `build_cmd` and
# `_reject_build_conflicts` below read them instead of writing the strings
# out a second time, so the alias cannot quietly drift from the thing it is
# shorthand for.
#
# `--only-feature-setup` was added for `build` specifically (final
# whole-branch review): without it, `--steps=setup --features=node-build`
# plans the WHOLE setup step - on Laravel that includes the profile's own
# `composer install`, `php artisan migrate --force`, `optimize:clear` and
# `storage:link`, not just node-build's two commands. Someone typing `build`
# to re-run Vite got an unprompted migration against their database; design
# doc §6 promises "rebuild the frontend, nothing else", which the old long
# form never actually did. This is why the documented long form grew a third
# flag instead of `build` quietly doing something the equivalent CLI
# invocation could not reproduce - see `engine/plan.py::build_plan`'s
# `only_feature_setup` for how the filtering itself works.
_BUILD_STEPS = "setup"
_BUILD_FEATURES = "node-build"
_BUILD_IMPLIES = (
    f"'build' already implies --steps={_BUILD_STEPS} --features={_BUILD_FEATURES} "
    "--only-feature-setup"
)


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
    only_feature_setup: bool
    dry_run: bool
    assume_yes: bool
    verbose: int
    # One runner for the whole invocation. It is first used while the context
    # is built - a `deployer:` target asks the project's Deployer where to
    # connect - and then executes the sync itself, so the two cannot disagree
    # about what a command is or where it runs.
    runner: CommandRunner


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--profile-root", type=click.Path(path_type=Path), default=DEFAULT_PROFILE_ROOT)
@click.option("--feature-root", type=click.Path(path_type=Path), default=DEFAULT_FEATURE_ROOT)
@click.option("--home", type=click.Path(path_type=Path), default=None)
@click.option("--project-root", type=click.Path(path_type=Path), default=DEFAULT_PROJECT_ROOT)
@click.option(
    "--yes", "assume_yes", is_flag=True, help="Assume every default; skip interactive prompts."
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Show more: -v each step's plan detail, -vv command output, -vvv argv and timings.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    # Not "execute nothing": a target with a `deployer:` block is resolved
    # before the plan exists, and that runs `dep config` in the web
    # container, which evaluates the project's own `deploy.php`. The help a
    # user reads has to say so, since it is the only place they might look
    # before trusting a dry run on a production target.
    help=(
        "Print the plan and run none of its steps. A target whose connection "
        "comes from Deployer is still resolved, which runs 'dep config' in the "
        "web container."
    ),
)
@click.option(
    "--steps",
    default=None,
    # The five group names are the vocabulary, not the offer: the runtime
    # accepts only those this project would produce work for
    # (`available_selection_groups`), and refuses the rest by name. Listing
    # all five unconditionally and saying nothing about that left the help
    # advertising steps the very next run rejects.
    help=(
        f"Comma-separated: {', '.join(sorted(SELECTION_GROUPS))}. "
        "Only the ones this project has work for are accepted."
    ),
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
@click.option(
    "--only-feature-setup",
    is_flag=True,
    help=(
        "Restrict the setup step to feature-contributed commands only "
        "(what 'build' implies; see --help build)."
    ),
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
    only_feature_setup: bool,
    steps: str | None,
    dry_run: bool,
    assume_yes: bool,
    verbose: int,
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
    _reject_build_conflicts(
        ctx.invoked_subcommand, steps, features_flag, no_features, only_feature_setup
    )
    _reject_init_conflicts(
        ctx.invoked_subcommand,
        {
            "--target": target_name,
            "--steps": steps,
            "--features": features_flag,
            "--no-features": no_features,
            "--only-feature-setup": only_feature_setup,
            "--dry-run": dry_run,
            "--yes": assume_yes,
        },
    )
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
        only_feature_setup=only_feature_setup,
        dry_run=dry_run,
        assume_yes=assume_yes,
        verbose=verbose,
        runner=SubprocessRunner(),
    )
    # Here, once, rather than in the transport: the argv builders stay pure,
    # and a directory that is not safe to share a connection through is
    # refused before anything has run.
    prepare_control_directory()
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
    only_feature_setup: bool,
) -> None:
    """Refuse `build` combined with any option it already fixes.

    `build` means exactly `--steps=setup --features=node-build
    --only-feature-setup` (see `_BUILD_STEPS`/`_BUILD_FEATURES`); overriding
    one silently - which is what happened before this check existed - is the
    same failure shape the module docstring opens with: an option the user
    typed, accepted without complaint, and then ignored. Refusing the
    combination, the same way `--features` and `--no-features` together are
    refused, means nothing a user typed on the command line is ever quietly
    thrown away.
    """
    if invoked_subcommand != "build":
        return
    if steps is not None:
        raise click.UsageError(f"--steps cannot be combined with 'build'; {_BUILD_IMPLIES}")
    if features_flag is not None:
        raise click.UsageError(f"--features cannot be combined with 'build'; {_BUILD_IMPLIES}")
    if no_features:
        raise click.UsageError(f"--no-features cannot be combined with 'build'; {_BUILD_IMPLIES}")
    if only_feature_setup:
        raise click.UsageError(
            f"--only-feature-setup cannot be combined with 'build'; {_BUILD_IMPLIES}"
        )


def _reject_init_conflicts(invoked_subcommand: str | None, given: dict[str, object]) -> None:
    """Refuse `init` combined with an option that would be accepted and ignored.

    `--dry-run` is the one that matters: `init` writes a file, and a user who
    typed it expected nothing to change. The others - `--yes` cannot answer a
    question about a host, `--target` is the name the wizard asks for - would
    be dropped just as silently, which is the failure `_reject_build_conflicts`
    exists for. `--type` and `--framework-version` are not here: `init` uses
    both, for the question about what the project is and for the check after.
    """
    if invoked_subcommand != "init":
        return
    for option, value in given.items():
        if value is not None and value is not False:
            raise click.UsageError(
                f"{option} cannot be combined with 'init'; "
                "init asks for what it needs and would ignore it"
            )


@cli.command("help")
@click.argument("command", required=False)
@click.pass_context
def help_cmd(ctx: click.Context, command: str | None) -> int:
    """Show this help, or the help of COMMAND."""
    # `ddev help` works for DDEV itself, so `ddev sync help` is what a user
    # guesses. The group's help is what they want, not this command's, which
    # is why it reaches for the parent context.
    #
    # `ddev help <command>` works for DDEV too, so `ddev sync help doctor` is
    # the same user's second guess; without the argument it exited 2 with
    # "Got unexpected extra argument". A name that is not a subcommand falls
    # back to the group help rather than failing, but says so first - the
    # help it then prints is not the help that was asked for, and printing it
    # silently would answer a question nobody asked.
    group_context = ctx.parent or ctx
    if command is not None:
        subcommand = cli.get_command(group_context, command)
        if subcommand is None:
            click.echo(f"There is no '{command}' command; here is the full help.", err=True)
        else:
            with click.Context(subcommand, info_name=command, parent=group_context) as sub:
                click.echo(sub.get_help())
            return 0
    click.echo(group_context.get_help())
    return 0


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
    # The real environment and the real stdout are consulted here, at the
    # edge, as `_run` does too; nothing below this call sniffs them, it
    # receives the answer as a plain boolean.
    #
    # Inside the DDEV add-on this follows the terminal only: DDEV allocates a
    # TTY for a service command when stdin and stdout are both terminals, and
    # forwards the host's TERM and COLORTERM with it, but not NO_COLOR or
    # FORCE_COLOR. Those two take effect when the engine runs directly (from
    # a checkout, say). Do not "fix" that by listing them under `environment:`
    # in docker-compose.ddev-sync.yaml: Compose binds those values when the
    # container is created (`ddev start`), and a `docker exec` merely inherits
    # them, so `NO_COLOR=1 ddev sync doctor` would still do nothing while a
    # value that happened to be set at the last start would stick silently.
    # If this needs to be controllable per call, the answer is a `--no-color`
    # flag that works on every path, not a compose entry.
    use_colour = colour_enabled(os.environ, sys.stdout)
    # `click.echo` strips ANSI sequences from any stream that is not a
    # terminal unless told otherwise, which would cancel `FORCE_COLOR` - the
    # very case that asks for colour without a terminal. `None` leaves
    # click's own default in place when colour is off.
    echo = partial(click.echo, color=True if use_colour else None)
    return doctor_command(context, echo, runner=options.runner, colour=use_colour)


@cli.command("init")
@click.pass_obj
def init_cmd(options: _Options) -> int:
    """Create a sync target, then check it."""
    use_colour = colour_enabled(os.environ, sys.stdout)
    return init_command(
        project_root=options.project_root,
        home=options.home,
        profile_root=options.profile_root,
        feature_root=options.feature_root,
        runner=options.runner,
        echo=partial(click.echo, color=True if use_colour else None),
        colour=use_colour,
        type_flag=options.type_flag,
        framework_version_flag=options.framework_version_flag,
        wait=Waiting(sys.stderr, enabled=waiting_enabled(os.environ, sys.stderr)),
    )


@cli.command("build")
@click.pass_obj
def build_cmd(options: _Options) -> int:
    """Alias for `--steps=setup --features=node-build --only-feature-setup`.

    By the time this callback runs, `cli()` has already rejected `build`
    combined with `--steps`, `--features`, `--no-features` or
    `--only-feature-setup` (`_reject_build_conflicts`), so `options.
    features_flag` is always `None` and both `options.no_features` and
    `options.only_feature_setup` are always `False` here - there is nothing
    left to override, only to fill in. `replace` still states every forced
    value explicitly rather than relying on that silently, so the alias
    stays self-describing even if that guarantee ever moved. Routing through
    `_run` - the same function the bare `ddev-sync` invocation calls - is
    what keeps this an alias rather than a second implementation that could
    drift from the long form.
    """
    forced = replace(
        options, features_flag=_BUILD_FEATURES, no_features=False, only_feature_setup=True
    )
    return _run(forced, steps=_BUILD_STEPS, dry_run=options.dry_run, assume_yes=options.assume_yes)


def _run(options: _Options, *, steps: str | None, dry_run: bool, assume_yes: bool) -> int:
    """Plan (and maybe execute) a sync; the one code path both `cli` and `build_cmd` call."""
    context = _build_run_context(options)
    selection = resolve_selection(
        steps,
        assume_yes,
        available=available_selection_groups(
            target=context.target,
            project=context.project,
            profile=context.profile,
            merged=context.merged,
            features=context.features,
            only_feature_setup=options.only_feature_setup,
        ),
    )
    use_colour = colour_enabled(os.environ, sys.stdout)
    return run_command(
        context,
        selection,
        runner=options.runner,
        dry_run=dry_run,
        assume_yes=assume_yes,
        # `color=True` only when colour was chosen: click strips escapes from a
        # non-terminal unless told otherwise, which is the very case
        # `FORCE_COLOR` asks for. `None` leaves click's default in place.
        echo=partial(click.echo, color=True if use_colour else None),
        only_feature_setup=options.only_feature_setup,
        colour=use_colour,
        verbose=options.verbose,
        stdout_is_terminal=sys.stdout.isatty(),
    )


# Shown while the project is read and the target resolved, until something
# slower says what it is doing instead.
READING_THE_PROJECT = "Reading the project and its configuration"


def _build_run_context(options: _Options) -> RunContext:
    """Assemble the run context, falling back to an interactive target pick.

    `build_context` resolves the target itself and raises
    `AmbiguousTargetError` immediately - never prompting - when
    `target_name` is `None` and the project defines zero or several targets.
    Retrying only on that specific error, instead of resolving the target
    interactively up front, keeps `read_ddev_project`'s "this does not look
    like a DDEV project" diagnostic as the first thing a broken project
    sees, rather than masking it behind a target question the project can
    never answer correctly anyway.

    The `except` is that subclass and not `TargetError`, because a retry can
    only ever help when the question is *which* target. Any other target
    error - a missing `stage`, an unknown `extends`, a literal beside a
    `deployer:` block - is the same error whichever name is supplied, and
    catching it here rebuilt everything (the project read, the config load,
    the type detection, the feature and profile resolution) only to fail
    identically. For a `deployer:` target that rebuild also ran `dep config`
    in the web container a second time, evaluating the project's own PHP
    twice in one sync and giving it a second chance to answer differently -
    exactly what memoising the resolver exists to prevent.
    """

    # Around the work and never around `resolve_target_name`, which may ask
    # which target to use: nothing is drawn while a question is on screen.
    waiting = Waiting(sys.stderr, enabled=waiting_enabled(os.environ, sys.stderr))

    def build(name: str | None) -> RunContext:
        with waiting(READING_THE_PROJECT):
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
                runner=options.runner,
                wait=waiting,
            )

    if options.target_name is not None:
        return build(resolve_target_name(options.target_name, options.project_root, options.home))
    try:
        return build(None)
    except AmbiguousTargetError:
        return build(resolve_target_name(None, options.project_root, options.home))


def _group_option_names() -> frozenset[str]:
    """Every long/short option string declared on the `cli` group itself.

    Used only to recognise a `NoSuchOption` whose name happens to be one of
    these - see `_hint_if_misplaced_group_option` - so it is computed from
    `cli.params` rather than written out a second time, the same reasoning
    the module docstring gives for every option living in exactly one place.
    """
    return frozenset(opt for param in cli.params for opt in param.opts)


def _misplaced_group_option_hint(error: click.exceptions.NoSuchOption) -> str | None:
    """A hint line, or `None`, for a `NoSuchOption` naming a `cli` group option.

    Every option is declared once, on the group, and must precede the
    subcommand name (module docstring above). A subcommand's own Click
    parser therefore does not recognise `--target`, `--profile-root`, or any
    other group option typed after, say, `doctor` - Click reports that as a
    generic "no such option", which is accurate but does not tell the user
    the option merely needs to move. This affects every subcommand
    (`doctor`, `targets`, `build`) alike, since none of them declare options
    of their own - so the check is generic, keyed off the group's own option
    list, rather than special-cased to whichever subcommand a bug report
    happened to name.

    `ClickException.message` is typed `Final` in Click's own stubs, so the
    hint is returned for the caller to print as a second line rather than
    spliced into `error.message` in place.
    """
    if error.option_name not in _group_option_names():
        return None
    return (
        f"{error.option_name!r} is a ddev-sync option and must precede the "
        f"subcommand, e.g. 'ddev-sync {error.option_name} ... <subcommand>'."
    )


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
    except SyncInterrupted as error:
        # Before `DdevSyncError` on purpose: it subclasses it, so a handler
        # placed after would return 1 and the exit code would be silently
        # wrong. 130 is the conventional code for a process ended by SIGINT.
        click.echo(str(error), err=True)
        return 130
    except (click.exceptions.Abort, KeyboardInterrupt):
        # An interrupt that reached here was raised outside the executor -
        # during the step picker, while the configuration was being read, or
        # while the plan was built - because one raised inside it, steps and
        # `post_sync` hooks included, has already become `SyncInterrupted`.
        # No step was cut short, so there is nothing to report beyond the
        # fact itself.
        # `Abort` is what actually arrives: Click catches the
        # `KeyboardInterrupt` inside `cli.main` and re-raises it as `Abort`
        # even with `standalone_mode=False`, so a handler for the bare
        # interrupt alone would never run and the traceback would remain.
        click.echo("Interrupted.", err=True)
        return 130
    except DdevSyncError as error:
        click.echo(str(error), err=True)
        return 1
    except ValueError as error:
        click.echo(str(error), err=True)
        return 1
    except click.exceptions.NoSuchOption as error:
        error.show()
        hint = _misplaced_group_option_hint(error)
        if hint is not None:
            click.echo(hint, err=True)
        return error.exit_code
    except click.ClickException as error:
        error.show()
        return error.exit_code
    return result
