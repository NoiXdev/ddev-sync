import json
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import FeatureError
from ddev_sync.features.detect import PREDICATES, matches, validate_predicate


def project_at(root: Path, docroot: str = "public") -> DdevProject:
    return DdevProject(name="acme", type="laravel", docroot=docroot, root=root)


def write_lock(root: Path, packages: list[dict[str, Any]]) -> None:
    (root / "composer.lock").write_text(json.dumps({"packages": packages}), encoding="utf-8")


def write_package_json(root: Path, **sections: Any) -> None:
    (root / "package.json").write_text(json.dumps(sections), encoding="utf-8")


def test_composer_package_matches_when_present(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "filament/filament", "version": "v3.2.0"}])
    assert matches({"composer_package": "filament/filament"}, project_at(tmp_path)) is True


def test_composer_package_searches_packages_dev_too(tmp_path: Path) -> None:
    (tmp_path / "composer.lock").write_text(
        json.dumps({"packages": [], "packages-dev": [{"name": "x/y", "version": "1.0.0"}]}),
        encoding="utf-8",
    )
    assert matches({"composer_package": "x/y"}, project_at(tmp_path)) is True


def test_composer_package_does_not_match_when_absent(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "psr/log", "version": "3.0.0"}])
    assert matches({"composer_package": "filament/filament"}, project_at(tmp_path)) is False


def test_composer_package_with_no_lockfile_does_not_match(tmp_path: Path) -> None:
    assert matches({"composer_package": "filament/filament"}, project_at(tmp_path)) is False


@pytest.mark.parametrize("content", ["not json", "[]", "null", '"a string"'])
def test_a_corrupt_lockfile_does_not_match_and_does_not_raise(tmp_path: Path, content: str) -> None:
    (tmp_path / "composer.lock").write_text(content, encoding="utf-8")
    assert matches({"composer_package": "x/y"}, project_at(tmp_path)) is False


def test_npm_package_matches_in_dependencies(tmp_path: Path) -> None:
    write_package_json(tmp_path, dependencies={"@inertiajs/vue3": "^1.0"})
    assert matches({"npm_package": "@inertiajs/vue3"}, project_at(tmp_path)) is True


def test_npm_package_matches_in_dev_dependencies(tmp_path: Path) -> None:
    write_package_json(tmp_path, devDependencies={"vite": "^5.0"})
    assert matches({"npm_package": "vite"}, project_at(tmp_path)) is True


def test_npm_package_does_not_match_when_absent(tmp_path: Path) -> None:
    write_package_json(tmp_path, dependencies={"vue": "^3.0"})
    assert matches({"npm_package": "vite"}, project_at(tmp_path)) is False


@pytest.mark.parametrize("content", ["not json", "[]", "null", '"a string"'])
def test_a_corrupt_package_json_does_not_match_and_does_not_raise(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "package.json").write_text(content, encoding="utf-8")
    assert matches({"npm_package": "vite"}, project_at(tmp_path)) is False


def test_file_exists_matches_a_file(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert matches({"file_exists": "package.json"}, project_at(tmp_path)) is True


def test_file_exists_matches_a_directory(tmp_path: Path) -> None:
    (tmp_path / "resources").mkdir()
    assert matches({"file_exists": "resources"}, project_at(tmp_path)) is True


def test_file_exists_does_not_match_an_absent_path(tmp_path: Path) -> None:
    assert matches({"file_exists": "nope.json"}, project_at(tmp_path)) is False


def test_file_exists_cannot_escape_the_project_root(tmp_path: Path) -> None:
    (tmp_path.parent / "outside.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FeatureError) as caught:
        matches({"file_exists": "../outside.txt"}, project_at(tmp_path))
    assert "outside" in str(caught.value)


def test_any_of_matches_when_one_nested_predicate_matches(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "livewire/livewire", "version": "3.0.0"}])
    predicate = {
        "any_of": [
            {"composer_package": "filament/filament"},
            {"composer_package": "livewire/livewire"},
        ]
    }
    assert matches(predicate, project_at(tmp_path)) is True


def test_any_of_does_not_match_when_none_do(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "psr/log", "version": "3.0.0"}])
    predicate = {"any_of": [{"composer_package": "a/b"}, {"composer_package": "c/d"}]}
    assert matches(predicate, project_at(tmp_path)) is False


def test_all_of_requires_every_nested_predicate(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "inertiajs/inertia-laravel", "version": "1.0.0"}])
    write_package_json(tmp_path, dependencies={"@inertiajs/vue3": "^1.0"})
    predicate = {
        "all_of": [
            {"composer_package": "inertiajs/inertia-laravel"},
            {"npm_package": "@inertiajs/vue3"},
        ]
    }
    assert matches(predicate, project_at(tmp_path)) is True


