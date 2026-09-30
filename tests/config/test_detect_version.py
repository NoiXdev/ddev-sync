import json
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.detect import (
    COMPOSER_PACKAGE_FOR_PROFILE,
    Resolution,
    detect_major_version,
    resolve_version,
)
from ddev_sync.ddev.project import DdevProject


def project_at(root: Path, docroot: str = "public") -> DdevProject:
    return DdevProject(name="acme", type="typo3", docroot=docroot, root=root)


def write_lock(root: Path, packages: list[dict[str, Any]]) -> None:
    (root / "composer.lock").write_text(json.dumps({"packages": packages}), encoding="utf-8")


@pytest.mark.parametrize(
    ("profile", "package", "version", "expected"),
    [
        ("typo3", "typo3/cms-core", "v12.4.20", "12"),
        ("typo3", "typo3/cms-core", "12.4.20", "12"),
        ("laravel", "laravel/framework", "v11.30.0", "11"),
        ("symfony", "symfony/framework-bundle", "v7.1.6", "7"),
        ("shopware6", "shopware/core", "v6.6.8.1", "6"),
        ("drupal", "drupal/core", "10.3.6", "10"),
    ],
)
def test_the_major_version_comes_from_composer_lock(
    tmp_path: Path, profile: str, package: str, version: str, expected: str
) -> None:
    write_lock(tmp_path, [{"name": package, "version": version}])
    assert detect_major_version(project_at(tmp_path), profile) == expected


def test_the_package_is_found_among_many(tmp_path: Path) -> None:
    write_lock(
        tmp_path,
        [
            {"name": "psr/log", "version": "3.0.0"},
            {"name": "typo3/cms-core", "version": "v11.5.41"},
            {"name": "guzzlehttp/guzzle", "version": "7.9.2"},
        ],
    )
    assert detect_major_version(project_at(tmp_path), "typo3") == "11"


def test_packages_dev_is_searched_too(tmp_path: Path) -> None:
    (tmp_path / "composer.lock").write_text(
        json.dumps(
            {"packages": [], "packages-dev": [{"name": "drupal/core", "version": "10.2.0"}]}
        ),
        encoding="utf-8",
    )
    assert detect_major_version(project_at(tmp_path), "drupal") == "10"


def test_no_composer_lock_at_all_returns_none(tmp_path: Path) -> None:
    assert detect_major_version(project_at(tmp_path), "typo3") is None


def test_the_generic_profile_has_no_version_to_detect(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "typo3/cms-core", "version": "v12.4.0"}])
    assert detect_major_version(project_at(tmp_path), "generic") is None


def test_the_composer_package_map_covers_every_versioned_profile() -> None:
    assert sorted(COMPOSER_PACKAGE_FOR_PROFILE) == [
        "drupal",
        "laravel",
        "shopware6",
        "symfony",
        "typo3",
    ]


def write_wp_version(root: Path, docroot: str, body: str) -> None:
    target = root / docroot / "wp-includes" if docroot else root / "wp-includes"
    target.mkdir(parents=True, exist_ok=True)
    (target / "version.php").write_text(body, encoding="utf-8")


def test_wordpress_version_comes_from_version_php(tmp_path: Path) -> None:
    write_wp_version(tmp_path, "public", "<?php\n$wp_version = '6.7.1';\n")
    assert detect_major_version(project_at(tmp_path), "wordpress") == "6"


def test_wordpress_respects_an_empty_docroot(tmp_path: Path) -> None:
    write_wp_version(tmp_path, "", "<?php\n$wp_version = '6.5.5';\n")
    assert detect_major_version(project_at(tmp_path, docroot=""), "wordpress") == "6"


def test_a_missing_wordpress_version_file_returns_none(tmp_path: Path) -> None:
    assert detect_major_version(project_at(tmp_path), "wordpress") is None


def test_a_wordpress_version_file_without_the_variable_returns_none(tmp_path: Path) -> None:
    write_wp_version(tmp_path, "public", "<?php\n// nothing useful here\n")
    assert detect_major_version(project_at(tmp_path), "wordpress") is None


def test_the_version_flag_beats_everything(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "typo3/cms-core", "version": "v12.4.0"}])
    assert resolve_version("9", None, project_at(tmp_path), "typo3") == Resolution(
        value="9", source="--framework-version flag"
    )


def test_configuration_beats_detection(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "typo3/cms-core", "version": "v12.4.0"}])
    assert resolve_version(None, "11", project_at(tmp_path), "typo3") == Resolution(
        value="11", source=".ddev/ddev-sync.yaml"
    )


def test_detection_is_used_when_nothing_is_configured(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "typo3/cms-core", "version": "v12.4.0"}])
    assert resolve_version(None, None, project_at(tmp_path), "typo3") == Resolution(
        value="12", source="composer.lock"
    )


def test_wordpress_detection_names_its_own_source(tmp_path: Path) -> None:
    write_wp_version(tmp_path, "public", "<?php\n$wp_version = '6.7.1';\n")
    resolution = resolve_version(None, None, project_at(tmp_path), "wordpress")
    assert resolution == Resolution(value="6", source="wp-includes/version.php")


def test_nothing_detectable_returns_none_so_the_cli_can_ask(tmp_path: Path) -> None:
    assert resolve_version(None, None, project_at(tmp_path), "typo3") is None


@pytest.mark.parametrize("version", ["dev-main", "9999999-dev", "dev-feature/x", ""])
def test_a_branch_version_yields_no_major_so_the_cli_asks(tmp_path: Path, version: str) -> None:
    write_lock(tmp_path, [{"name": "typo3/cms-core", "version": version}])
    assert detect_major_version(project_at(tmp_path), "typo3") is None


def test_a_lock_file_without_the_expected_package_yields_none(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "psr/log", "version": "3.0.0"}])
    assert detect_major_version(project_at(tmp_path), "typo3") is None


@pytest.mark.parametrize("content", ["not json at all", "[]", '"a string"', "null"])
def test_a_corrupt_lock_file_yields_none_rather_than_raising(tmp_path: Path, content: str) -> None:
    (tmp_path / "composer.lock").write_text(content, encoding="utf-8")
    assert detect_major_version(project_at(tmp_path), "typo3") is None


def test_a_lock_file_whose_packages_key_is_not_a_list_yields_none(tmp_path: Path) -> None:
    (tmp_path / "composer.lock").write_text(
        json.dumps({"packages": {"typo3/cms-core": "12"}}), encoding="utf-8"
    )
    assert detect_major_version(project_at(tmp_path), "typo3") is None


def test_a_package_entry_without_a_version_yields_none(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "typo3/cms-core"}])
    assert detect_major_version(project_at(tmp_path), "typo3") is None


def test_the_oserror_branch_is_reached_when_version_file_is_a_directory(tmp_path: Path) -> None:
    docroot = tmp_path / "public"
    version_dir = docroot / "wp-includes" / "version.php"
    version_dir.mkdir(parents=True)
    assert detect_major_version(project_at(tmp_path), "wordpress") is None
