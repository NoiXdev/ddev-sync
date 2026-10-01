from pathlib import Path
from typing import Any, cast

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.sanitize import parse_rules
from ddev_sync.profiles.registry import ProfileRegistry
from ddev_sync.transport.fake import FakeCommandRunner

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)


def rules(name: str) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", REGISTRY.resolve(name, None).data["sanitize"]["rules"])


def context_for(name: str, tmp_path: Path) -> ExecutionContext:
    """A real `ExecutionContext` around one profile's data, so sanitize rule
    parameters go through the same `${...}` expansion a real run applies -
    unlike `rules()` above, which reads the profile's raw, unexpanded YAML.
    """
    profile = REGISTRY.resolve(name, None)
    return ExecutionContext(
        project=DdevProject(name="acme", type=name, docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc(f"profile:{name}", profile.data)]),
        runner=FakeCommandRunner(),
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


def test_shopware_rewrites_the_sales_channel_domain_as_a_rule() -> None:
    rule = next(entry for entry in rules("shopware6") if entry["name"] == "local-domain")
    assert rule["operation"] == "search_replace"
    assert rule["table"] == "sales_channel_domain"
    assert rule["column"] == "url"


def test_shopware_expands_ddev_sitename_in_the_domain_rewrite(tmp_path: Path) -> None:
    # Pins the fix for the bug this replaced: `rule.params["to"]` used to
    # carry the literal, unexpanded string "${DDEV_SITENAME}" straight into
    # the database - `expand_placeholders` only ever ran on the (dead)
    # env-rewrite path. A sync reported "sanitize OK" while every shop's
    # sales_channel_domain pointed at a URL that could never resolve.
    parsed = parse_rules(context_for("shopware6", tmp_path))
    rule = next(entry for entry in parsed if entry.name == "local-domain")
    assert rule.params["to"] == "https://acme.ddev.site"
    assert "${DDEV_SITENAME}" not in rule.params["to"]


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
