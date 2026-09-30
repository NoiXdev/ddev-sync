"""Assemble everything a run needs from the filesystem and the flags."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddev_sync.config.detect import resolve_profile_name, resolve_version
from ddev_sync.config.loader import discover_config_paths, load_user_layers
from ddev_sync.config.merge import LayerDoc, MergeResult, merge_layers
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.config.targets import collect_targets, list_target_names, resolve_target
from ddev_sync.ddev.project import DdevProject, read_ddev_project
from ddev_sync.errors import ConfigError, TargetError
from ddev_sync.profiles.registry import ProfileRegistry, ResolvedProfile


@dataclass(frozen=True)
class RunContext:
    """Everything resolved and ready, before any step is planned."""

    project: DdevProject
    profile: ResolvedProfile
    target: ResolvedTarget
    merged: MergeResult
    notices: tuple[str, ...] = ()


def build_context(
    *,
    project_root: Path,
    home: Path,
    target_name: str | None,
    type_flag: str | None,
    framework_version_flag: str | None,
    profile_root: Path,
) -> RunContext:
    """Read config, detect type and version, resolve profile and target."""
    project = read_ddev_project(project_root)
    user_layers = load_user_layers(discover_config_paths(project_root, home))
    user_merged = merge_layers(user_layers)
    configured = _as_mapping(user_merged.data.get("project"), "project")

    profile_resolution = resolve_profile_name(
        type_flag, _text(configured.get("type")), project.type
    )
    # `resolve_profile_name` only returns `None` when its `ddev_type` argument
    # is `None`. `project.type` is a required field on `DdevProject`
    # (`read_ddev_project` already raised `ConfigError` if `.ddev/config.yaml`
    # lacked it), so it is never `None` here and neither is this result. The
    # assert documents that invariant for the type checker instead of writing
    # an untestable `DetectionError` branch that this call site can never
    # reach - `DetectionError` remains reserved for detectors that genuinely
    # can return "unknown" (see `config/detect.py`).
    assert profile_resolution is not None

    version_resolution = resolve_version(
        framework_version_flag, _text(configured.get("version")), project, profile_resolution.value
    )
    major = None if version_resolution is None else version_resolution.value

    registry = ProfileRegistry(profile_root)
    profile = registry.resolve(profile_resolution.value, major)

    merged = merge_layers([*profile.layers, *user_layers])
    target = resolve_target(
        _pick_target_name(target_name, user_layers), collect_targets(user_layers)
    )

    notices = () if profile.notice is None else (profile.notice,)
    return RunContext(
        project=project, profile=profile, target=target, merged=merged, notices=notices
    )


def _as_mapping(raw: object, label: str) -> dict[str, Any]:
    """An empty `project:` block is a legitimate "nothing to override" -
    treat it as such rather than crashing; anything else that is not a
    mapping is a genuine configuration mistake and is named explicitly.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{label!r} must be a mapping with optional 'type' and 'version'")
    return raw


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _pick_target_name(explicit: str | None, user_layers: list[LayerDoc]) -> str:
    if explicit is not None:
        return explicit
    names = list_target_names(collect_targets(user_layers))
    if not names:
        raise TargetError("no sync targets are defined; run 'ddev sync init' to create one")
    if len(names) == 1:
        return names[0]
    raise TargetError(f"several targets are defined ({', '.join(names)}); choose one with --target")
