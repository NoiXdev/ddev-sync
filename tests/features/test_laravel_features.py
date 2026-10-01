from pathlib import Path

import pytest

from ddev_sync.features.registry import FeatureRegistry

FEATURE_ROOT = Path(__file__).parent.parent.parent / "features"
REGISTRY = FeatureRegistry(FEATURE_ROOT)


def commands(name: str) -> list[str]:
    data = REGISTRY.load(name).data
    return [str(entry["run"]) for entry in data.get("setup", {}).get("commands", [])]


def test_node_build_is_profile_agnostic() -> None:
    assert REGISTRY.load("node-build").applies_to == ()


def test_node_build_uses_the_derived_package_manager_not_a_hardcoded_one() -> None:
    joined = " ".join(commands("node-build"))
    assert "${PM}" in joined
    assert "${PM_RUN}" in joined
    for hardcoded in ("npm ", "yarn ", "pnpm "):
        assert hardcoded not in joined


def test_node_build_exposes_install_and_script_as_settings() -> None:
    settings = REGISTRY.load("node-build").settings
    assert sorted(settings) == ["install", "script"]
    assert settings["install"].default == "ci"
    assert settings["script"].default == "build"


def test_every_setting_carries_a_description() -> None:
    for name in REGISTRY.available():
        for key, spec in REGISTRY.load(name).settings.items():
            assert spec.description, f"{name}.{key} has no description"


@pytest.mark.parametrize("name", ["filament", "livewire", "inertia", "horizon"])
def test_the_laravel_features_are_scoped_to_laravel(name: str) -> None:
    assert REGISTRY.load(name).applies_to == ("laravel",)


def test_inertia_contributes_nothing_and_that_is_allowed() -> None:
    feature = REGISTRY.load("inertia")
    assert feature.data == {}
    assert feature.detect


def test_horizon_clears_queue_state_as_sanitize_not_setup() -> None:
    rules = REGISTRY.load("horizon").data["sanitize"]["rules"]
    names = {rule["name"] for rule in rules}
    assert "clear-horizon-jobs" in names


def test_laravels_baseline_queue_safety_is_in_the_profile_not_the_feature() -> None:
    # Forgetting a feature must not leave production jobs runnable locally.
    from ddev_sync.profiles.registry import ProfileRegistry

    profiles = ProfileRegistry(Path(__file__).parent.parent.parent / "profiles")
    rules = profiles.resolve("laravel", None).data["sanitize"]["rules"]
    names = {rule["name"] for rule in rules}
    assert {"clear-jobs", "clear-failed-jobs"} <= names


@pytest.mark.parametrize("name", REGISTRY.available())
def test_commands_that_may_not_exist_tolerate_failure(name: str) -> None:
    # A framework CLI subcommand that a given version does not have must not
    # abort a sync whose database import already succeeded.
    for entry in REGISTRY.load(name).data.get("setup", {}).get("commands", []):
        if "artisan" in str(entry.get("run", "")):
            assert entry.get("allow_failure") is True, entry


def test_horizon_does_not_duplicate_the_profiles_jobs_tables() -> None:
    # horizon_jobs/horizon_supervisors are Horizon-specific tables, distinct
    # from the plain `jobs`/`failed_jobs` tables the base laravel profile
    # already truncates; this pins that the feature does not re-truncate what
    # the profile already covers.
    rules = REGISTRY.load("horizon").data["sanitize"]["rules"]
    names = {rule["name"] for rule in rules}
    assert names == {"clear-horizon-jobs", "clear-horizon-supervisors"}
