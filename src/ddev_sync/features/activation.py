"""Turn a user's `features:` key, or the absence of one, into requests.

Three levels override one another, most specific last: a machine-wide
`defaults.detect_features: false`, a project's `features:` key, and a single
run's `--features` / `--no-features`. Each is more specific about who and
when than the one above it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ddev_sync.errors import FeatureError
from ddev_sync.features.registry import Feature

DETECT_FEATURES_DEFAULT = True


@dataclass(frozen=True)
class Request:
    """One asked-for feature, and where the ask came from."""

    name: str
    settings: Mapping[str, str]
    source: str


def parse_feature_key(value: Any, source: str) -> list[Request] | None:
    """Parse a `features:` value. None means the key was absent.

    An absent key and an empty list are deliberately different: absent means
    "use detection", `[]` means "none, and I mean it". Collapsing them with a
    single falsy check silently re-enables detection for someone who turned
    it off.
    """
    if value is None:
        return None
    if isinstance(value, list):
        if not all(isinstance(entry, str) for entry in value):
            raise FeatureError(f"{source}: a features list holds feature names, got {value!r}")
        return [Request(name, {}, source) for name in value]
    if isinstance(value, Mapping):
        return [_request(name, settings, source) for name, settings in value.items()]
    raise FeatureError(
        f"{source}: 'features' must be a list of names or a mapping of "
        f"name to settings, got {type(value).__name__}"
    )


def _request(name: Any, settings: Any, source: str) -> Request:
    if settings is None:
        settings = {}
    if not isinstance(settings, Mapping):
        raise FeatureError(
            f"{source}: settings for feature {name!r} must be a mapping, "
            f"got {type(settings).__name__}"
        )
    return Request(str(name), {str(k): str(v) for k, v in settings.items()}, source)


def resolve_settings(feature: Feature, request: Request) -> dict[str, str]:
    """Overlay a request's settings onto the feature's defaults."""
    unknown = sorted(set(request.settings) - set(feature.settings))
    if unknown:
        known = ", ".join(sorted(feature.settings)) or "none"
        raise FeatureError(
            f"{request.source}: feature {feature.name!r} has no setting(s) "
            f"{', '.join(unknown)}; it accepts: {known}"
        )
    resolved = {key: spec.default for key, spec in feature.settings.items()}
    resolved.update(request.settings)
    return resolved
