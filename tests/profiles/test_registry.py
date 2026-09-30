from pathlib import Path

import pytest

from ddev_sync.errors import ProfileError
from ddev_sync.profiles.registry import ProfileRegistry


def write_profile(root: Path, filename: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(body, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    profiles = tmp_path / "profiles"
    write_profile(
        profiles,
        "typo3.yaml",
        "name: typo3\nsetup:\n  commands:\n    - run: composer install\n",
    )
    write_profile(
        profiles,
        "typo3@9.yaml",
        "extends: typo3\nsetup:\n  commands:\n    mode: replace\n    values:\n"
        "      - run: vendor/bin/typo3cms cache:flush\n",
    )
    write_profile(
        profiles,
        "typo3@12.yaml",
        "extends: typo3\nsetup:\n  commands:\n    mode: extend\n    values:\n"
        "      - run: vendor/bin/typo3 cache:flush\n",
    )
    write_profile(profiles, "generic.yaml", "name: generic\n")
    return profiles


def test_a_base_profile_loads_without_a_variant(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("generic", None)
    assert resolved.name == "generic"
    assert resolved.label == "generic"
    assert resolved.notice is None


def test_an_exact_variant_is_applied_as_a_delta(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", "12")
    assert resolved.label == "typo3@12"
    assert resolved.notice is None
    assert resolved.data["setup"]["commands"] == [
        {"run": "composer install"},
        {"run": "vendor/bin/typo3 cache:flush"},
    ]


def test_a_variant_may_replace_the_base_list(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", "9")
    assert resolved.data["setup"]["commands"] == [{"run": "vendor/bin/typo3cms cache:flush"}]


def test_the_layers_are_returned_labelled_for_provenance(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", "12")
    assert [entry.label for entry in resolved.layers] == ["profile:typo3", "profile:typo3@12"]


def test_available_majors_are_sorted(root: Path) -> None:
    assert ProfileRegistry(root).available_majors("typo3") == (9, 12)


def test_available_profiles_excludes_variants(root: Path) -> None:
    assert ProfileRegistry(root).available_profiles() == ["generic", "typo3"]


def test_no_version_given_uses_the_base_alone(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", None)
    assert resolved.label == "typo3"
    assert resolved.data["setup"]["commands"] == [{"run": "composer install"}]


def test_an_unknown_profile_lists_the_ones_that_exist(root: Path) -> None:
    with pytest.raises(ProfileError) as caught:
        ProfileRegistry(root).resolve("joomla", None)
    message = str(caught.value)
    assert "joomla" in message
    assert "generic, typo3" in message


def test_an_empty_profile_file_has_no_name_and_is_rejected(tmp_path: Path) -> None:
    # An empty YAML document parses to `{}` (see `_read`'s `raw is None` branch
    # below), which is a legitimate *document* but not a well-formed *profile*:
    # `validate_profile` now runs on every merged profile and requires `name`,
    # so a nameless profile fails at load time rather than resolving to a
    # silently useless empty plan.
    profiles = tmp_path / "profiles"
    write_profile(profiles, "empty.yaml", "")
    with pytest.raises(ProfileError) as caught:
        ProfileRegistry(profiles).resolve("empty", None)
    assert "name" in str(caught.value)


def test_unparseable_profile_yaml_names_the_file(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    write_profile(profiles, "broken.yaml", "name: [unclosed\n")
    with pytest.raises(ProfileError) as caught:
        ProfileRegistry(profiles).resolve("broken", None)
    assert "broken.yaml" in str(caught.value)


def test_a_non_mapping_profile_is_rejected(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    write_profile(profiles, "listy.yaml", "- one\n")
    with pytest.raises(ProfileError) as caught:
        ProfileRegistry(profiles).resolve("listy", None)
    assert "mapping" in str(caught.value)


def test_a_non_numeric_version_is_rejected_with_the_available_variants(root: Path) -> None:
    with pytest.raises(ProfileError) as caught:
        ProfileRegistry(root).resolve("typo3", "twelve")
    message = str(caught.value)
    assert "twelve" in message
    assert "9, 12" in message


def test_a_version_on_a_profile_with_no_variants_is_simply_ignored(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("generic", "3")
    assert resolved.label == "generic"
    assert resolved.notice is None


def test_an_empty_profile_directory_reports_none_available(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(ProfileError) as caught:
        ProfileRegistry(empty).resolve("typo3", None)
    assert "available profiles: none" in str(caught.value)


def test_the_extends_key_never_leaks_into_the_merged_profile(root: Path) -> None:
    assert "extends" not in ProfileRegistry(root).resolve("typo3", "12").data


def test_a_major_newer_than_every_variant_falls_back_and_says_so(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", "14")
    assert resolved.label == "typo3@12"
    assert resolved.notice == "typo3 14 detected - no typo3@14 profile, using typo3@12"
    assert resolved.data["setup"]["commands"] == [
        {"run": "composer install"},
        {"run": "vendor/bin/typo3 cache:flush"},
    ]


def test_a_major_between_two_variants_picks_the_lower_one(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", "11")
    assert resolved.label == "typo3@9"
    assert "using typo3@9" in (resolved.notice or "")


def test_a_major_older_than_every_variant_uses_the_base_with_a_notice(root: Path) -> None:
    resolved = ProfileRegistry(root).resolve("typo3", "8")
    assert resolved.label == "typo3"
    assert resolved.notice is not None
    assert "older than every variant" in resolved.notice
    assert "9, 12" in resolved.notice
    assert resolved.data["setup"]["commands"] == [{"run": "composer install"}]
