from pathlib import Path

import pytest

from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import FeatureError
from ddev_sync.features.package_manager import (
    LOCKFILES,
    detect_package_manager,
    require_package_manager,
)


def project_at(root: Path) -> DdevProject:
    return DdevProject(name="acme", type="laravel", docroot="public", root=root)


@pytest.mark.parametrize(
    ("lockfile", "expected_name", "expected_run"),
    [
        ("package-lock.json", "npm", "npm run"),
        ("yarn.lock", "yarn", "yarn"),
        ("pnpm-lock.yaml", "pnpm", "pnpm run"),
    ],
)
def test_each_lockfile_selects_its_manager(
    tmp_path: Path, lockfile: str, expected_name: str, expected_run: str
) -> None:
    (tmp_path / lockfile).write_text("", encoding="utf-8")
    manager = detect_package_manager(project_at(tmp_path))
    assert manager is not None
    assert manager.name == expected_name
    assert manager.run == expected_run


def test_yarn_does_not_use_run_because_yarn_build_is_the_idiom(tmp_path: Path) -> None:
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    manager = detect_package_manager(project_at(tmp_path))
    assert manager is not None
    assert manager.run == "yarn"


def test_no_lockfile_detects_nothing(tmp_path: Path) -> None:
    assert detect_package_manager(project_at(tmp_path)) is None


def test_two_lockfiles_resolve_deterministically_in_declared_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("", encoding="utf-8")
    first = LOCKFILES[0][0]
    manager = detect_package_manager(project_at(tmp_path))
    assert manager is not None
    assert manager.name == dict(LOCKFILES)[first].name


def test_require_returns_the_manager_when_one_is_present(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert require_package_manager(project_at(tmp_path)).name == "pnpm"


def test_require_names_every_lockfile_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(FeatureError) as caught:
        require_package_manager(project_at(tmp_path))
    message = str(caught.value)
    for lockfile, _ in LOCKFILES:
        assert lockfile in message


def test_a_lockfile_that_is_a_directory_is_not_a_lockfile(tmp_path: Path) -> None:
    (tmp_path / "yarn.lock").mkdir()
    assert detect_package_manager(project_at(tmp_path)) is None
