"""Validate a feature file before anything acts on it.

Contribution blocks are validated by reusing the profile validators rather
than restating them: a feature's `files.paths` is the same shape as a
profile's, and two implementations of one rule drift.
"""

from typing import Any

from ddev_sync.errors import FeatureError
from ddev_sync.features import detect
from ddev_sync.profiles import schema as profile_schema

FEATURE_TOP_LEVEL = frozenset(
    {
        "name",
        "description",
        "applies_to",
        "detect",
        "settings",
        "env",
        "database",
        "files",
        "sanitize",
        "setup",
        "hooks",
    }
)

CONTRIBUTION_KEYS = frozenset({"env", "database", "files", "sanitize", "setup", "hooks"})


def validate_feature(data: dict[str, Any]) -> None:
    """Raise `FeatureError` unless `data` is a well-formed feature."""
    name = data.get("name")
    if not name:
        raise FeatureError("a feature needs a 'name'")

    unknown = sorted(set(data) - FEATURE_TOP_LEVEL)
    if unknown:
        raise FeatureError(
            f"feature {name!r}: unknown key(s) {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(FEATURE_TOP_LEVEL))}"
        )

    _validate_applies_to(str(name), data.get("applies_to"))
    _validate_detect(str(name), data.get("detect"))
    _validate_settings(str(name), data.get("settings"))
    _validate_contribution(str(name), data)


def _validate_applies_to(name: str, applies_to: Any) -> None:
    if applies_to is None:
        return
    if not isinstance(applies_to, list) or not all(isinstance(entry, str) for entry in applies_to):
        raise FeatureError(f"feature {name!r}: 'applies_to' must be a list of profile names")


def _validate_detect(name: str, detect_block: Any) -> None:
    if detect_block is None:
        return
    detect.validate_predicate(name, detect_block)


def _validate_settings(name: str, settings: Any) -> None:
    if settings is None:
        return
    if not isinstance(settings, dict):
        raise FeatureError(f"feature {name!r}: 'settings' must be a mapping")
    for key, spec in settings.items():
        if not isinstance(spec, dict) or "default" not in spec:
            raise FeatureError(
                f"feature {name!r}: setting {key!r} must be a mapping with a 'default'"
            )


def _validate_contribution(name: str, data: dict[str, Any]) -> None:
    """Hold a feature's contribution to the same rules as a profile's.

    `validate_profile` requires a `name` and rejects keys it does not know,
    so it is handed a synthetic profile carrying only the contribution.
    """
    contribution = {key: data[key] for key in CONTRIBUTION_KEYS if key in data}
    if not contribution:
        return
    try:
        profile_schema.validate_profile({"name": name, **contribution})
    except Exception as error:  # the profile validator raises ProfileError
        raise FeatureError(f"feature {name!r}: {error}") from error
