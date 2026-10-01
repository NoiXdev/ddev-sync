"""Derive the project's package manager from its lockfile.

Not a setting: which manager a project uses is a fact it already records.
Asking the user to declare it is asking them to repeat themselves and to
keep the repetition correct.

This module exports two detection functions. Callers should:
- Use `detect_package_manager` when the absence of a lockfile is acceptable
  (e.g., when deciding whether to activate a feature).
- Use `require_package_manager` when the user has made an explicit request that
  requires a lockfile to succeed, so the absence can be reported as a proper error.
"""

from dataclasses import dataclass

from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import FeatureError


@dataclass(frozen=True)
class PackageManager:
    """The binary, and the form that runs a package.json script.

    `run` is separate rather than `f"{name} run"` because yarn differs:
    `yarn build` is the idiom, while npm and pnpm need `run`.
    """

    name: str
    run: str


# Ordered tuple rather than a dict to ensure deterministic resolution when
# a project has multiple lockfiles (which happens after incomplete migrations).
# The order here defines the priority: npm, yarn, pnpm.
LOCKFILES: tuple[tuple[str, PackageManager], ...] = (
    ("package-lock.json", PackageManager("npm", "npm run")),
    ("yarn.lock", PackageManager("yarn", "yarn")),
    ("pnpm-lock.yaml", PackageManager("pnpm", "pnpm run")),
)


def detect_package_manager(project: DdevProject) -> PackageManager | None:
    """The manager whose lockfile is present, or None.

    Returns the first manager whose lockfile exists in the project root.
    Files that are directories are not considered (by checking is_file()).
    """
    existing = {entry.name for entry in project.root.iterdir() if entry.is_file()}
    for lockfile, manager in LOCKFILES:
        if lockfile in existing:
            return manager
    return None


def require_package_manager(project: DdevProject) -> PackageManager:
    """Like `detect_package_manager`, but raises when nothing is found."""
    manager = detect_package_manager(project)
    if manager is not None:
        return manager
    looked = ", ".join(lockfile for lockfile, _ in LOCKFILES)
    raise FeatureError(
        f"no lockfile found in {project.root}; a feature using ${{PM}} needs one of: {looked}"
    )
