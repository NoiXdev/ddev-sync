"""Find, read and validate the three user-editable configuration layers."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ddev_sync.config.merge import LayerDoc
from ddev_sync.errors import ConfigError

SUPPORTED_CONFIG_VERSION = 1

GLOBAL_LABEL = "~/.ddev/ddev-sync/config.yaml"
PROJECT_LABEL = ".ddev/ddev-sync.yaml"
LOCAL_LABEL = ".ddev/ddev-sync.local.yaml"


@dataclass(frozen=True)
class ConfigPaths:
    """Where the three user-editable layers live for one project."""

    global_config: Path
    project_config: Path
    local_config: Path


def discover_config_paths(project_root: Path, home: Path) -> ConfigPaths:
    """Locate the user layers. Existence is not checked here."""
    return ConfigPaths(
        global_config=home / ".ddev" / "ddev-sync" / "config.yaml",
        project_config=project_root / ".ddev" / "ddev-sync.yaml",
        local_config=project_root / ".ddev" / "ddev-sync.local.yaml",
    )


def load_user_layers(paths: ConfigPaths) -> list[LayerDoc]:
    """Read the user layers that exist, weakest first."""
    candidates = (
        (paths.global_config, GLOBAL_LABEL),
        (paths.project_config, PROJECT_LABEL),
        (paths.local_config, LOCAL_LABEL),
    )
    return [
        LayerDoc(label=label, data=_read_config_file(path, label))
        for path, label in candidates
        if path.is_file()
    ]


def _read_config_file(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"{label}: could not be parsed as YAML: {error}") from error

    document = _as_mapping(raw, label)
    _check_version(document, label)
    return {key: value for key, value in document.items() if key != "version"}


def _as_mapping(raw: Any, label: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{label}: must contain a mapping at the top level")
    return raw


def _check_version(document: dict[str, Any], label: str) -> None:
    if not document:
        return
    if "version" not in document:
        raise ConfigError(f"{label}: missing the required key 'version: 1'")
    version = document["version"]
    if version != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(
            f"{label}: config version {version} is not supported; "
            f"this build understands version {SUPPORTED_CONFIG_VERSION}"
        )
