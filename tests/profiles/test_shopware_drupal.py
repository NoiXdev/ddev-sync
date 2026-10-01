from pathlib import Path
from typing import Any, cast

import pytest

from ddev_sync.profiles.registry import ProfileRegistry

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)


def rules(name: str) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", REGISTRY.resolve(name, None).data["sanitize"]["rules"])


def test_shopware_rewrites_the_sales_channel_domain_as_a_rule() -> None:
    rule = next(entry for entry in rules("shopware6") if entry["name"] == "local-domain")
    assert rule["operation"] == "search_replace"
    assert rule["table"] == "sales_channel_domain"
    assert rule["column"] == "url"
    assert "${DDEV_SITENAME}" in rule["to"]


def test_shopware_clears_the_mailer_settings_as_a_rule() -> None:
    rule = next(entry for entry in rules("shopware6") if entry["name"] == "clear-mailer")
    assert rule["operation"] == "clear_mail_config"
    assert rule["table"] == "system_config"
    assert any("mailerSettings" in key for key in rule["keys"])


def test_shopware_truncates_the_plugin_table_so_local_plugins_reinstall() -> None:
    assert any(
        entry["name"] == "clear-plugins" and "plugin" in entry["sql"]
        for entry in rules("shopware6")
    )


def test_shopware_reads_the_standard_database_variables() -> None:
    env_map = REGISTRY.resolve("shopware6", None).data["database"]["env_map"]
    assert env_map["name"] == "DATABASE_NAME"


def test_shopware_syncs_public_media() -> None:
    paths = REGISTRY.resolve("shopware6", None).data["files"]["paths"]
    assert {entry["local"] for entry in paths} == {"public/media/", "public/thumbnail/"}


def test_drupal_uses_drush_for_the_admin_and_cache() -> None:
    admin = next(entry for entry in rules("drupal") if entry["name"] == "local-admin")
    assert admin["driver"] == "drush"
    cache = next(entry for entry in rules("drupal") if entry["name"] == "flush-cache")
    assert "drush" in cache["command"]


def test_drupal_syncs_the_public_files_directory() -> None:
    paths = REGISTRY.resolve("drupal", None).data["files"]["paths"]
    assert {entry["local"] for entry in paths} == {"web/sites/default/files/"}


def test_drupal_clears_sessions_and_watchdog() -> None:
    names = {entry["name"] for entry in rules("drupal")}
    assert {"clear-sessions", "clear-watchdog"} <= names


def test_drupal_reads_credentials_explicitly_without_an_env_map() -> None:
    database = REGISTRY.resolve("drupal", None).data["database"]
    assert database["remote_credentials"] == "explicit"
    assert "env_map" not in database


@pytest.mark.parametrize("name", ["shopware6", "drupal"])
def test_neither_profile_declares_version_variants_yet(name: str) -> None:
    assert REGISTRY.available_majors(name) == ()