def test_all_of_fails_when_one_is_missing(tmp_path: Path) -> None:
    write_lock(tmp_path, [{"name": "inertiajs/inertia-laravel", "version": "1.0.0"}])
    predicate = {
        "all_of": [
            {"composer_package": "inertiajs/inertia-laravel"},
            {"npm_package": "@inertiajs/vue3"},
        ]
    }
    assert matches(predicate, project_at(tmp_path)) is False


def test_combinators_nest(tmp_path: Path) -> None:
    write_package_json(tmp_path, dependencies={"vite": "^5.0"})
    predicate = {
        "all_of": [
            {"file_exists": "package.json"},
            {"any_of": [{"npm_package": "vite"}, {"npm_package": "webpack"}]},
        ]
    }
    assert matches(predicate, project_at(tmp_path)) is True


def test_an_empty_any_of_does_not_match(tmp_path: Path) -> None:
    assert matches({"any_of": []}, project_at(tmp_path)) is False


def test_an_empty_all_of_matches_vacuously(tmp_path: Path) -> None:
    # Python's all([]) is True and that is the right answer: "every one of no
    # conditions holds". A feature writing this gets unconditional activation,
    # which `typo3-processedfiles` wants expressed as no detect block at all.
    assert matches({"all_of": []}, project_at(tmp_path)) is True


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({}, "found 0"),
        ({"composer_package": "a/b", "npm_package": "c"}, "found 2"),
        ({"compser_package": "a/b"}, "unknown detection predicate"),
        ({"any_of": {"composer_package": "a/b"}}, "takes a list"),
        ({"all_of": ["not-a-predicate"]}, "takes a list"),
    ],
)
def test_a_malformed_predicate_is_rejected_by_shape(
    tmp_path: Path, predicate: Any, expected: str
) -> None:
    with pytest.raises(FeatureError) as caught:
        matches(predicate, project_at(tmp_path))
    assert expected in str(caught.value)


def test_the_vocabulary_is_exactly_five_predicates() -> None:
    assert {
        "composer_package",
        "npm_package",
        "file_exists",
        "any_of",
        "all_of",
    } == PREDICATES


def test_validate_predicate_rejects_unknown_predicate() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_predicate("my-feature", {"unknown_key": "value"})
    assert "my-feature" in str(caught.value)
    assert "unknown" in str(caught.value).lower()


def test_validate_predicate_rejects_any_of_that_is_not_a_list() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_predicate("my-feature", {"any_of": {"nested": "dict"}})
    assert "my-feature" in str(caught.value)


def test_validate_predicate_rejects_non_mapping() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_predicate("my-feature", "not a mapping")
    assert "my-feature" in str(caught.value)


def test_validate_predicate_accepts_valid_nested_predicates() -> None:
    validate_predicate(
        "my-feature",
        {
            "any_of": [
                {"composer_package": "a/b"},
                {"npm_package": "c"},
            ]
        },
    )


def test_validate_predicate_rejects_invalid_nested_predicates() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_predicate(
            "my-feature",
            {
                "all_of": [
                    {"composer_package": "a/b"},
                    {"unknown_predicate": "c"},
                ]
            },
        )
    assert "my-feature" in str(caught.value)


def test_file_exists_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(FeatureError) as caught:
        matches({"file_exists": ""}, project_at(tmp_path))
    assert "empty" in str(caught.value).lower()


def test_file_exists_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(FeatureError) as caught:
        matches({"file_exists": "/etc/passwd"}, project_at(tmp_path))
    assert "outside" in str(caught.value).lower() or "project root" in str(caught.value).lower()


def test_file_exists_rejects_symlink_pointing_outside_project(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    symlink = tmp_path / "link"
    symlink.symlink_to(outside)
    with pytest.raises(FeatureError) as caught:
        matches({"file_exists": "link"}, project_at(tmp_path))
    assert "outside" in str(caught.value).lower() or "project root" in str(caught.value).lower()


def test_validate_predicate_rejects_empty_file_exists() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_predicate("my-feature", {"file_exists": ""})
    assert "my-feature" in str(caught.value)
    assert "non-empty" in str(caught.value).lower()


def test_validate_predicate_rejects_non_string_file_exists() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_predicate("my-feature", {"file_exists": 123})
    assert "my-feature" in str(caught.value)
    assert "non-empty" in str(caught.value).lower()
