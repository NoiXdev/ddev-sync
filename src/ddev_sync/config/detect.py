"""Resolve which profile and which framework version apply to this project.

Both resolutions follow the same shape - explicit flag, then configuration,
then detection, then ask - and both report where the answer came from, because
a tool that silently guesses is a tool nobody trusts with a production dump.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddev_sync.ddev.project import DdevProject

GENERIC_PROFILE = "generic"

PROFILE_FOR_DDEV_TYPE: dict[str, str] = {
    "typo3": "typo3",
    "laravel": "laravel",
    "wordpress": "wordpress",
    "symfony": "symfony",
    "shopware6": "shopware6",
    "drupal": "drupal",
}

COMPOSER_PACKAGE_FOR_PROFILE: dict[str, str] = {
    "typo3": "typo3/cms-core",
    "laravel": "laravel/framework",
    "symfony": "symfony/framework-bundle",
    "shopware6": "shopware/core",
    "drupal": "drupal/core",
}

WORDPRESS_PROFILE = "wordpress"

_SEMVER_MAJOR = re.compile(r"^v?(\d+)\.")
_WP_VERSION = re.compile(r"\$wp_version\s*=\s*'([0-9]+)\.")


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


def resolve_version(
    flag: str | None,
    configured: str | None,
    project: DdevProject,
    profile_name: str,
) -> Resolution | None:
    """Pick the framework major version. Returns None when nothing can decide."""
    if flag is not None:
        return Resolution(value=flag, source="--framework-version flag")
    if configured is not None:
        return Resolution(value=configured, source=".ddev/ddev-sync.yaml")
    detected = detect_major_version(project, profile_name)
    if detected is None:
        return None
    source = "wp-includes/version.php" if profile_name == WORDPRESS_PROFILE else "composer.lock"
    return Resolution(value=detected, source=source)


def detect_major_version(project: DdevProject, profile_name: str) -> str | None:
    """Detect the framework's major version, or None when it cannot be known."""
    if profile_name == WORDPRESS_PROFILE:
        return _major_from_wordpress(project)
    package = COMPOSER_PACKAGE_FOR_PROFILE.get(profile_name)
    if package is None:
        return None
    return _major_from_composer_lock(project.root / "composer.lock", package)


def _major_from_composer_lock(lock_path: Path, package: str) -> str | None:
    if not lock_path.is_file():
        return None
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    for section in ("packages", "packages-dev"):
        version = _find_version(document.get(section), package)
        if version is not None:
            return version
    return None


def _find_version(entries: Any, package: str) -> str | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == package:
            match = _SEMVER_MAJOR.match(str(entry.get("version", "")))
            return match.group(1) if match else None
    return None


def _major_from_wordpress(project: DdevProject) -> str | None:
    version_file = project.docroot_path / "wp-includes" / "version.php"
    try:
        text = version_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _WP_VERSION.search(text)
    return match.group(1) if match else None
