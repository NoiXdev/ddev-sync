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
REGISTRY = FeatureRegistry(FEATURE_ROOT)
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


def test_detection_activates_only_when_a_real_build_script_exists(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}', "utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    resolution = resolve_features(REGISTRY, project_at(tmp_path), "laravel", None, True)
    assert [a.feature.name for a in resolution.activated] == ["node-build"]


def test_detection_stays_quiet_on_a_package_json_with_no_build_script(tmp_path: Path) -> None:
    # Laravel Mix names its scripts `dev`/`production`; a package.json like
    # this used to activate node-build anyway (the old detect block only
    # checked file_exists) and then failed non-zero at `${PM_RUN} build`,
    # after the database was already imported. Detection must simply never
    # consider it a candidate - no error, no mention - exactly like an
    # out-of-scope applies_to candidate.
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "mix"}}', "utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    resolution = resolve_features(REGISTRY, project_at(tmp_path), "laravel", None, True)
    assert "node-build" not in [a.feature.name for a in resolution.activated]


def test_an_explicit_request_still_activates_without_a_build_script(tmp_path: Path) -> None:
    # Detection goes quiet without a build script; an explicit request does
    # not - it bypasses the detect block entirely (as every feature's detect
    # block does for an explicit request), so it still activates here and
    # fails loudly only when the actual `${PM_RUN} build` command runs and
    # npm/yarn/pnpm itself reports the missing script. The user asked for
    # something impossible and is told so, rather than it being silently
    # skipped.
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "mix"}}', "utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    resolution = resolve_features(
        REGISTRY,
        project_at(tmp_path),
        "laravel",
        [Request("node-build", {}, SOURCE)],
        True,
    )
    assert [a.feature.name for a in resolution.activated] == ["node-build"]
    commands = resolution.layers[0].data["setup"]["commands"]
    assert commands == [{"run": "npm ci"}, {"run": "npm run build"}]
