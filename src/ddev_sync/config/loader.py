"""Find, read and validate the three user-editable configuration layers."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ddev_sync.config.merge import LayerDoc
from ddev_sync.errors import ConfigError
from ddev_sync.yamlfile import load_yaml

SUPPORTED_CONFIG_VERSION = 1

GLOBAL_LABEL = "~/.ddev/ddev-sync/config.yaml"
PROJECT_LABEL = ".ddev/ddev-sync.yaml"
LOCAL_LABEL = ".ddev/ddev-sync/local.yaml"
LEGACY_LOCAL_LABEL = ".ddev/ddev-sync.local.yaml"


@dataclass(frozen=True)
class ConfigPaths:
    """Where the user-editable layers live for one project.

    There are two local paths, not two local layers: `local_config` is where
    the local layer lives now, inside the directory the add-on ships a
    `.gitignore` for, and `legacy_local_config` is where it used to live, one
    level up, where nothing ignored it. Whichever exists supplies the same
    single layer, in the same position.
    """

    global_config: Path
    project_config: Path
    local_config: Path
    legacy_local_config: Path


def discover_config_paths(project_root: Path, home: Path) -> ConfigPaths:
    """Locate the user layers. Existence is not checked here."""
    return ConfigPaths(
        global_config=home / ".ddev" / "ddev-sync" / "config.yaml",
        project_config=project_root / ".ddev" / "ddev-sync.yaml",
        local_config=project_root / ".ddev" / "ddev-sync" / "local.yaml",
        legacy_local_config=project_root / ".ddev" / "ddev-sync.local.yaml",
    )


def load_user_layers(paths: ConfigPaths) -> list[LayerDoc]:
    """Read the user layers that exist, weakest first."""
    local, local_label = _local_layer(paths)
    candidates = (
        (paths.global_config, GLOBAL_LABEL),
        (paths.project_config, PROJECT_LABEL),
        (local, local_label),
    )
    return [
        LayerDoc(label=label, data=_read_config_file(path, label))
        for path, label in candidates
        if path.is_file()
    ]


def _local_layer(paths: ConfigPaths) -> tuple[Path, str]:
    """Which file supplies the local layer - the one that exists, or the new
    path by default if neither does.

    Always a concrete path, never `None`: the "neither exists" case needs no
    branch of its own here because `load_user_layers` already filters
    candidates with `is_file()`.

    Refusing when both exist rather than ranking them: this file carries
    credentials, and silently reading one copy while the user edits the other
    is the kind of failure that looks like the password being wrong. The
    engine already refuses to guess when two layers define the same target
    (`cli/init.py`), and this is that rule with more at stake.
    """
    new = paths.local_config.is_file()
    old = paths.legacy_local_config.is_file()
    if new and old:
        raise ConfigError(
            f"both {LOCAL_LABEL} and {LEGACY_LOCAL_LABEL} exist, and only one of them "
            f"can be the local layer. Move what you need into {LOCAL_LABEL} - the "
            "add-on ignores that path for you - and delete the other."
        )
    if old:
        return paths.legacy_local_config, LEGACY_LOCAL_LABEL
    return paths.local_config, LOCAL_LABEL


def _read_config_file(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = load_yaml(path.read_text(encoding="utf-8"))
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
