from pathlib import Path
from typing import Any

import pytest

from ddev_sync.profiles.registry import ProfileRegistry

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"


@pytest.fixture
def registry() -> ProfileRegistry:
    return ProfileRegistry(PROFILE_ROOT)


def test_every_declared_major_has_a_variant(registry: ProfileRegistry) -> None:
    assert registry.available_majors("typo3") == (9, 10, 11, 12, 13)


@pytest.mark.parametrize(("major", "binary"), [("9", "typo3cms"), ("10", "typo3cms")])
def test_the_console_era_uses_the_typo3cms_binary(
    registry: ProfileRegistry, major: str, binary: str
) -> None:
    resolved = registry.resolve("typo3", major)
    commands = [entry["run"] for entry in resolved.data["setup"]["commands"]]
    assert any(binary in command for command in commands)
    assert not any("vendor/bin/typo3 " in command for command in commands)


@pytest.mark.parametrize("major", ["11", "12", "13"])
def test_the_modern_era_uses_the_typo3_binary(registry: ProfileRegistry, major: str) -> None:
    resolved = registry.resolve("typo3", major)
    commands = [entry["run"] for entry in resolved.data["setup"]["commands"]]
    assert any("vendor/bin/typo3 " in command for command in commands)
    assert not any("typo3cms" in command for command in commands)


def test_a_v14_project_falls_back_to_v13_with_a_notice(registry: ProfileRegistry) -> None:
    resolved = registry.resolve("typo3", "14")
    assert resolved.label == "typo3@13"
    assert resolved.notice is not None


def test_the_admin_driver_matches_the_era(registry: ProfileRegistry) -> None:
    old = registry.resolve("typo3", "9")
    new = registry.resolve("typo3", "12")
    assert _admin_driver(old.data) == "console"
    assert _admin_driver(new.data) == "typo3"


def _admin_driver(data: dict[str, Any]) -> str:
    for rule in data["sanitize"]["rules"]:
        if rule.get("operation") == "create_admin":
            return str(rule["driver"])
    raise AssertionError("no create_admin rule in the profile")


def test_fileadmin_and_uploads_are_both_synced(registry: ProfileRegistry) -> None:
    paths = registry.resolve("typo3", "12").data["files"]["paths"]
    locals_ = {entry["local"] for entry in paths}
    assert locals_ == {"public/fileadmin/", "public/uploads/"}


def test_a_variant_with_no_name_of_its_own_still_validates(registry: ProfileRegistry) -> None:
    """typo3@11/@12/@13 are near-empty deltas (`extends: typo3` only) with no
    `name` key of their own - `validate_profile` runs on the merged document,
    so the name inherited from the base satisfies it. This is the asymmetry
    documented in the task brief: a *base* profile without a name is rejected,
    a *variant* without one is fine because it is never validated on its own.
    """
    for major in ("11", "12", "13"):
        resolved = registry.resolve("typo3", major)
        assert resolved.data["name"] == "typo3"


def test_the_no_variant_version_deltas_still_prevent_a_wrong_nearest_lower_match(
    registry: ProfileRegistry,
) -> None:
    """Without the near-empty typo3@11/@12/@13 files, a v13 project would
    resolve through nearest-lower matching to typo3@10 and silently run the
    typo3cms binary the modern CLI does not ship. Pin that it does not.
    """
    resolved = registry.resolve("typo3", "13")
    assert resolved.label == "typo3@13"
    commands = [entry["run"] for entry in resolved.data["setup"]["commands"]]
    assert not any("typo3cms" in command for command in commands)


@pytest.mark.parametrize("major", ["11", "12", "13"])
def test_the_modern_era_updates_the_schema_with_a_core_command(
    registry: ProfileRegistry, major: str
) -> None:
    # `database:updateschema` is helhum/typo3-console's: a stock install has no
    # `database` namespace, and as an `allow_failure` step the engine printed
    # the missing command as OK. `extension:setup` is core's, and is not
    # allowed to fail silently.
    resolved = registry.resolve("typo3", major)
    entries = {entry["run"]: entry for entry in resolved.data["setup"]["commands"]}
    assert "vendor/bin/typo3 database:updateschema" not in entries
    assert "allow_failure" not in entries["vendor/bin/typo3 extension:setup"]


def test_the_env_step_is_off_but_keeps_what_it_would_patch(registry: ProfileRegistry) -> None:
    # A composer-installed TYPO3 has no `.env`, and `patch_keys` raises when it
    # finds none - after the production import and before sanitize. The keys
    # stay in the profile as the record of what a `helhum/dotenv-connector`
    # project would opt back in to.
    env = registry.resolve("typo3", "13").data["env"]
    assert env["strategy"] == "none"
    assert env["set"]["TYPO3_DB_HOST"] == "ddev-${DDEV_SITENAME}-db"
