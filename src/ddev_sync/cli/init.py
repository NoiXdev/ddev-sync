"""`ddev sync init`: ask for one target, write it, then check it.

The order of the questions is not the order a person would list the answers
in. The name comes before the connection because it is all that is needed to
know whether the target already exists, and a user who declines to replace one
should lose one answer, not five.

Only `.ddev/ddev-sync.yaml` is ever written. The global file is mounted
read-only in the sidecar on purpose (`docker-compose.ddev-sync.yaml`), and the
sidecar is where this command normally runs, so offering it would end in a
permission error. The advice the shipped template gives about keeping hosts out
of the repository is said after the write instead.

Every question returns `None` when there is no terminal (see `cli/prompts.py`),
and that is turned into an error here, the moment it happens, rather than
guessed past. There is no flag that supplies an answer instead: a wizard whose
answers all come from the command line would be a configuration file written
twice, so the error names the file to edit.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ddev_sync.cli.commands import doctor_command
from ddev_sync.cli.context import Detection, ProjectResolution, build_context, resolve_project
from ddev_sync.cli.prompts import (
    DEPLOYER_SOURCE,
    ENV_FILE_NONE,
    ask_connection,
    ask_production_url,
    ask_stage,
    ask_target_name,
    choose_connection_source,
    choose_deployer_host,
    choose_env_file,
    confirm_detection,
    confirm_replacement,
)
from ddev_sync.cli.render import describe_endpoint
from ddev_sync.config.loader import (
    GLOBAL_LABEL,
    PROJECT_LABEL,
    ConfigPaths,
    discover_config_paths,
    load_user_layers,
)
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.config.targets import CollectedTarget, collect_targets, resolve_target
from ddev_sync.config.writer import WriteRefused, render_target, write_settings, write_target
from ddev_sync.errors import DdevSyncError, DeployerError
from ddev_sync.operations.placeholders import (
    PRODUCTION_URL_CONFIG_KEY,
    PRODUCTION_URL_PLACEHOLDER,
    validate_production_url,
)
from ddev_sync.transport.deployer import ProjectDeployer, make_deployer_resolver
from ddev_sync.transport.runner import CommandRunner

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
        context = build_context(
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
    return settings


def _missing_env_file(resolution: ProjectResolution) -> list[str] | None:
    """The configured candidates when none exists; `None` when there is no question.

    The two conditions for "there is no env step" are `engine/plan.py::_env`'s
    own, read from the same merged configuration, so this question appears
    exactly when that step would - and the file is matched the way
    `operations/env.py::_find_env_file` matches it, against real directory
    entries rather than a constructed path, because `is_file()` folds case on
    a case-insensitive filesystem.
    """
    node = resolution.merged.data.get("env", {})
    settings = node if isinstance(node, dict) else {}
    if str(settings.get("strategy", "patch_keys")) == "none":
        return None
    raw_values = settings.get("set", {})
    if not isinstance(raw_values, dict) or not raw_values:
        return None
    configured = settings.get("file", [".env.local", ".env"])
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


def _no_terminal() -> DdevSyncError:
    return DdevSyncError(
        "'ddev sync init' asks its questions in a terminal, and none is attached, so "
        "there is nothing to ask and nothing was written. Run it from an interactive "
        f"shell, or add the target to {PROJECT_LABEL} by hand: the file the add-on "
        "installed has a commented example."
    )
