from pathlib import Path

import pytest

from ddev_sync.profiles.registry import ProfileRegistry
from tests.test_no_legacy_terms import BANNED

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)
PROFILE_NAMES = REGISTRY.available_profiles()


def variants() -> list[tuple[str, str | None]]:
    combinations: list[tuple[str, str | None]] = []
    for name in PROFILE_NAMES:
        combinations.append((name, None))
        combinations.extend((name, str(major)) for major in REGISTRY.available_majors(name))
    return combinations


@pytest.mark.parametrize(("name", "major"), variants())
def test_every_profile_variant_loads_and_validates(name: str, major: str | None) -> None:
    resolved = REGISTRY.resolve(name, major)
    assert resolved.data["name"] == name


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_every_profile_declares_a_name_matching_its_filename(name: str) -> None:
    assert REGISTRY.resolve(name, None).data["name"] == name


@pytest.mark.parametrize(("name", "major"), variants())
def test_no_profile_mentions_an_agency_specific_term(name: str, major: str | None) -> None:
    # Reuses the repo-wide scanner's own pattern rather than spelling the
    # banned terms out a second time here: a literal copy of them would trip
    # that very scanner, since it only exempts its own file by name.
    text = str(REGISTRY.resolve(name, major).data)
    assert BANNED.search(text) is None


@pytest.mark.parametrize(("name", "major"), variants())
def test_every_local_files_path_is_relative(name: str, major: str | None) -> None:
    paths = REGISTRY.resolve(name, major).data.get("files", {}).get("paths", [])
    for entry in paths:
        assert not str(entry["local"]).startswith("/")
        assert ".." not in str(entry["local"])
