"""Load feature files from a directory.

Deliberately shaped like `profiles/registry.py` — same error phrasing, same
YAML handling — so that knowing one means knowing the other.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ddev_sync.errors import FeatureError
from ddev_sync.features.schema import CONTRIBUTION_KEYS, validate_feature
from ddev_sync.paths import existing_or_fallback
from ddev_sync.yamlfile import load_yaml

INSTALLED_FEATURE_ROOT = Path("/opt/ddev-sync/features")
# See `profiles/registry.py`'s identical pair of constants for the full
# reasoning (same shape deliberately - this module's own docstring already
# says knowing one of this pair means knowing the other). `parents[3]` from
# this file (src/ddev_sync/features/registry.py) is the repository root.
DEV_CHECKOUT_FEATURE_ROOT = Path(__file__).resolve().parents[3] / "features"
DEFAULT_FEATURE_ROOT = existing_or_fallback(INSTALLED_FEATURE_ROOT, DEV_CHECKOUT_FEATURE_ROOT)

_FILENAME = re.compile(r"^(?P<name>[a-z0-9_-]+)\.yaml$")


@dataclass(frozen=True)
class SettingSpec:
    """One knob a feature exposes, with its default."""

    default: str
    description: str = ""


@dataclass(frozen=True)
class Feature:
    """A feature file, with its metadata split from its contribution."""

    name: str
    description: str = ""
    applies_to: tuple[str, ...] = ()
    detect: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, SettingSpec] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


class FeatureRegistry:
    """Reads feature YAML from a directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def available(self) -> list[str]:
        """Every feature name, sorted."""
        return sorted(
            match.group("name")
            for path in self._root.glob("*.yaml")
            if (match := _FILENAME.match(path.name)) is not None
        )

    def load(self, name: str) -> Feature:
        """Load and validate one feature by name."""
        path = self._root / f"{name}.yaml"
        if not path.is_file():
            known = ", ".join(self.available()) or "none"
            raise FeatureError(f"unknown feature {name!r}; available features: {known}")
        data = self._read(path)
        validate_feature(data)
        declared = str(data["name"])
        if declared != name:
            raise FeatureError(
                f"{path.name} declares name {declared!r}, which does not match "
                f"the filename; a feature is activated by its filename, so the "
                f"two must agree"
            )
        return Feature(
            name=declared,
            description=str(data.get("description", "")),
            applies_to=tuple(data.get("applies_to", ())),
            detect=dict(data.get("detect", {})),
            settings={
                key: SettingSpec(
                    default=str(spec["default"]),
                    description=str(spec.get("description", "")),
                )
                for key, spec in data.get("settings", {}).items()
            },
            data={key: data[key] for key in CONTRIBUTION_KEYS if key in data},
        )

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            raw = load_yaml(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise FeatureError(f"{path.name}: could not be parsed as YAML: {error}") from error
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise FeatureError(f"{path.name}: must contain a mapping at the top level")
        return raw
