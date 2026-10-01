"""Decide which features are active, and turn them into merge layers.

Two placeholder namespaces are expanded at two different times, and keeping
them straight matters. A feature's own settings and the derived ${PM} /
${PM_RUN} are expanded HERE, because this is the only place a feature's
settings exist. ${DDEV_SITENAME} and ${SYNC_TARGET_DATABASE} are expanded
LATER by operations/placeholders.py, which needs an ExecutionContext. The two
compose because that expander leaves unknown placeholders alone, so a
${DDEV_SITENAME} passes untouched through this pass.

An explicit `features:` key REPLACES detection rather than adding to it: a
team that lists features is stating the whole set. If adding-to turns out to
be wanted, that is a separate `features_extra:` key, not a quiet merge.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ddev_sync.config.merge import LayerDoc
from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import FeatureError
from ddev_sync.features.activation import Request, resolve_settings
from ddev_sync.features.detect import matches
from ddev_sync.features.package_manager import detect_package_manager, require_package_manager
from ddev_sync.features.registry import Feature, FeatureRegistry


@dataclass(frozen=True)
class ActivatedFeature:
    """One active feature, its resolved settings, and why it is active."""

    feature: Feature
    settings: dict[str, str]
    reason: str


@dataclass(frozen=True)
class FeatureResolution:
    """Everything the rest of the tool needs to know about features."""

    activated: tuple[ActivatedFeature, ...] = ()
    layers: tuple[LayerDoc, ...] = ()
    detection_enabled: bool = True


def resolve_features(
    registry: FeatureRegistry,
    project: DdevProject,
    profile_name: str,
    requests: Sequence[Request] | None,
    detect_enabled: bool,
) -> FeatureResolution:
    """Resolve the active feature set.

    `requests` is None when no `features:` key was written, which is the cue
    to detect. An empty sequence means "none, deliberately".
    """
    candidates: list[tuple[ActivatedFeature, bool]]
    if requests is not None:
        candidates = [
            (_from_request(registry, profile_name, request), True) for request in requests
        ]
    elif detect_enabled:
        candidates = [(entry, False) for entry in _detected(registry, project, profile_name)]
    else:
        candidates = []

    activated: list[ActivatedFeature] = []
    layers: list[LayerDoc] = []
    for entry, explicit in candidates:
        layer = _layer(entry, project, explicit=explicit)
        if layer is None:
            continue
        activated.append(entry)
        layers.append(layer)

    return FeatureResolution(
        activated=tuple(activated),
        layers=tuple(layers),
        detection_enabled=detect_enabled,
    )


def _from_request(
    registry: FeatureRegistry, profile_name: str, request: Request
) -> ActivatedFeature:
    feature = registry.load(request.name)
    if feature.applies_to and profile_name not in feature.applies_to:
        raise FeatureError(
            f"{request.source}: feature {feature.name!r} applies only to "
            f"{', '.join(feature.applies_to)}, but this project's profile is "
            f"{profile_name!r}"
        )
    return ActivatedFeature(
        feature=feature,
        settings=resolve_settings(feature, request),
        reason=f"from {request.source}",
    )


def _detected(
    registry: FeatureRegistry, project: DdevProject, profile_name: str
) -> list[ActivatedFeature]:
    """Probe every in-scope feature that declares a detect block.

    Out-of-scope features are filtered before probing rather than probed and
    discarded: `filament` should never be looked for on a TYPO3 project. This
    is deliberately asymmetric with `_from_request`, which raises when the
    user explicitly asks for an out-of-scope feature - an explicit request
    names something impossible and deserves to be told, while detection
    simply never considers a candidate that could not apply, silently.

    The same rule governs the package manager gate in `_layer`: a detected
    feature that needs `${PM}` but finds no lockfile is dropped exactly like
    an out-of-scope detected feature, while an explicitly requested one
    raises. One principle, two applications - `applies_to` and the package
    manager - so do not read the duplication as two unrelated special cases.

    `registry.available()` already returns sorted names, which is where the
    alphabetical ordering of detected features comes from; there is no
    separate sort here so there is exactly one source of the order.
    """
    activated: list[ActivatedFeature] = []
    for name in registry.available():
        feature = registry.load(name)
        if not feature.detect:
            continue
        if feature.applies_to and profile_name not in feature.applies_to:
            continue
        if not matches(feature.detect, project):
            continue
        activated.append(
            ActivatedFeature(
                feature=feature,
                settings=resolve_settings(feature, Request(name, {}, "detection")),
                reason=f"detected: {_describe(feature.detect)}",
            )
        )
    return activated


def _describe(detect: dict[str, Any]) -> str:
    """A short human-readable rendering of what matched."""
    key, value = next(iter(detect.items()))
    if key in {"any_of", "all_of"}:
        return key
    return str(value)


def _layer(entry: ActivatedFeature, project: DdevProject, *, explicit: bool) -> LayerDoc | None:
    """Expand a feature's contribution into a merge layer, or None to drop it.

    `explicit` carries the activation's origin down from `resolve_features`,
    which is the only place that still knows whether this entry came from a
    request or from detection by the time the package manager is resolved.
    The asymmetry mirrors `applies_to`: an explicit request that needs ${PM}
    but finds no lockfile is an instruction that cannot be fulfilled, so it
    raises and names every lockfile searched. A detected feature in the same
    situation is only a guess - package.json without a lockfile is an
    ordinary state (a fresh checkout before `npm install`, a repo that
    gitignores its lockfile) - so it is silently not a candidate, exactly
    like a detected feature that fails `applies_to`.
    """
    substitutions = {f"${{{key}}}": value for key, value in entry.settings.items()}
    if _uses_package_manager(entry.feature.data):
        manager = detect_package_manager(project)
        if manager is None:
            if explicit:
                require_package_manager(project)  # raises, naming every lockfile
            return None
        substitutions["${PM}"] = manager.name
        substitutions["${PM_RUN}"] = manager.run
    return LayerDoc(
        label=f"feature:{entry.feature.name}",
        data=_expand(entry.feature.data, substitutions),
    )


def _uses_package_manager(value: Any) -> bool:
    """Walk the structure looking for a literal ${PM} or ${PM_RUN} string.

    Walking properly (rather than `"${PM}" in repr(data)`) avoids a false
    positive on a feature that merely mentions the string in, say, a
    description - `repr` would match that and then fail loudly with a
    confusing "no lockfile found" error for a feature that never uses the
    package manager at all. It reuses `_expand`'s own traversal shape rather
    than writing a third one.
    """
    if isinstance(value, str):
        return "${PM}" in value or "${PM_RUN}" in value
    if isinstance(value, dict):
        return any(_uses_package_manager(item) for item in value.values())
    if isinstance(value, list):
        return any(_uses_package_manager(item) for item in value)
    return False


def _expand(value: Any, substitutions: dict[str, str]) -> Any:
    """Substitute recursively, leaving unknown placeholders for the next pass."""
    if isinstance(value, str):
        for placeholder, replacement in substitutions.items():
            value = value.replace(placeholder, replacement)
        return value
    if isinstance(value, dict):
        return {key: _expand(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, substitutions) for item in value]
    return value
