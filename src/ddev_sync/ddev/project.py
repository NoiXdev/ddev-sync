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
from ddev_sync.yamlfile import load_yaml

DEFAULT_PROJECT_ROOT = Path("/var/www/html")


@dataclass(frozen=True)
class DdevProject:
    """The surrounding DDEV project, as declared in `.ddev/config.yaml`."""

    name: str
    type: str
    docroot: str
    # Expected to be absolute and resolved: `transport/container.py` compares
    # it lexically against other paths. `read_ddev_project` guarantees this;
    # code that builds a `DdevProject` directly has to.
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
    and a workdir that came from `operations/base.py::resolve_local_path` (an
    explicit `workdir:` in a setup command) is a resolved path. A relative or
    symlinked `--project-root` would therefore make a directory inside the
    project look like one outside it. Resolving here, the one place a
    `DdevProject` is built, is what keeps the two sides comparable.
    """
    canonical_root = project_root.resolve()
    config_path = canonical_root / ".ddev" / "config.yaml"
    if not config_path.is_file():
        raise ConfigError(
            f"{config_path} not found - this does not look like a DDEV project. "
            "Run ddev-sync from inside one."
        )
    document = _read_mapping(config_path)
    # `document.get(key) is None` rather than `key not in document`: a key
    # written with no value (`name:`) parses to None, and `str(None)` is the
    # five-character string "None" - which would have become a project named
    # None, and a database container called `ddev-None-db`.
    missing = [key for key in ("name", "type") if document.get(key) is None]
    if missing:
        raise ConfigError(
            f".ddev/config.yaml is missing {', '.join(missing)}; the file looks incomplete"
        )
    raw_docroot = document.get("docroot")
    return DdevProject(
        name=str(document["name"]),
        type=str(document["type"]),
        # A docroot that is absent, or written with no value, is the project
        # root itself - the same thing DDEV means by `docroot: ""`.
        docroot=_validated_docroot("" if raw_docroot is None else str(raw_docroot)),
        root=canonical_root,
    )


def _validated_docroot(docroot: str) -> str:
    """Refuse a docroot that is not a relative path inside the project.

    `docroot` is joined onto `root` by `DdevProject.docroot_path`, and the
    result is handed to `transport/container.py`, which maps a workdir onto
    the web container's `/var/www/html` with `Path.relative_to`. The two bad
    shapes failed differently, and only one of them failed loudly - both
    measured against `web_command` with the root at `/var/www/html`:

    * an absolute docroot makes the join discard the root entirely
      (`Path("/a") / "/etc"` is `/etc`), which `relative_to` catches:
      `ValueError: /etc is not inside the project`.
    * a `..` segment raised nothing at all, because `relative_to` is lexical.
      `docroot: ../sibling` produced `docker exec -w /var/www/html/../sibling`
      and `public/../../etc` produced a `-w` the container resolves to `/etc`.
      Framework commands ran outside the mount, silently.

    So this refuses `..` even where it lands back inside the project: the rule
    is then one a reader can hold, and the silent case is the one that most
    needs a line number. `operations/placeholders.py` keeps its own check -
    it guards shell splicing, a different question, and it tests `".." in
    docroot` as a substring where this tests whole segments, so the two
    deliberately refuse different sets. A `DdevProject` built in code does not
    come through here either way.
    """
    if docroot.startswith("/") or ".." in Path(docroot).parts:
        raise ConfigError(
            f"docroot {docroot!r} in .ddev/config.yaml must be a relative path inside the "
            "project, with no '..' segment: an absolute path replaces the project root "
            "instead of being joined to it, and a `..` segment can climb out of the only "
            "directory the web container has mounted"
        )
    return docroot


def _read_mapping(config_path: Path) -> dict[str, Any]:
    try:
        raw = load_yaml(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f".ddev/config.yaml could not be parsed: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(".ddev/config.yaml must contain a mapping at the top level")
    return raw
