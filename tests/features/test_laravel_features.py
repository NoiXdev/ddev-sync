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


def test_node_build_detect_requires_both_a_package_json_and_a_build_script() -> None:
    # Pinned explicitly: `file_exists: package.json` alone used to be enough,
    # which activated the feature on projects with no `build` script (Laravel
    # Mix's `dev`/`production`, tooling-only package.json files) and then
    # failed non-zero with no allow_failure, after the database was already
    # imported. A regression back to the single predicate must fail here even
    # if nothing else happens to exercise the gap.
    assert REGISTRY.load("node-build").detect == {
        "all_of": [
            {"file_exists": "package.json"},
            {"npm_script": "build"},
        ]
    }


def test_node_build_uses_the_derived_package_manager_not_a_hardcoded_one() -> None:
    joined = " ".join(commands("node-build"))
    assert "${PM_INSTALL}" in joined
    assert "${PM_RUN}" in joined
    for hardcoded in ("npm ", "yarn ", "pnpm "):
        assert hardcoded not in joined
    # Not a per-project setting (see package_manager.py): a project cannot
    # override the install verb, only which script `${PM_RUN}` runs.
    assert "${install}" not in joined


def test_node_build_exposes_only_script_as_a_setting() -> None:
    # `install` is not here: which verb installs from a lockfile exactly is a
    # fact about the package manager (see features/package_manager.py), not
    # a per-project choice - that was the bug (`install: ci` rendered as the
    # non-existent `pnpm ci` / `yarn ci` on those projects).
    settings = REGISTRY.load("node-build").settings
    assert sorted(settings) == ["script"]
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


def test_horizon_clears_queue_state_via_setup_not_sanitize() -> None:
    # Horizon's queue and supervisor state lives in Redis, not the database:
    # it ships no migrations and no horizon_jobs/horizon_supervisors tables
    # (checked against laravel/horizon 5.x and main). A sanitize.rules entry
    # that TRUNCATEs a table that does not exist does not merely fail
    # quietly - operations/sanitize.py::_run_sql raises unconditionally,
    # which aborts the whole sync right after the database import. So this
    # cleanup lives in `setup.commands`, which tolerates failure, not in
    # `sanitize.rules`, which cannot.
    data = REGISTRY.load("horizon").data
    assert "sanitize" not in data
    commands_run = [str(entry.get("run", "")) for entry in data["setup"]["commands"]]
    assert any("horizon:clear" in command for command in commands_run)


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


def test_composer_package_detections_are_pinned() -> None:
    # A typo in a detect value fails silently: the feature simply never
    # activates, and nothing reports the mismatch. Pinning the exact string
    # here makes any future change to one a deliberate edit, not a slip.
    assert REGISTRY.load("filament").detect == {"composer_package": "filament/filament"}
    assert REGISTRY.load("livewire").detect == {"composer_package": "livewire/livewire"}
    assert REGISTRY.load("inertia").detect == {"composer_package": "inertiajs/inertia-laravel"}
    assert REGISTRY.load("horizon").detect == {"composer_package": "laravel/horizon"}
