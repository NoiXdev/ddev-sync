"""Resolve which profile and which framework version apply to this project.

Both resolutions follow the same shape - explicit flag, then configuration,
then detection, then ask - and both report where the answer came from, because
a tool that silently guesses is a tool nobody trusts with a production dump.
"""

from dataclasses import dataclass

GENERIC_PROFILE = "generic"

PROFILE_FOR_DDEV_TYPE: dict[str, str] = {
    "typo3": "typo3",
    "laravel": "laravel",
    "wordpress": "wordpress",
    "symfony": "symfony",
    "shopware6": "shopware6",
    "drupal": "drupal",
}


@dataclass(frozen=True)
class Resolution:
    """A resolved value plus the human-readable place it came from."""

    value: str
    source: str


def resolve_profile_name(
    flag: str | None, configured: str | None, ddev_type: str | None
) -> Resolution | None:
    """Pick the profile name. Returns None when nothing can decide."""
    if flag is not None:
        return Resolution(value=flag, source="--type flag")
    if configured is not None:
        return Resolution(value=configured, source=".ddev/ddev-sync.yaml")
    if ddev_type is None:
        return None
    mapped = PROFILE_FOR_DDEV_TYPE.get(ddev_type)
    if mapped is not None:
        return Resolution(value=mapped, source=".ddev/config.yaml")
    return Resolution(
        value=GENERIC_PROFILE,
        source=f"generic fallback (no profile for DDEV type {ddev_type!r})",
    )
