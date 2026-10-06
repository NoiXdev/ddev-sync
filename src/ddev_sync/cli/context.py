"""Assemble everything a run needs from the filesystem and the flags."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ddev_sync.config.detect import resolve_profile_name, resolve_version
from ddev_sync.config.loader import discover_config_paths, load_user_layers
from ddev_sync.config.merge import LayerDoc, MergeResult, merge_layers
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.config.targets import collect_targets, list_target_names, resolve_target
from ddev_sync.ddev.project import DdevProject, read_ddev_project
from ddev_sync.errors import AmbiguousTargetError, ConfigError
from ddev_sync.features.activation import DETECT_FEATURES_DEFAULT, Request, parse_feature_key
from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT, FeatureRegistry
from ddev_sync.features.resolver import FeatureResolution, resolve_features
from ddev_sync.profiles.registry import ProfileRegistry, ResolvedProfile
from ddev_sync.transport.deployer import make_deployer_resolver
from ddev_sync.transport.runner import CommandRunner


@dataclass(frozen=True)
class RunContext:
    """Everything resolved and ready, before any step is planned."""

    project: DdevProject
    profile: ResolvedProfile
    target: ResolvedTarget
    merged: MergeResult
    notices: tuple[str, ...] = ()
    features: FeatureResolution = field(default_factory=FeatureResolution)


def build_context(
    *,
    project_root: Path,
    home: Path,
    target_name: str | None,
    type_flag: str | None,
    framework_version_flag: str | None,
    profile_root: Path,
    feature_root: Path = DEFAULT_FEATURE_ROOT,
    features_flag: str | None = None,
    no_features: bool = False,
    runner: CommandRunner | None = None,
) -> RunContext:
    """Read config, detect type and version, resolve profile and target.

    `runner` is what lets a `deployer:` target ask the project's own Deployer
    where it deploys. It is optional because most runs never need it, and the
    resolver is lazy: `dep` only runs when a target actually has the block.
    """
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

    # Features need the profile's name (to filter `applies_to`), so they can
    # only be resolved once the profile itself is - and must happen before
    # the final merge, since their layers slot between the profile's and the
    # user's own configuration (spec §4, amended).
    feature_registry = FeatureRegistry(feature_root)
    detect_enabled, disabled_reason = _detect_enabled(user_merged.data, no_features=no_features)
    requests = _feature_requests(user_merged, features_flag)
    resolution = resolve_features(
        feature_registry, project, profile.name, requests, detect_enabled, disabled_reason
    )

    merged = merge_layers([*profile.layers, *resolution.layers, *user_layers])
    target = resolve_target(
        _pick_target_name(target_name, user_layers),
        collect_targets(user_layers),
        resolve_deployer=(None if runner is None else make_deployer_resolver(project, runner)),
    )

    notices = () if profile.notice is None else (profile.notice,)
    return RunContext(
        project=project,
        profile=profile,
        target=target,
        merged=merged,
        notices=notices,
        features=resolution,
    )


_DISABLED_BY_FLAG = "--no-features"
_DISABLED_BY_CONFIG = "~/.ddev/ddev-sync/config.yaml"


def _detect_enabled(user_data: dict[str, Any], *, no_features: bool) -> tuple[bool, str]:
    """`defaults.detect_features` in the merged user config, defaulting to
    `DETECT_FEATURES_DEFAULT`, then overridden by `--no-features` (spec §4,
    amended). `--no-features` always wins: it is the single run's own word,
    the most specific of the three levels this setting can come from.

    Returns `(enabled, reason)`: `reason` names whichever of the two kill
    switches would be responsible if detection ends up disabled, so the dry
    run can say so honestly (`render.py::render_features`) instead of always
    blaming the config file - which is what it did before this function told
    it which one actually fired, even for a run disabled by `--no-features`
    alone, a file that need not exist. `reason` is meaningless when `enabled`
    is True; it is still returned rather than `None` because the caller
    always has a concrete answer to "which one would it have been".
    """
    if no_features:
        return False, _DISABLED_BY_FLAG
    defaults = user_data.get("defaults")
    if not isinstance(defaults, dict):
        return DETECT_FEATURES_DEFAULT, _DISABLED_BY_CONFIG
    return bool(defaults.get("detect_features", DETECT_FEATURES_DEFAULT)), _DISABLED_BY_CONFIG


def _feature_requests(user_merged: MergeResult, features_flag: str | None) -> list[Request] | None:
    """`features:` in the merged user config, overridden by `--features`.

    `--features` is a flat, comma-separated list of names - it has no way to
    carry per-feature settings, unlike the YAML `features:` key - so it is
    turned into bare `Request`s directly rather than going through
    `parse_feature_key`, which exists to parse the richer YAML shapes.
    """
    if features_flag is not None:
        return [
            Request(name.strip(), {}, "--features")
            for name in features_flag.split(",")
            if name.strip()
        ]
    return parse_feature_key(user_merged.data.get("features"), _features_source(user_merged))


def _features_source(user_merged: MergeResult) -> str:
    """Name the layer that defined the top-level `features:` key.

    `features:` is either a bare list of names (tracked in `list_sources`) or
    a mapping of name to settings (tracked per leaf, under `scalar_sources`,
    because a mapping merges like ordinary nested data) - `parse_feature_key`
    accepts both shapes, so both are checked here. This mirrors
    `config/targets.py`'s `_strongest_source`, which faces the same two-shape
    problem for one target's definition.
    """
    list_labels = user_merged.list_sources.get(("features",))
    if list_labels:
        return list_labels[-1]
    for path, label in user_merged.scalar_sources.items():
        if path[:1] == ("features",):
            return label
    return "merged configuration"


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
    """The target to resolve, without ever prompting.

    Both failures are `AmbiguousTargetError` rather than a plain
    `TargetError` because they are the two - and the only two - that asking
    the user which target to use can answer. `cli/main.py`'s
    `_build_run_context` catches exactly this class to do that, so anything
    else raised from here would make it rebuild the whole context to reach
    the same error twice.
    """
    if explicit is not None:
        return explicit
    names = list_target_names(collect_targets(user_layers))
    if not names:
        raise AmbiguousTargetError(
            "no sync targets are defined; run 'ddev sync init' to create one"
        )
    if len(names) == 1:
        return names[0]
    raise AmbiguousTargetError(
        f"several targets are defined ({', '.join(names)}); choose one with --target"
    )
