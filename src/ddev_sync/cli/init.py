"""`ddev sync init`: ask for one target, write it, then check it.

The order of the questions is not the order a person would list the answers
in. The name and the file come before the connection because they are all
that is needed to know whether the target already exists, and a user who
declines to replace one should lose two answers, not six.

Every question returns `None` when there is no terminal (see `cli/prompts.py`),
and that is turned into an error here, the moment it happens, rather than
guessed past. There is no flag that supplies an answer instead: a wizard whose
answers all come from the command line would be a configuration file written
twice, so the error names the file to edit.
"""

from collections.abc import Callable
from pathlib import Path

from ddev_sync.cli.commands import doctor_command
from ddev_sync.cli.context import build_context, detect_project
from ddev_sync.cli.prompts import (
    DEPLOYER_SOURCE,
    ask_connection,
    ask_stage,
    ask_target_name,
    choose_connection_source,
    choose_layer,
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
from ddev_sync.config.merge import merge_layers
from ddev_sync.config.targets import CollectedTarget, collect_targets
from ddev_sync.config.writer import WriteRefused, render_target, write_target
from ddev_sync.ddev.project import DdevProject, read_ddev_project
from ddev_sync.errors import DdevSyncError
from ddev_sync.transport.runner import CommandRunner

DEFAULT_NAME = "production"

# How many times the name and file are asked for when the target already
# exists and the user will not replace it. The first answer is one of the
# three. A decline is an ordinary thing to do - the name was taken, so a
# different name or the other file is wanted - and the second question already
# offers a free name, so a second round nearly always settles it. A third
# covers a user who is still choosing. After that the loop is more likely to be
# a user who cannot leave than one who is choosing, and `Ctrl-C` is the only
# exit the prompt itself gives, so the command ends with what it did and what
# to do instead.
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
    project = read_ddev_project(project_root)
    paths = discover_config_paths(project_root, home)

    _confirm_detection(project, paths, type_flag, framework_version_flag)
    name, label, path = _ask_destination(paths, echo)

    source = choose_connection_source(deployer_available=_has_deployer_recipe(project.root))
    if source is None:
        raise _no_terminal()
    if source == DEPLOYER_SOURCE:
        raise DdevSyncError(
            "importing a target from Deployer is not available yet; "
            "run 'ddev sync init' again and enter the connection by hand"
        )

    connection = ask_connection()
    if connection is None:
        raise _no_terminal()
    stage = ask_stage()
    if stage is None:
        raise _no_terminal()

    block = render_target(name, {**connection, "stage": stage})
    try:
        write_target(path=path, paths=paths, name=name, block=block)
    except WriteRefused as refusal:
        echo(str(refusal))
        echo(f"Add the target to {label} yourself, under `targets:`:\n\n{block}")
        return 1

    # The file is on disk by now, but "wrote" is only said once the outcome is
    # known: a success line followed by an error would call a target that does
    # not resolve a success. Whichever way it goes, the user is told which
    # file holds the target.
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
        )
        code = doctor_command(context, echo, runner=runner, colour=colour)
    except DdevSyncError as error:
        raise DdevSyncError(
            f"the target {name!r} is in {label}, but it could not be checked: {error}. "
            f"Edit it there, then check it with 'ddev sync --target {name} doctor'."
        ) from error
    if code == 0:
        echo(f"Wrote the target {name!r} to {label}.")
    else:
        echo(
            f"The target stays in {label}. Edit it there, then check it again with "
            f"'ddev sync --target {name} doctor'."
        )
    return code


def _confirm_detection(
    project: DdevProject,
    paths: ConfigPaths,
    type_flag: str | None,
    framework_version_flag: str | None,
) -> None:
    """Ask whether the detected profile is right; refuse to go on if it is not.

    It comes first because everything written afterwards is checked against
    the profile this resolves to.
    """
    merged = merge_layers(load_user_layers(paths))
    detection = detect_project(project, merged, type_flag, framework_version_flag)
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


def _ask_destination(paths: ConfigPaths, echo: Callable[[str], None]) -> tuple[str, str, Path]:
    """Ask for the name and the file until they name a target that is free or replaceable."""
    layer_paths = {GLOBAL_LABEL: paths.global_config, PROJECT_LABEL: paths.project_config}
    default = DEFAULT_NAME
    for _ in range(MAX_ATTEMPTS):
        answer = ask_target_name(default)
        if answer is None:
            raise _no_terminal()
        name = answer.strip()
        echo(
            f"{GLOBAL_LABEL} is not part of the repository, so it keeps hosts and users "
            f"out of it; {PROJECT_LABEL} is shared with everyone who clones the project."
        )
        label = choose_layer()
        if label is None:
            raise _no_terminal()
        taken = _targets_in(paths, label)
        if name not in taken:
            return name, label, layer_paths[label]
        replace = confirm_replacement(name, label, describe_endpoint(taken[name].spec))
        if replace is None:
            raise _no_terminal()
        if replace:
            return name, label, layer_paths[label]
        echo(f"Nothing was replaced. Choose another name, or another file than {label}.")
        default = _free_name(name, taken)
    raise DdevSyncError(
        f"nothing was written: a target of the name you gave already exists, and you did "
        f"not want it replaced, {MAX_ATTEMPTS} times. Run 'ddev sync init' again, or edit "
        f"{PROJECT_LABEL} or {GLOBAL_LABEL} yourself."
    )


def _targets_in(paths: ConfigPaths, label: str) -> dict[str, CollectedTarget]:
    """The targets one file defines by itself, not what several files merge to.

    A target that only another file defines is not what the writer would
    replace, so asking about it would be asking the wrong question.
    """
    own = [layer for layer in load_user_layers(paths) if layer.label == label]
    return collect_targets(own)


def _free_name(name: str, taken: dict[str, CollectedTarget]) -> str:
    """`name` with the first number after it that the file does not use yet."""
    number = 2
    while f"{name}-{number}" in taken:
        number += 1
    return f"{name}-{number}"


def _has_deployer_recipe(root: Path) -> bool:
    return any((root / recipe).is_file() for recipe in _DEPLOYER_RECIPES)


def _no_terminal() -> DdevSyncError:
    return DdevSyncError(
        "'ddev sync init' asks its questions in a terminal, and none is attached, so "
        "there is nothing to ask and nothing was written. Run it from an interactive "
        f"shell, or add the target to {PROJECT_LABEL} by hand: the file the add-on "
        "installed has a commented example."
    )
