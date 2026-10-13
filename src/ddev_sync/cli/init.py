"""`ddev sync init`: ask for one target, write it, then check it.

The order of the questions is not the order a person would list the answers
in. Within the target's own questions, the name comes before the connection
because it is all that is needed to know whether the target already exists,
and a user who declines to replace one should lose one answer, not five. The
project questions - production URL, env file, features - are asked first of
all, ahead of the name, because they describe the project rather than the
target; nothing here protects *them* from a later decline the way the name
ordering protects the connection. What holds instead is narrower: nothing is
written until every question has an answer, so an `init` that ends anywhere
before the write - at the name, at a declined collision, at the connection -
leaves the configuration file exactly as it found it. The cost is real: the
production-URL, env-file and feature answers are asked again on the next run,
the same as the name and connection would be.

The global file is never written. It is mounted read-only in the sidecar on
purpose (`docker-compose.ddev-sync.yaml`), and the sidecar is where this
command normally runs, so offering it would end in a permission error. The
advice the shipped template gives about keeping hosts out of the repository
is said after the write instead. `.ddev/ddev-sync.yaml` is the only file most
runs touch; `_offer_override` below is the one place that also writes a local
layer - credentials entered by hand, which do not belong in the file that is
committed with the project - and it writes to whichever of the two local
paths is already in use, never both (`cli/init.py::_local_path`).

Every question returns `None` when there is no terminal (see `cli/prompts.py`),
and that is turned into an error here, the moment it happens, rather than
guessed past. There is no flag that supplies an answer instead: a wizard whose
answers all come from the command line would be a configuration file written
twice, so the error names the file to edit.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ddev_sync.cli.commands import _execution_context, doctor_command
from ddev_sync.cli.context import (
    Detection,
    ProjectResolution,
    RunContext,
    build_context,
    resolve_project,
)
from ddev_sync.cli.prompts import (
    DEPLOYER_SOURCE,
    ENV_FILE_NONE,
    REMOTE_ENV_NONE,
    WP_CLI_SOURCE,
    ask_connection,
    ask_credentials,
    ask_production_url,
    ask_stage,
    ask_target_name,
    choose_connection_source,
    choose_credentials_source,
    choose_deployer_host,
    choose_env_file,
    choose_features,
    choose_remote_env_file,
    confirm_detection,
    confirm_replacement,
)
from ddev_sync.cli.render import describe_endpoint
from ddev_sync.config.detect import WORDPRESS_PROFILE
from ddev_sync.config.loader import (
    GLOBAL_LABEL,
    LEGACY_LOCAL_LABEL,
    LOCAL_LABEL,
    PROJECT_LABEL,
    ConfigPaths,
    discover_config_paths,
    load_user_layers,
)
from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.config.targets import CollectedTarget, collect_targets, resolve_target
from ddev_sync.config.writer import WriteRefused, render_target, write_settings, write_target
from ddev_sync.errors import ConfigError, DdevSyncError, DeployerError
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import DATABASE_ENV_FILE_KEY, remote_env_candidates
from ddev_sync.operations.placeholders import (
    PRODUCTION_URL_CONFIG_KEY,
    PRODUCTION_URL_PLACEHOLDER,
    validate_production_url,
)
from ddev_sync.operations.preflight import PreflightOperation, verify_credentials
from ddev_sync.transport.deployer import ProjectDeployer, make_deployer_resolver
from ddev_sync.transport.runner import CommandRunner
from ddev_sync.transport.ssh import ssh_command

DEFAULT_NAME = "production"

# How many times the name is asked for when the target already exists and the
# user will not replace it. The first answer is one of the three. A decline is
# an ordinary thing to do - the name was taken, so a different one is wanted -
# and the next question already offers a free name, so a second round nearly
# always settles it. A third covers a user who is still choosing. After that
# the loop is more likely to be a user who cannot leave than one who is
# choosing, and `Ctrl-C` is the only exit the prompt itself gives, so the
# command ends with what it did and what to do instead.
MAX_ATTEMPTS = 3

# The two file names Deployer itself looks for (`transport/deployer.py`).
_DEPLOYER_RECIPES = ("deploy.php", "deploy.yaml")


def init_command(
    *,
    project_root: Path,
    home: Path,
    profile_root: Path,
    feature_root: Path,
    runner: CommandRunner,
    echo: Callable[[str], None],
    colour: bool = False,
    type_flag: str | None = None,
    framework_version_flag: str | None = None,
) -> int:
    """Create one target and run `doctor` against it.

    The exit code is `doctor`'s, except that a write the writer refused is 1.
    A target that was written stays written when `doctor` fails: the user
    corrects a file, they do not answer every question again.
    """
    paths = discover_config_paths(project_root, home)
    resolution = resolve_project(
        project_root=project_root,
        home=home,
        type_flag=type_flag,
        framework_version_flag=framework_version_flag,
        profile_root=profile_root,
        feature_root=feature_root,
    )
    project = resolution.project
    _confirm_detection(resolution.detection)
    # Asked before the target and written after it: an `init` that ends at the
    # name question must leave the file exactly as it found it, which is part
    # A's promise and the reason these answers are not written as they arrive.
    settings = _ask_project_settings(resolution, echo)
    name = _ask_name(paths, echo)

    # One resolver for the whole command: it is lazy, so a manual entry never
    # runs `dep`, and when it does run it is once - for the host list, for the
    # check, and for reading back what was written.
    deployer = make_deployer_resolver(project, runner)
    source = choose_connection_source(deployer_available=_has_deployer_recipe(project.root))
    if source is None:
        raise _no_terminal()
    connection = (
        _deployer_connection(deployer) if source == DEPLOYER_SOURCE else _manual_connection()
    )
    stage = ask_stage()
    if stage is None:
        raise _no_terminal()

    # Stage is asked whichever way the connection was given. A Deployer alias
    # is not a stage - hosts are named `production` by some projects and after
    # a machine by others - and a target without one does not resolve.
    block = render_target(name, {**connection, "stage": stage})
    try:
        write_target(path=paths.project_config, paths=paths, name=name, block=block)
    except WriteRefused as refusal:
        echo(str(refusal))
        echo(f"Add the target to {PROJECT_LABEL} yourself, under `targets:`:\n\n{block}")
        return 1

    if settings:
        try:
            write_settings(path=paths.project_config, paths=paths, settings=settings)
        except WriteRefused as refusal:
            echo(str(refusal))
            echo(
                f"The target {name!r} was written. Add the rest to {PROJECT_LABEL} "
                f"yourself: {', '.join(f'{key}: {value!r}' for key, value in settings.items())}"
            )
            return 1

    # The file is on disk by now, but "wrote" is only said once the outcome is
    # known: a success line followed by an error would call a target that does
    # not resolve a success. Whatever ends the check - a refusal, an interrupt,
    # an error nobody foresaw - the user is told which file holds the target.
    try:
        context = _resolve_context(
            project_root=project_root,
            home=home,
            name=name,
            type_flag=type_flag,
            framework_version_flag=framework_version_flag,
            profile_root=profile_root,
            feature_root=feature_root,
            runner=runner,
            deployer=deployer,
        )
        execution = _execution_context(
            context, runner=runner, assume_yes=True, secrets=set(), stdout_is_terminal=False
        )
        wordpress = resolution.profile.name == WORDPRESS_PROFILE
        fixed, wrote_legacy_credentials = _fix_credentials(execution, paths, wordpress, echo)
        if fixed:
            # `_fix_credentials` may have just written `database.env_file`;
            # `context` above was resolved before that write, so `doctor`
            # would otherwise report on the configuration as it was found,
            # not as it was fixed.
            context = _resolve_context(
                project_root=project_root,
                home=home,
                name=name,
                type_flag=type_flag,
                framework_version_flag=framework_version_flag,
                profile_root=profile_root,
                feature_root=feature_root,
                runner=runner,
                deployer=deployer,
            )
        code = doctor_command(context, echo, runner=runner, colour=colour)
        written = _written_target(paths, name, deployer)
        others = _other_layers_defining(paths, name)
    except DdevSyncError as error:
        raise DdevSyncError(
            f"the target {name!r} is in {PROJECT_LABEL}, but it could not be checked: {error}. "
            f"Edit it there, then check it with 'ddev sync --target {name} doctor'."
        ) from error
    except BaseException:
        echo(
            f"The target {name!r} is in {PROJECT_LABEL}, but the check did not finish. "
            f"Edit it there, or check it with 'ddev sync --target {name} doctor'."
        )
        raise
    if others and context.target != written:
        # `doctor` has just validated this other definition and may have found
        # it green; saying "wrote" over that would be a success for a target
        # the user cannot have meant.
        raise DdevSyncError(
            f"the target {name!r} was written to {PROJECT_LABEL}, but it is also defined in "
            f"{', '.join(others)}, and the check above ran against the combination, not "
            "against what was just written. The files are merged global, then project, then "
            "local, and the last one to set a value wins. Make the definitions agree, or "
            "remove one."
        )
    if code == 0:
        echo(f"Wrote the target {name!r} to {PROJECT_LABEL}.")
    else:
        echo(
            f"The target stays in {PROJECT_LABEL}. Edit it there, then check it again with "
            f"'ddev sync --target {name} doctor'."
        )
    # That file is committed with the project, and the template's advice about
    # hosts and users cannot be followed by `init`, so it is given here.
    echo(
        f"{PROJECT_LABEL} is committed with the project. To keep this host and user out of "
        f"the repository, move the target to {GLOBAL_LABEL}."
    )
    if wrote_legacy_credentials:
        # `_offer_override` already `echo`d the full warning synchronously, at
        # write time - before `doctor_command` above ran, so a `doctor` that
        # raises (it makes real ssh calls) cannot make the write happen with
        # no warning at all. This is a shorter restatement, not a repeat of
        # the same sentence: said again here, last, in case the checklist
        # pushed the first one off screen.
        echo(
            f"Reminder: the credentials just written to {LEGACY_LOCAL_LABEL} are not "
            f"gitignored - move them to {LOCAL_LABEL}."
        )
    return code


def _confirm_detection(detection: Detection) -> None:
    """Ask whether the detected profile is right; refuse to go on if it is not.

    It comes first because everything written afterwards is checked against
    the profile this resolves to. The detection is handed in rather than
    computed here: `resolve_project` already did it, with the same inputs a
    real run uses.
    """
    profile = detection.profile
    version = detection.version
    if version is None:
        described, origin = profile.value, f"{profile.source}; no version detected"
    else:
        described = f"{profile.value} {version.value}"
        origin = f"{profile.source}; version from {version.source}"
    answer = confirm_detection(described, origin)
    if answer is None:
        raise _no_terminal()
    if not answer:
        raise DdevSyncError(
            "nothing was written. Set `project.type` and `project.version` in "
            f"{PROJECT_LABEL} to what this project is, then run 'ddev sync init' again."
        )


def _ask_project_settings(
    resolution: ProjectResolution, echo: Callable[[str], None]
) -> dict[str, Any]:
    """The configuration keys `init` asks about, beyond the target itself.

    Each question is skipped when there is nothing to decide, so a project
    that needs none of them sees no extra question at all. The keys are dotted
    paths, which is what `write_settings` takes. `echo` prints a line beside
    an answer that is not itself a value to write: naming no env file is one.
    """
    settings: dict[str, Any] = {}
    if _wants_production_url(resolution):
        url = ask_production_url()
        if url is None:
            raise _no_terminal()
        settings[PRODUCTION_URL_CONFIG_KEY] = url.strip()
    candidates = _missing_env_file(resolution)
    if candidates is not None:
        present = sorted(
            entry.name
            for entry in resolution.project.root.iterdir()
            if entry.is_file() and entry.name.startswith(".env")
        )
        answer = choose_env_file(candidates, present)
        if answer is None:
            raise _no_terminal()
        if answer == ENV_FILE_NONE:
            echo(
                f"No environment file was named. The sync will stop at the env step, "
                f"after the database has been imported - create one of "
                f"{', '.join(candidates)} in the project root before running it."
            )
        else:
            settings["env.file"] = [answer]
    labels = _detected_features(resolution)
    if labels is not None:
        kept = choose_features(labels)
        if kept is None:
            raise _no_terminal()
        # Written only when something was actually dropped. A `features:` key
        # switches detection off for good (`features/resolver.py:68`), so
        # writing one that merely restates what detection found would freeze
        # this project's feature set for no benefit at all.
        if len(kept) != len(labels):
            settings["features"] = kept
            echo(
                "Feature detection is now off for this project: the `features:` key in "
                f"{PROJECT_LABEL} lists what the sync uses. Delete it to detect again."
            )
    return settings


def _detected_features(resolution: ProjectResolution) -> dict[str, str] | None:
    """Detected features and why, or `None` when there is no question to ask.

    There is none when nothing was activated, and none when the user has
    already written a `features:` key - in that case the resolution's entries
    came from that key rather than from detection, so offering to deselect
    them would be offering to rewrite an answer already given. That is read
    from the layers directly rather than inferred from `reason`'s text.
    """
    if any("features" in layer.data for layer in resolution.user_layers):
        return None
    activated = resolution.features.activated
    if not activated:
        return None
    return {entry.feature.name: entry.reason for entry in activated}


def _missing_env_file(resolution: ProjectResolution) -> list[str] | None:
    """The configured candidates when none exists; `None` when there is no question.

    The conditions for "there is no env step" are `engine/plan.py::_env`'s
    own, read from the same merged configuration, so this question appears
    exactly when that step would: the strategy is not `none`, and `env.set` or
    `env.default` has something in it - a non-mapping of either counts as
    empty, as it does there. The file is matched the way
    `operations/env.py::_find_env_file` matches it, against real directory
    entries rather than a constructed path, because `is_file()` folds case on
    a case-insensitive filesystem. The guards are `_env`'s too, raising the
    same sentences: a hand-edited `env.file` that is neither a name nor a list
    of names would otherwise reach the comprehension below as a raw
    `TypeError`, and `env.default` under `append_block` is a combination no
    step can honour, which `_env` refuses before it looks at any file.
    """
    node = resolution.merged.data.get("env", {})
    settings = node if isinstance(node, dict) else {}
    if str(settings.get("strategy", "patch_keys")) == "none":
        return None
    raw_values = settings.get("set", {})
    raw_default = settings.get("default", {})
    imposed = raw_values if isinstance(raw_values, dict) else {}
    defaulted = raw_default if isinstance(raw_default, dict) else {}
    if not imposed and not defaulted:
        return None
    if defaulted and str(settings.get("strategy", "patch_keys")) == "append_block":
        raise ConfigError("env.default cannot be used with strategy 'append_block'")
    configured = settings.get("file", [".env.local", ".env"])
    if not isinstance(configured, str | list):
        raise ConfigError(
            f"env.file must be a file name or a list of file names, got {configured!r}"
        )
    candidates = (
        [configured] if isinstance(configured, str) else [str(entry) for entry in configured]
    )
    existing = {entry.name for entry in resolution.project.root.iterdir() if entry.is_file()}
    if any(name in existing for name in candidates):
        return None
    return candidates


def _wants_production_url(resolution: ProjectResolution) -> bool:
    """Whether a rule needs the URL and the configuration does not supply it.

    The search is for the literal placeholder anywhere under `sanitize`, in
    the *unexpanded* merged configuration - not for the profile being
    `wordpress`. A rule may carry it in `sql:` or in any parameter, and a
    project or a future profile that adopts the placeholder is asked the same
    question without anything here learning its name.
    """
    configured = _setting(resolution.merged.data, PRODUCTION_URL_CONFIG_KEY)
    if isinstance(configured, str) and validate_production_url(configured) is None:
        return False
    return _mentions(resolution.merged.data.get("sanitize"), PRODUCTION_URL_PLACEHOLDER)


def _setting(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _mentions(value: Any, needle: str) -> bool:
    """Whether `needle` occurs in any string anywhere inside `value`."""
    if isinstance(value, str):
        return needle in value
    if isinstance(value, list):
        return any(_mentions(item, needle) for item in value)
    if isinstance(value, dict):
        return any(_mentions(item, needle) for item in value.values())
    return False


def _ask_name(paths: ConfigPaths, echo: Callable[[str], None]) -> str:
    """Ask for the target's name, at most `MAX_ATTEMPTS` times.

    A name `.ddev/ddev-sync.yaml` does not define is taken at once. One it does
    define is taken only if the user agrees to replace it; otherwise the name
    is asked again, with a free one offered, until the attempts run out and the
    command ends with nothing written.
    """
    taken = _targets_in_project_file(paths)
    default = DEFAULT_NAME
    for _ in range(MAX_ATTEMPTS):
        answer = ask_target_name(default)
        if answer is None:
            raise _no_terminal()
        name = answer.strip()
        if name not in taken:
            return name
        replace = confirm_replacement(name, PROJECT_LABEL, describe_endpoint(taken[name].spec))
        if replace is None:
            raise _no_terminal()
        if replace:
            return name
        echo(f"Nothing was replaced. Choose another name than {name!r}.")
        default = _free_name(name, taken)
    raise DdevSyncError(
        f"nothing was written: a target of the name you gave already exists, and you did "
        f"not want it replaced, {MAX_ATTEMPTS} times. Run 'ddev sync init' again, or edit "
        f"{PROJECT_LABEL} yourself."
    )


def _targets_in_project_file(paths: ConfigPaths) -> dict[str, CollectedTarget]:
    """The targets the project file defines by itself, not what several files merge to.

    A target that only another file defines is not what the writer would
    replace, so asking about it would be asking the wrong question.
    """
    own = [layer for layer in load_user_layers(paths) if layer.label == PROJECT_LABEL]
    return collect_targets(own)


def _written_target(paths: ConfigPaths, name: str, deployer: ProjectDeployer) -> ResolvedTarget:
    """`name` as the project file alone defines it: what was just written.

    The resolver is needed for a `deployer:` block, which is what an import
    writes; without it the block cannot be resolved and the read-back fails.
    """
    own = [layer for layer in load_user_layers(paths) if layer.label == PROJECT_LABEL]
    return resolve_target(name, collect_targets(own), resolve_deployer=deployer)


def _other_layers_defining(paths: ConfigPaths, name: str) -> list[str]:
    """Labels of the files other than the project file that also define `name`."""
    return [
        layer.label
        for layer in load_user_layers(paths)
        if layer.label != PROJECT_LABEL and name in collect_targets([layer])
    ]


def _free_name(name: str, taken: dict[str, CollectedTarget]) -> str:
    """`name` with the first number after it that the file does not use yet."""
    number = 2
    while f"{name}-{number}" in taken:
        number += 1
    return f"{name}-{number}"


def _manual_connection() -> dict[str, object]:
    connection = ask_connection()
    if connection is None:
        raise _no_terminal()
    return connection


def _deployer_connection(deployer: ProjectDeployer) -> dict[str, object]:
    """The `deployer:` block for the host the user picks from Deployer's own list.

    The block names the alias and nothing else. The engine rejects a host,
    user, port or path beside it, and resolving them when a run needs them is
    what keeps a project that redeploys elsewhere working without anyone
    editing this file.

    When `dep config` cannot be read this stops rather than falling back to
    manual entry. The user asked for the connection to come from Deployer; a
    target typed in instead would not be that, and whatever broke - a stopped
    web container, a recipe that errors, no `dep` - breaks every later run of a
    `deployer:` target the same way, so it is better met here, before a write.
    Nothing has been written at this point, which the message says, so there is
    no target to believe in.
    """
    try:
        hosts = deployer.hosts()
    except DeployerError as error:
        raise DeployerError(
            f"nothing was written: the hosts could not be read from Deployer: {error}. "
            "Fix that and run 'ddev sync init' again, or choose to enter the connection by hand."
        ) from error
    if not hosts:
        raise DeployerError(
            "nothing was written: 'dep config' ran, but Deployer declares no hosts. Declare one "
            "in deploy.php or deploy.yaml and run 'ddev sync init' again, or choose to enter "
            "the connection by hand."
        )
    alias = choose_deployer_host(hosts)
    if alias is None:
        raise _no_terminal()
    return {"deployer": {"host": alias}}


def _has_deployer_recipe(root: Path) -> bool:
    return any((root / recipe).is_file() for recipe in _DEPLOYER_RECIPES)


def _resolve_context(
    *,
    project_root: Path,
    home: Path,
    name: str,
    type_flag: str | None,
    framework_version_flag: str | None,
    profile_root: Path,
    feature_root: Path,
    runner: CommandRunner,
    deployer: ProjectDeployer,
) -> RunContext:
    """`build_context` with `init_command`'s own arguments bound.

    Called twice when `_fix_credentials` actually writes something: once to
    resolve the target before offering the fix, once more afterwards so that
    `doctor` sees the configuration as it was fixed, not as it was found.
    """
    return build_context(
        project_root=project_root,
        home=home,
        target_name=name,
        type_flag=type_flag,
        framework_version_flag=framework_version_flag,
        profile_root=profile_root,
        feature_root=feature_root,
        runner=runner,
        deployer=deployer,
    )


def _fix_credentials(
    execution: ExecutionContext,
    paths: ConfigPaths,
    wordpress: bool,
    echo: Callable[[str], None],
) -> tuple[bool, bool]:
    """Offer to fix a credentials source that does not resolve.

    Runs after the target is written because `read_remote_credentials` needs
    a resolved target, and before `doctor` so that the checklist the user
    sees describes the configuration as fixed rather than as merely found
    broken.

    Goes through `PreflightOperation().check()` rather than its private
    `_probe`/`_check_credentials`: `tests/cli/test_init.py::report_green`
    stubs `check` for roughly twenty other `init` tests, and calling the
    private methods instead would run a real probe through their fake runner
    on every one of them and fire this question where none of them expect
    one. `check` also keeps the guard meaningful on its own terms -
    `credentials_ok` is `False` only when ssh and the deploy path are already
    green (see `PreflightReport`'s own docstring) - so a connectivity failure
    still cannot reach the question below; that failure has its own `doctor`
    row and its own fix.

    The first element is whether the question ran, not whether it wrote
    anything: a run that reaches the override (`REMOTE_ENV_NONE`) and still
    writes nothing - a declined source, or one whose probe fails - still
    spent this function's one `check()` call, and `init_command` uses the
    return value to decide whether the context it hands `doctor` next needs
    re-resolving from disk - cheap next to the ssh round trip `check()`
    already paid for either way.

    The second element is whether `_offer_override` wrote to the legacy local
    path - its own warning is already `echo`d by the time this returns;
    `init_command` uses this only to decide whether to restate it in its
    closing lines, not to print it for the first time.
    """
    report = PreflightOperation().check(execution)
    if report.credentials_ok is not False:
        return False, False
    if execution.config("database.remote_credentials", default="env") == "env":
        return True, _offer_remote_env_file(execution, paths, wordpress, echo)
    # The remote-file question's premise - a `.env` on the target that
    # nothing named - is only ever true for the `env` source. `explicit`
    # makes no network call at all, and `wp_cli` asks WordPress's own
    # `wp-cli`, not a file; asking it anyway led straight to a question that
    # could not fix anything, and only `REMOTE_ENV_NONE` reached the override
    # that could.
    return True, _offer_override(execution, paths, wordpress, echo)


def _offer_remote_env_file(
    execution: ExecutionContext,
    paths: ConfigPaths,
    wordpress: bool,
    echo: Callable[[str], None],
) -> bool:
    """Ask which file on the target really holds the database credentials.

    Only reached for an `env` source (`_fix_credentials`'s own gate): that is
    the one source this question's premise fits, since `present` comes from
    the target's own data root (`_remote_env_entries`) - so the usual cause
    of a red credentials row *here* - the file is there under a name nobody
    configured - is one keystroke away rather than a guess.

    `wordpress` is threaded through to `_offer_override` below, which is
    reached when naming a file does not settle it either - the last resort,
    for setups where nothing on the remote can be read back at all. Returns
    whatever that call returns - whether the override wrote to the legacy
    local path - unchanged, so `_fix_credentials` has one place to collect it
    regardless of which of the two questions actually wrote it.
    """
    looked_for = remote_env_candidates(execution)
    present = _remote_env_entries(execution)
    answer = choose_remote_env_file(looked_for, present)
    if answer is None:
        raise _no_terminal()
    if answer == REMOTE_ENV_NONE:
        return _offer_override(execution, paths, wordpress, echo)
    write_settings(
        path=paths.project_config, paths=paths, settings={DATABASE_ENV_FILE_KEY: [answer]}
    )
    return False


def _offer_override(
    execution: ExecutionContext,
    paths: ConfigPaths,
    wordpress: bool,
    echo: Callable[[str], None],
) -> bool:
    """Offer a credentials source other than the one that failed.

    Last resort by design: the credentials normally live on the target, which
    is what the question before this one tries to reach. This is for the
    setups where nothing on the remote can be read back - Bedrock and other
    env-based WordPress installs among them.

    Returns whether the write landed on `LEGACY_LOCAL_LABEL`, so
    `init_command` can restate the warning below in its own closing lines -
    it does not also carry the warning's text: this `echo`s it immediately,
    at write time, so nothing downstream (a `doctor` that raises, a
    multiply-defined target) can make the write happen with no warning ever
    reaching the user at all. The closing restatement is what used to be this
    function's only emission; echoing twice, once synchronously and once at
    the end, is deliberate - see `init_command`'s own comment at the second
    one.
    """
    source = choose_credentials_source(wordpress)
    if source is None:
        raise _no_terminal()
    if source == WP_CLI_SOURCE:
        # No secret: `wp_cli` reads the values from the target every run, so
        # the setting is a plain project-file key like any other.
        write_settings(
            path=paths.project_config,
            paths=paths,
            settings={"database.remote_credentials": "wp_cli"},
        )
        return False
    values = ask_credentials()
    if values is None:
        raise _no_terminal()
    if not _credentials_work(execution, values):
        echo(
            "Those credentials did not work against the target, so they were not "
            "written. Check them and run 'ddev sync init' again."
        )
        return False
    local_path = _local_path(paths)
    write_settings(
        path=local_path,
        paths=paths,
        settings={
            "database.remote_credentials": "explicit",
            "database.credentials": values,
        },
    )
    if local_path != paths.legacy_local_config:
        return False
    # The one path the add-on's shipped `.gitignore` does not cover - covering
    # it is why the layer moved to `LOCAL_LABEL` in the first place
    # (`config/loader.py`). Relevant only here, and only now: a project on the
    # new path has nothing to act on, and the risk this names only exists once
    # the secret has actually landed on the old one. Said now, synchronously,
    # not only in `init_command`'s closing lines: `doctor_command` right after
    # this makes real ssh calls and can raise, and a raise must not be the
    # reason a written production password goes unmentioned.
    echo(
        f"The credentials were written to {LEGACY_LOCAL_LABEL}. The add-on's shipped "
        f".gitignore does not cover that path - it covers {LOCAL_LABEL} instead - so "
        "this file is not ignored and the password it holds would be committed with "
        f"the project. Move it to {LOCAL_LABEL}."
    )
    return True


def _credentials_work(execution: ExecutionContext, values: dict[str, str]) -> bool:
    """Whether `values` actually authenticate, without writing them anywhere.

    A candidate layer on top of the merged configuration, probed through
    `verify_credentials` - the same `SHOW TABLES` round trip a real sanitize
    run would make - so what is proved here is that the remote actually
    accepts these values, not merely that five fields were typed in. `cache`
    is a plain constructor-settable field on the frozen `ExecutionContext`
    (`operations/base.py`), so `dataclasses.replace` both builds the
    candidate and empties it in one call - emptying it matters because the
    failure that brought the user into this branch is already memoised
    there, and a reused cache would return that instead of trying the
    candidate.

    `verify_credentials` itself calls `read_remote_credentials` first, so a
    value left empty (the one thing `_from_config` alone can catch) and a
    complete-but-wrong password (which it cannot) both surface as the one
    `OperationError` caught here - there is no reason for this function to
    call `read_remote_credentials` a second time to learn the same thing.
    """
    candidate = LayerDoc(
        label="answers",
        data={"database": {"remote_credentials": "explicit", "credentials": values}},
    )
    probe = replace(
        execution,
        merged=merge_layers([LayerDoc("merged", execution.merged.data), candidate]),
        cache={},
    )
    try:
        verify_credentials(probe)
    except OperationError:
        return False
    return True


def _local_path(paths: ConfigPaths) -> Path:
    """Where the local layer should be written.

    The new path, unless the old one is already in use. Writing the new file
    beside an existing old one would produce the very state `load_user_layers`
    refuses, and a command may not create the error it then declines to
    resolve.
    """
    return paths.legacy_local_config if paths.legacy_local_config.is_file() else paths.local_config


def _remote_env_entries(execution: ExecutionContext) -> list[str]:
    """The `.env*` entries in the target's data root, sorted.

    Filtered in Python rather than with a shell glob: an unmatched `.env*`
    comes back as the literal pattern, which would then be offered as a
    filename that does not exist. One `ls`, over the same connection the
    `env`-source credentials probe just used (`_fix_credentials`'s gate means
    this only runs for that source) - through `ssh_command` and
    `context.runner.run`, never `subprocess` (`transport/runner.py`). A
    listing that fails for any reason is an empty list rather than a second
    error: the question still has its opt-out, and the failure that matters
    is already on screen as the red row that led here.
    """
    result = execution.runner.run(
        ssh_command(execution.target, ["ls", "-1a", execution.target.data_root])
    )
    if not result.ok:
        return []
    return sorted(
        line.strip() for line in result.stdout.splitlines() if line.strip().startswith(".env")
    )


def _no_terminal() -> DdevSyncError:
    return DdevSyncError(
        "'ddev sync init' asks its questions in a terminal, and none is attached, so "
        "there is nothing to ask and nothing was written. Run it from an interactive "
        f"shell, or add the target to {PROJECT_LABEL} by hand: the file the add-on "
        "installed has a commented example."
    )
