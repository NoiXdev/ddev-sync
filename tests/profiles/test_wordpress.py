from pathlib import Path
from typing import Any, cast

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext
from ddev_sync.operations.sanitize import parse_rules
from ddev_sync.profiles.registry import ProfileRegistry
from ddev_sync.transport.fake import FakeCommandRunner

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)


def rules() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], REGISTRY.resolve("wordpress", None).data["sanitize"]["rules"])


def context_for(tmp_path: Path) -> ExecutionContext:
    """A real `ExecutionContext` around the wordpress profile, so sanitize
    rule parameters go through `${...}` expansion the same way a real run
    does - unlike `rules()` above, which reads the raw, unexpanded YAML.

    `parse_rules` expands every configured rule, not just the one a given
    test is looking at, so the profile's `${WP_TABLE_PREFIX}` rules
    (anonymize-users, reset-user-passwords) make this read the prefix from
    production via wp-cli over ssh (the wordpress profile's
    `database.remote_credentials: wp_cli`) on every call here - scripted
    once, regardless of which rule a test actually asserts on.
    """
    profile = REGISTRY.resolve("wordpress", None)
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="acme_prod\nacme\nsecret\ndb\nwp_\n")
    return ExecutionContext(
        project=DdevProject(name="acme", type="wordpress", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers(
            [
                LayerDoc("profile:wordpress", profile.data),
                # `rewrite-site-url`'s `from` is now `${PRODUCTION_URL}`
                # (operations/placeholders.py), which `parse_rules` resolves
                # from `sanitize.production_url` for every rule it parses, not
                # only the one a given test asserts on - so a project layer
                # must supply it here exactly as a real sync's config would.
                LayerDoc("project", {"sanitize": {"production_url": "https://acme.example"}}),
            ]
        ),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
    )


def test_wordpress_does_not_touch_an_env_file() -> None:
    assert REGISTRY.resolve("wordpress", None).data["env"]["strategy"] == "none"


def test_wordpress_reads_credentials_through_wp_cli() -> None:
    database = REGISTRY.resolve("wordpress", None).data["database"]
    assert database["remote_credentials"] == "wp_cli"


def test_uploads_are_the_synced_directory() -> None:
    paths = REGISTRY.resolve("wordpress", None).data["files"]["paths"]
    assert {entry["local"] for entry in paths} == {"wp-content/uploads/"}


def test_the_url_rewrite_uses_wp_cli_not_sql() -> None:
    rule = next(entry for entry in rules() if entry["name"] == "rewrite-site-url")
    assert rule["driver"] == "wp_cli"


def test_the_url_rewrite_expands_ddev_sitename(tmp_path: Path) -> None:
    # Pins the same fix as shopware's equivalent test: `rule.params["to"]`
    # must carry the expanded URL, not the literal "${DDEV_SITENAME}" token.
    parsed = parse_rules(context_for(tmp_path))
    rule = next(entry for entry in parsed if entry.name == "rewrite-site-url")
    assert rule.params["to"] == "https://acme.ddev.site"
    assert "${DDEV_SITENAME}" not in rule.params["to"]


def test_anonymize_users_targets_the_resolved_table_prefix(tmp_path: Path) -> None:
    # Pins the Part 1/2 fix: profiles/wordpress.yaml used to hardcode
    # `wp_users`, which is wrong on any site with a non-default
    # `$table_prefix`. The fake rule's `table` must carry the *resolved*
    # prefix, not the literal placeholder text.
    parsed = parse_rules(context_for(tmp_path))
    rule = next(entry for entry in parsed if entry.name == "anonymize-users")
    assert rule.operation == "fake"
    assert rule.params["table"] == "wp_users"
    assert rule.params["key"] == "ID"


def test_reset_user_passwords_targets_the_resolved_table_prefix(tmp_path: Path) -> None:
    parsed = parse_rules(context_for(tmp_path))
    rule = next(entry for entry in parsed if entry.name == "reset-user-passwords")
    assert rule.params["table"] == "wp_users"


def test_the_admin_is_created_through_wp_cli() -> None:
    rule = next(entry for entry in rules() if entry["name"] == "local-admin")
    assert rule["driver"] == "wp_cli"


def test_the_placeholder_production_url_is_marked_so_nobody_ships_it() -> None:
    rule = next(entry for entry in rules() if entry["name"] == "rewrite-site-url")
    assert rule["from"] == "${PRODUCTION_URL}", (
        "The wordpress profile must not ship a literal production URL - it has "
        "no way to know one. If this assertion is failing because 'from' was "
        "replaced with a real URL, that URL does not belong in a shared "
        "profile; resolve it through 'sanitize.production_url' instead."
    )
