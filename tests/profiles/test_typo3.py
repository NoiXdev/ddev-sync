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
    """typo3@12/@13 are near-empty deltas (`extends: typo3` only), and @11
    carries a replaced rule list, but none of the three has a `name` key of
    its own - `validate_profile` runs on the merged document, so the name
    inherited from the base satisfies it. This is the asymmetry documented in
    the task brief: a *base* profile without a name is rejected, a *variant*
    without one is fine because it is never validated on its own.
    """
    for major in ("11", "12", "13"):
        resolved = registry.resolve("typo3", major)
        assert resolved.data["name"] == "typo3"


def test_the_no_variant_version_deltas_still_prevent_a_wrong_nearest_lower_match(
    registry: ProfileRegistry,
) -> None:
    """Without the typo3@11/@12/@13 files, a v13 project would
    resolve through nearest-lower matching to typo3@10 and silently run the
    typo3cms binary the modern CLI does not ship. Pin that it does not.
    """
    resolved = registry.resolve("typo3", "13")
    assert resolved.label == "typo3@13"
    commands = [entry["run"] for entry in resolved.data["setup"]["commands"]]
    assert not any("typo3cms" in command for command in commands)


# Measured against a stock typo3/cms-base-distribution:^11 install
# (typo3/cms-core v11.5.41, PHP 8.1.34): `typo3 list --raw` offers eighteen
# commands and not one of them creates a user. Core gained
# `backend:user:create` in 12. Inheriting the base's `local-admin` rule
# therefore aborted a sync inside sanitize - after the production database was
# already imported.
def test_typo3_11_declares_no_create_admin_rule(registry: ProfileRegistry) -> None:
    rules = registry.resolve("typo3", "11").data["sanitize"]["rules"]
    assert not any(rule.get("operation") == "create_admin" for rule in rules)


# And the two it does keep from the base, which 11.5.41 really has: the rule
# list is replaced wholesale, so every data-protecting rule had to be copied
# across by hand and a typo would drop one silently.
def test_typo3_11_keeps_the_rules_that_protect_production_data(
    registry: ProfileRegistry,
) -> None:
    names = {rule["name"] for rule in registry.resolve("typo3", "11").data["sanitize"]["rules"]}
    assert {
        "anonymize-be-users",
        "reset-be-passwords",
        "anonymize-fe-users",
        "reset-fe-passwords",
    } <= names


def test_typo3_11_still_runs_cores_commands_and_not_typo3cms(
    registry: ProfileRegistry,
) -> None:
    resolved = registry.resolve("typo3", "11")
    commands = [entry["run"] for entry in resolved.data["setup"]["commands"]]
    assert not any("typo3cms" in command for command in commands)
    assert any("extension:setup" in command for command in commands)
    flush = [rule for rule in resolved.data["sanitize"]["rules"] if rule["name"] == "flush-cache"]
    assert flush and flush[0]["command"] == "vendor/bin/typo3 cache:flush"


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
