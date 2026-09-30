"""Load built-in profiles and resolve the right version variant.

A profile is a base document plus optional `@major` deltas. The deltas use the
same extend/replace/prepend list semantics as user configuration, so there is
one merge mechanism in the whole tool rather than two that drift apart.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.errors import ProfileError

DEFAULT_PROFILE_ROOT = Path("/opt/ddev-sync/profiles")

_VARIANT_FILENAME = re.compile(r"^(?P<name>[a-z0-9_-]+)@(?P<major>\d+)\.yaml$")
_BASE_FILENAME = re.compile(r"^(?P<name>[a-z0-9_-]+)\.yaml$")


@dataclass(frozen=True)
class ResolvedProfile:
    """A profile after its version variant has been applied."""

    name: str
    label: str
    data: dict[str, Any]
    layers: tuple[LayerDoc, ...]
    notice: str | None = None


class ProfileRegistry:
    """Reads profile YAML from a directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def available_profiles(self) -> list[str]:
        """Base profile names, sorted. Variants are not listed separately."""
        names = {
            match.group("name")
            for path in self._root.glob("*.yaml")
            if (match := _BASE_FILENAME.match(path.name)) is not None
        }
        return sorted(names)

    def available_majors(self, name: str) -> tuple[int, ...]:
        """The major versions that have a variant file, ascending."""
        majors = sorted(
            int(match.group("major"))
            for path in self._root.glob(f"{name}@*.yaml")
            if (match := _VARIANT_FILENAME.match(path.name)) is not None
        )
        return tuple(majors)

    def resolve(self, name: str, major: str | None) -> ResolvedProfile:
        """Load `name`, apply the best matching variant for `major`."""
        base_path = self._root / f"{name}.yaml"
        if not base_path.is_file():
            known = ", ".join(self.available_profiles()) or "none"
            raise ProfileError(f"unknown profile {name!r}; available profiles: {known}")

        layers = [LayerDoc(label=f"profile:{name}", data=self._read(base_path))]
        chosen, notice = self._choose_major(name, major)

        if chosen is not None:
            variant_path = self._root / f"{name}@{chosen}.yaml"
            layers.append(LayerDoc(label=f"profile:{name}@{chosen}", data=self._read(variant_path)))

        merged = merge_layers(layers)
        merged.data.pop("extends", None)
        return ResolvedProfile(
            name=name,
            label=f"{name}@{chosen}" if chosen is not None else name,
            data=merged.data,
            layers=tuple(layers),
            notice=notice,
        )

    def _choose_major(self, name: str, major: str | None) -> tuple[int | None, str | None]:
        if major is None:
            return None, None
        available = self.available_majors(name)
        if not available:
            return None, None
        try:
            wanted = int(major)
        except ValueError:
            raise ProfileError(
                f"version {major!r} is not a major version number; "
                f"{name} has variants for {', '.join(str(entry) for entry in available)}"
            ) from None
        if wanted in available:
            return wanted, None
        lower = [entry for entry in available if entry < wanted]
        if lower:
            chosen = max(lower)
            return chosen, (
                f"{name} {wanted} detected - no {name}@{wanted} profile, using {name}@{chosen}"
            )
        return None, (
            f"{name} {wanted} detected - older than every variant "
            f"({', '.join(str(entry) for entry in available)}), using the {name} base profile"
        )

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ProfileError(f"{path.name}: could not be parsed as YAML: {error}") from error
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ProfileError(f"{path.name}: must contain a mapping at the top level")
        return raw
