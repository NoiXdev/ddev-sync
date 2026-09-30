from pathlib import Path
from typing import Any, cast

import pytest

from ddev_sync.profiles.registry import ProfileRegistry

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)


def setup_commands(name: str) -> list[str]:
    return [
        str(entry["run"])
        for entry in REGISTRY.resolve(name, None).data["setup"]["commands"]
        if "run" in entry
    ]


def env_map(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], REGISTRY.resolve(name, None).data["database"]["env_map"])


def test_laravel_reads_the_standard_db_variables() -> None:
    assert env_map("laravel") == {
        "name": "DB_DATABASE",
        "user": "DB_USERNAME",
        "password": "DB_PASSWORD",
        "host": "DB_HOST",
        "port": "DB_PORT",
    }


def test_laravel_points_the_env_at_ddev() -> None:
    values = REGISTRY.resolve("laravel", None).data["env"]["set"]
    assert values["DB_HOST"] == "ddev-${DDEV_SITENAME}-db"
    assert values["APP_ENV"] == "local"
    assert values["APP_DEBUG"] == "true"


def test_laravel_syncs_the_public_storage_link_target() -> None:
    paths = REGISTRY.resolve("laravel", None).data["files"]["paths"]
    assert {entry["local"] for entry in paths} == {"storage/app/public/"}


def test_laravel_runs_migrate_and_clears_caches() -> None:
    commands = setup_commands("laravel")
    assert any("composer install" in command for command in commands)
    assert any("artisan migrate" in command for command in commands)
    assert any("artisan optimize:clear" in command for command in commands)


def test_laravel_clears_sessions_and_jobs_when_sanitizing() -> None:
    rules = REGISTRY.resolve("laravel", None).data["sanitize"]["rules"]
    names = {rule["name"] for rule in rules}
    assert {"clear-sessions", "clear-jobs", "clear-failed-jobs"} <= names


def test_symfony_reads_the_standard_database_variables() -> None:
    assert env_map("symfony")["name"] == "DATABASE_NAME"


def test_symfony_prefers_env_local() -> None:
    assert REGISTRY.resolve("symfony", None).data["env"]["file"] == [".env.local", ".env"]


def test_symfony_sets_a_composed_database_url() -> None:
    values = REGISTRY.resolve("symfony", None).data["env"]["set"]
    assert "DATABASE_URL" in values
    assert "${DDEV_SITENAME}" in values["DATABASE_URL"]


def test_symfony_clears_the_cache_after_import() -> None:
    commands = setup_commands("symfony")
    assert any("cache:clear" in command for command in commands)


@pytest.mark.parametrize("name", ["laravel", "symfony"])
def test_neither_profile_declares_version_variants_yet(name: str) -> None:
    assert REGISTRY.available_majors(name) == ()
