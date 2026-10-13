from pathlib import Path
from typing import Any, cast

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.env import patch_keys
from ddev_sync.operations.sanitize import parse_rules
from ddev_sync.profiles.registry import ProfileRegistry
from ddev_sync.transport.fake import FakeCommandRunner

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)


def symfony_context(tmp_path: Path, **override: Any) -> ExecutionContext:
    profile = REGISTRY.resolve("symfony", None)
    layers = [LayerDoc("profile:symfony", profile.data)]
    if override:
        layers.append(LayerDoc(".ddev/ddev-sync.yaml", override))
    return ExecutionContext(
        project=DdevProject(name="acme", type="symfony", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers(layers),
        runner=FakeCommandRunner(),
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


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


def test_the_symfony_database_url_agrees_with_the_discrete_parts() -> None:
    values = REGISTRY.resolve("symfony", None).data["env"]["set"]
    expected = (
        f"mysql://{values['DATABASE_USER']}:{values['DATABASE_PASSWORD']}"
        f"@{values['DATABASE_HOST']}:{values['DATABASE_PORT']}"
        f"/{values['DATABASE_NAME']}"
    )
    assert values["DATABASE_URL"] == expected, (
        "DATABASE_URL and the discrete DATABASE_* keys must describe the same "
        "database. A Symfony project may read either; if they disagree, the app "
        "silently talks to the wrong one."
    )


def test_symfony_clears_the_cache_after_import() -> None:
    commands = setup_commands("symfony")
    assert any("cache:clear" in command for command in commands)


@pytest.mark.parametrize("name", ["laravel", "symfony"])
def test_neither_profile_declares_version_variants_yet(name: str) -> None:
    assert REGISTRY.available_majors(name) == ()


def test_symfony_user_table_defaults_to_user(tmp_path: Path) -> None:
    # Part 4: `user` is Symfony's conventional default, not a guarantee - see
    # profiles/VERIFIED.md. Unset, both the sql rule and the operation rule's
    # `table` param must still resolve to the historical default.
    parsed = parse_rules(symfony_context(tmp_path))
    anonymize = next(entry for entry in parsed if entry.name == "anonymize-users")
    reset = next(entry for entry in parsed if entry.name == "reset-user-passwords")
    assert anonymize.sql == "UPDATE user SET email = CONCAT('user', id, '@example.invalid')"
    assert anonymize.requires_table == "user"
    assert reset.params["table"] == "user"


def test_symfony_user_table_is_overridable(tmp_path: Path) -> None:
    context = symfony_context(tmp_path, database={"symfony_user_table": "app_user"})
    parsed = parse_rules(context)
    anonymize = next(entry for entry in parsed if entry.name == "anonymize-users")
    reset = next(entry for entry in parsed if entry.name == "reset-user-passwords")
    assert anonymize.sql == "UPDATE app_user SET email = CONCAT('user', id, '@example.invalid')"
    assert anonymize.requires_table == "app_user"
    assert reset.params["table"] == "app_user"


def test_the_driver_choices_are_defaults_and_the_targets_are_imposed() -> None:
    # The rule the spec settles on: impose a key when the sync's own work makes
    # the project's value wrong or dangerous; default it when the value is no
    # more wrong after the sync than before. MAIL_MAILER is a driver choice and
    # still imposed, because the sync just put production addresses on a
    # machine that must not send real mail. CACHE_STORE is a driver choice too
    # and is imposed for the opposite reason: the sync's own `optimize:clear`
    # runs `cache:clear` against whatever store is configured, which would
    # flush a memcached pointed off the machine. Nothing the sync runs acts on
    # the queue or the sessions, so those two are defaults.
    env = REGISTRY.resolve("laravel", None).data["env"]
    assert env["default"] == {"QUEUE_CONNECTION": "sync", "SESSION_DRIVER": "file"}
    # The cache key was renamed in Laravel 11; both names are imposed, or the
    # flush above stays live on the version that reads the other one.
    assert env["set"]["CACHE_STORE"] == "file"
    assert env["set"]["CACHE_DRIVER"] == "file"
    # A non-empty URL key outranks every discrete key beside it, so the pins
    # decide nothing unless the URL keys are imposed empty (an empty value is
    # ignored by Laravel's ConfigurationUrlParser).
    for url_key in ("REDIS_URL", "DB_URL", "DATABASE_URL"):
        assert env["set"][url_key] == "", f"{url_key} must be imposed empty"
    assert env["set"]["REDIS_HOST"] == "ddev-${DDEV_SITENAME}-redis"
    for key in ("APP_ENV", "APP_URL", "DB_HOST", "DB_DATABASE", "MAIL_MAILER", "MAIL_HOST"):
        assert key in env["set"], f"{key} must stay imposed"
    for key in ("QUEUE_CONNECTION", "SESSION_DRIVER"):
        assert key not in env["set"], f"{key} must no longer be imposed"


def test_a_production_url_in_the_env_file_is_emptied_not_kept() -> None:
    # A non-empty URL key outranks the discrete host, port and database keys,
    # so a .env copied from production would still reach production's Redis
    # and database through the pins. The profile blanks the URL keys; the
    # quoting of the line the project wrote is preserved.
    env = REGISTRY.resolve("laravel", None).data["env"]
    production = (
        'REDIS_URL="redis://:secret@redis.prod.example:6379"\n'
        "DB_URL=mysql://app:secret@db.prod.example/app\n"
        "DATABASE_URL=mysql://app:secret@db.prod.example/app\n"
    )
    assert patch_keys(production, env["set"]).splitlines()[:3] == [
        'REDIS_URL=""',
        "DB_URL=",
        "DATABASE_URL=",
    ]
