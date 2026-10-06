"""Read the DDEV project this tool is running inside.

The project type comes from `.ddev/config.yaml` and nothing else. The tool it
replaces guessed the type from substrings in the project name, which broke on
every project named by a human rather than a convention.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ddev_sync.errors import ConfigError

DEFAULT_PROJECT_ROOT = Path("/var/www/html")


@dataclass(frozen=True)
class DdevProject:
    """The surrounding DDEV project, as declared in `.ddev/config.yaml`."""

    name: str
    type: str
    docroot: str
    root: Path

    @property
    def db_container(self) -> str:
        """The database container's hostname on the DDEV network."""
        return f"ddev-{self.name}-db"

    @property
    def web_container(self) -> str:
        """The web container's name on the DDEV network.

        Addressed by name rather than by id because `docker exec` accepts
        either and DDEV's naming is stable (`ddev-<project>-web`), while a
        container id changes on every `ddev restart`.
        """
        return f"ddev-{self.name}-web"

    @property
    def docroot_path(self) -> Path:
        """Absolute path to the web root. Equals `root` when docroot is empty."""
        return self.root / self.docroot if self.docroot else self.root


def read_ddev_project(project_root: Path) -> DdevProject:
    """Read `.ddev/config.yaml` under `project_root`, canonicalising the root.

    `root` is compared lexically against other paths - `transport/container.py`
    maps a workdir onto the container's mount point with `Path.relative_to`,
    and the paths it compares come from `operations/base.py::resolve_local_path`,
    which always returns a resolved path. A relative or symlinked
    `--project-root` would therefore make a directory inside the project look
    like one outside it. Resolving here, the one place a `DdevProject` is
    built, is what keeps the two sides comparable.
    """
    canonical_root = project_root.resolve()
    config_path = canonical_root / ".ddev" / "config.yaml"
    if not config_path.is_file():
        raise ConfigError(
            f"{config_path} not found - this does not look like a DDEV project. "
            "Run ddev-sync from inside one."
        )
    document = _read_mapping(config_path)
    missing = [key for key in ("name", "type") if key not in document]
    if missing:
        raise ConfigError(
            f".ddev/config.yaml is missing {', '.join(missing)}; the file looks incomplete"
        )
    return DdevProject(
        name=str(document["name"]),
        type=str(document["type"]),
        docroot=str(document.get("docroot", "")),
        root=canonical_root,
    )


def _read_mapping(config_path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f".ddev/config.yaml could not be parsed: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(".ddev/config.yaml must contain a mapping at the top level")
    return raw
