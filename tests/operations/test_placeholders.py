from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.placeholders import expand_placeholders, expand_value
from ddev_sync.transport.fake import FakeCommandRunner


def make_context(runner: FakeCommandRunner, root: Path, **config: Any) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="wordpress", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
    )


def test_ddev_sitename_is_expanded(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("ddev-${DDEV_SITENAME}-db", context) == "ddev-acme-db"


def test_sync_target_database_defaults_to_db(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("${SYNC_TARGET_DATABASE}", context) == "db"


def test_sync_target_database_honours_the_config_override(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(), tmp_path, database={"target_database": "acme_local"}
    )
    assert expand_value("${SYNC_TARGET_DATABASE}", context) == "acme_local"


def test_a_non_string_value_passes_through_unchanged(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value(7, context) == 7
    assert expand_value(None, context) is None
    assert expand_value(True, context) is True


def test_a_list_and_a_dict_are_expanded_recursively(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value(["${DDEV_SITENAME}", 1], context) == ["acme", 1]
    assert expand_value({"a": "${DDEV_SITENAME}"}, context) == {"a": "acme"}


def test_an_unknown_placeholder_is_left_alone(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("${NOT_A_PLACEHOLDER}", context) == "${NOT_A_PLACEHOLDER}"


# --- ${WP_TABLE_PREFIX} -----------------------------------------------------


def test_wp_table_prefix_is_read_via_wp_cli_when_no_override_is_set(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["wp", "config", "get", "table_prefix"], stdout="wp_\n")
    context = make_context(runner, tmp_path)
    assert expand_value("${WP_TABLE_PREFIX}users", context) == "wp_users"
    assert runner.invocations[0].cwd == context.project.docroot_path


def test_wp_table_prefix_is_not_queried_when_a_config_override_is_set(tmp_path: Path) -> None:
    # FakeCommandRunner has no scripted commands at all here - if the override
    # were ignored and 'wp' shelled out anyway, this would raise
    # UnscriptedCommand instead of returning the configured value.
    context = make_context(FakeCommandRunner(), tmp_path, database={"wp_table_prefix": "custom_"})
    assert expand_value("${WP_TABLE_PREFIX}users", context) == "custom_users"


def test_wp_table_prefix_failure_names_the_command_and_the_override_key(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["wp", "config", "get", "table_prefix"], returncode=1, stderr="wp-config.php not found"
    )
    context = make_context(runner, tmp_path)
    with pytest.raises(OperationError) as caught:
        expand_value("${WP_TABLE_PREFIX}users", context)
    message = str(caught.value)
    assert "wp config get table_prefix" in message
    assert "database.wp_table_prefix" in message
    assert "wp-config.php not found" in message


def test_wp_table_prefix_empty_output_is_also_a_failure(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["wp", "config", "get", "table_prefix"], stdout="   \n")
    context = make_context(runner, tmp_path)
    with pytest.raises(OperationError) as caught:
        expand_value("${WP_TABLE_PREFIX}users", context)
    message = str(caught.value)
    assert "wp config get table_prefix" in message
    assert "database.wp_table_prefix" in message


def test_wp_table_prefix_resolves_only_once_per_context_across_many_expansions(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["wp", "config", "get", "table_prefix"], stdout="wp_\n")
    context = make_context(runner, tmp_path)
    assert expand_value("${WP_TABLE_PREFIX}users", context) == "wp_users"
    assert expand_value("${WP_TABLE_PREFIX}options", context) == "wp_options"
    assert len(runner.calls) == 1


def test_a_value_with_no_wp_placeholder_never_shells_out_to_wp(tmp_path: Path) -> None:
    # The laziness guarantee this whole module exists for: a TYPO3 (or any
    # other non-WordPress) profile's sql/params never mention
    # ${WP_TABLE_PREFIX}, so resolving them must never invoke 'wp' - an eager
    # resolver would shell out on every sync, for every profile, regardless of
    # whether anything actually asked for it. FakeCommandRunner has no
    # scripted commands, so any attempted call raises UnscriptedCommand.
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("TRUNCATE TABLE be_sessions", context) == "TRUNCATE TABLE be_sessions"


# --- ${SYMFONY_USER_TABLE} ---------------------------------------------------


def test_symfony_user_table_defaults_to_user(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("${SYMFONY_USER_TABLE}", context) == "user"


def test_symfony_user_table_is_overridable(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(), tmp_path, database={"symfony_user_table": "app_user"}
    )
    assert expand_value("${SYMFONY_USER_TABLE}", context) == "app_user"


# --- expand_placeholders (flat mapping) -------------------------------------


def test_expand_placeholders_still_works_as_a_flat_mapping(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_placeholders({"DB_HOST": "ddev-${DDEV_SITENAME}-db"}, context) == {
        "DB_HOST": "ddev-acme-db"
    }


def test_expand_placeholders_of_an_empty_mapping_is_empty(tmp_path: Path) -> None:
    assert expand_placeholders({}, make_context(FakeCommandRunner(), tmp_path)) == {}
