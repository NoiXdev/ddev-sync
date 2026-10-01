"""End-to-end coverage of the shipped `node-build` feature against each
package manager.

Unlike `tests/features/test_resolver.py` (which exercises `_layer`'s
placeholder expansion against small, local fixture YAMLs) and
`tests/features/test_laravel_features.py` (which asserts the *shape* of the
shipped file), this resolves the real `features/node-build.yaml` through the
real resolver for a project with each lockfile in turn, so a mismatch between
`PackageManager`'s install verbs and what the shipped feature actually emits
cannot hide behind a fixture that only ever exercised one of the three.
"""

from pathlib import Path

import pytest

from ddev_sync.ddev.project import DdevProject
from ddev_sync.features.activation import Request
from ddev_sync.features.registry import FeatureRegistry
from ddev_sync.features.resolver import resolve_features

FEATURE_ROOT = Path(__file__).parent.parent.parent / "features"
SOURCE = "--features"


def project_at(root: Path) -> DdevProject:
    return DdevProject(name="acme", type="laravel", docroot="public", root=root)


@pytest.mark.parametrize(
    ("lockfile", "expected_install", "expected_build"),
    [
        ("package-lock.json", "npm ci", "npm run build"),
        ("yarn.lock", "yarn install --frozen-lockfile", "yarn build"),
        ("pnpm-lock.yaml", "pnpm install --frozen-lockfile", "pnpm run build"),
    ],
)
def test_each_package_manager_produces_its_own_real_commands(
    tmp_path: Path, lockfile: str, expected_install: str, expected_build: str
) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}', "utf-8")
    (tmp_path / lockfile).write_text("", encoding="utf-8")
    registry = FeatureRegistry(FEATURE_ROOT)
    resolution = resolve_features(
        registry,
        project_at(tmp_path),
        "laravel",
        [Request("node-build", {}, SOURCE)],
        True,
    )
    commands = resolution.layers[0].data["setup"]["commands"]
    assert commands == [{"run": expected_install}, {"run": expected_build}]
