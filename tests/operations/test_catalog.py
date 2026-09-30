from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.catalog import LOCAL_ADMIN_PASSWORD
from ddev_sync.operations.sanitize import SANITIZE_OPERATIONS, SanitizeOperation
from ddev_sync.transport.fake import FakeCommandRunner


def make_context(runner: FakeCommandRunner, root: Path, **config: Any) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=root),
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
        assume_yes=True,
    )


def rule_config(**rule: Any) -> dict[str, Any]:
    return {"sanitize": {"rules": [rule]}}


def test_every_catalog_operation_is_registered() -> None:
    assert sorted(SANITIZE_OPERATIONS) == [
        "clear_mail_config",
        "create_admin",
        "flush_cache",
        "reset_passwords",
        "search_replace",
    ]


def test_clear_mail_config_deletes_the_named_keys(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], stdout="")
    config = rule_config(
        name="mail",
        operation="clear_mail_config",
        table="system_config",
        key_column="configuration_key",
        keys=["core.mailerSettings.host", "core.mailerSettings.password"],
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    sql = runner.calls[0][-1]
    assert "DELETE FROM `system_config`" in sql
    assert "core.mailerSettings.host" in sql
    assert "core.mailerSettings.password" in sql


def test_clear_mail_config_with_no_keys_runs_nothing(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    config = rule_config(
        name="mail", operation="clear_mail_config", table="t", key_column="k", keys=[]
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert runner.calls == []


def test_clear_mail_config_escapes_quotes_in_a_key(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], stdout="")
    config = rule_config(
        name="mail",
        operation="clear_mail_config",
        table="t",
        key_column="k",
        keys=["it's.a.key"],
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "it''s.a.key" in runner.calls[0][-1]


def test_a_failing_sql_shaped_operation_is_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], returncode=1, stderr="Table doesn't exist")
    config = rule_config(
        name="pw",
        operation="reset_passwords",
        table="fe_users",
        column="password",
        value="x",
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "Table doesn't exist" in str(caught.value)


def test_reset_passwords_sets_the_configured_value(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], stdout="")
    config = rule_config(
        name="pw",
        operation="reset_passwords",
        table="fe_users",
        column="password",
        value="$argon2i$disabled",
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    sql = runner.calls[0][-1]
    assert "UPDATE `fe_users` SET `password` = '$argon2i$disabled'" in sql


def test_search_replace_updates_the_configured_column(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], stdout="")
    config = rule_config(
        name="domain",
        operation="search_replace",
        table="sales_channel_domain",
        column="url",
        **{"from": "https://acme.example", "to": "https://acme.ddev.site"},
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    sql = runner.calls[0][-1]
    assert "UPDATE `sales_channel_domain`" in sql
    assert "https://acme.ddev.site" in sql
    assert "WHERE `url` = 'https://acme.example'" in sql


def test_search_replace_without_from_rewrites_every_row(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], stdout="")
    config = rule_config(
        name="domain",
        operation="search_replace",
        table="t",
        column="url",
        to="https://acme.ddev.site",
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "WHERE" not in runner.calls[0][-1]


def test_search_replace_without_to_is_rejected(tmp_path: Path) -> None:
    config = rule_config(name="domain", operation="search_replace", table="t", column="c")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    assert "'to'" in str(caught.value)


@pytest.mark.parametrize("missing", ["table", "column"])
def test_search_replace_missing_a_required_param_names_it(tmp_path: Path, missing: str) -> None:
    params: dict[str, Any] = {"table": "t", "column": "c", "to": "x"}
    del params[missing]
    config = rule_config(name="domain", operation="search_replace", **params)
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    assert missing in str(caught.value)


@pytest.mark.parametrize(
    ("driver", "expected_head"),
    [
        ("typo3", ["vendor/bin/typo3", "backend:createadmin"]),
        ("console", ["vendor/bin/typo3cms", "backend:createadmin"]),
        ("wp_cli", ["wp", "user", "create"]),
        ("drush", ["vendor/bin/drush", "user:create"]),
    ],
)
def test_create_admin_uses_the_configured_driver(
    tmp_path: Path, driver: str, expected_head: list[str]
) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix([expected_head[0]], stdout="created")
    config = rule_config(name="admin", operation="create_admin", driver=driver, user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    assert list(argv[: len(expected_head)]) == expected_head
    assert "ddev" in argv


def test_create_admin_uses_the_documented_local_password(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["vendor/bin/typo3"], stdout="created")
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert LOCAL_ADMIN_PASSWORD in runner.calls[0]


def test_an_existing_admin_is_not_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["vendor/bin/typo3"], returncode=1, stderr="A user with this name already exists"
    )
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})


def test_another_create_admin_failure_is_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["vendor/bin/typo3"], returncode=1, stderr="database is gone")
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "database is gone" in str(caught.value)


def test_an_unknown_create_admin_driver_lists_the_valid_ones(tmp_path: Path) -> None:
    config = rule_config(name="admin", operation="create_admin", driver="magic", user="x")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    message = str(caught.value)
    assert "magic" in message
    assert "console, drush, typo3, wp_cli" in message


def test_flush_cache_runs_the_profile_supplied_command(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], stdout="")
    config = rule_config(
        name="cache", operation="flush_cache", command="vendor/bin/typo3 cache:flush"
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert runner.calls[0] == ("sh", "-c", "vendor/bin/typo3 cache:flush")


def test_flush_cache_runs_in_the_project_root(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], stdout="")
    config = rule_config(name="cache", operation="flush_cache", command="true")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert runner.invocations[0].cwd == tmp_path


def test_flush_cache_failures_are_tolerated(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], returncode=1, stderr="no cache to flush")
    config = rule_config(name="cache", operation="flush_cache", command="true")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})


def test_flush_cache_without_a_command_is_rejected(tmp_path: Path) -> None:
    config = rule_config(name="cache", operation="flush_cache")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    assert "command" in str(caught.value)


def test_search_replace_can_use_wp_cli(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["wp"], stdout="Success")
    config = rule_config(
        name="urls",
        operation="search_replace",
        driver="wp_cli",
        **{"from": "https://acme.example", "to": "https://acme.ddev.site"},
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert runner.calls[0][:2] == ("wp", "search-replace")
    assert runner.invocations[0].cwd == tmp_path / "public"


def test_a_failing_wp_search_replace_is_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["wp"], returncode=1, stderr="Error: no wp-config.php")
    config = rule_config(
        name="urls",
        operation="search_replace",
        driver="wp_cli",
        **{"from": "a", "to": "b"},
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "no wp-config.php" in str(caught.value)
