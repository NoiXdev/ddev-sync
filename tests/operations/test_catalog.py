from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.catalog import _ADMIN_DRIVERS, LOCAL_ADMIN_PASSWORD
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


def test_clear_mail_config_with_malformed_keys_is_rejected(tmp_path: Path) -> None:
    config = rule_config(
        name="mail",
        operation="clear_mail_config",
        table="t",
        key_column="k",
        keys="core.mailerSettings.host",
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    message = str(caught.value)
    assert "mail" in message
    assert "'keys'" in message


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


def test_a_tolerated_missing_table_is_swallowed_on_an_operation_rule(tmp_path: Path) -> None:
    # Part of fix 4: an operation rule (reset_passwords here) builds and runs
    # SQL against a named table exactly like a plain sql: rule, so the same
    # errno 1146 is just as possible - and must be just as tolerable.
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["mysql"],
        returncode=1,
        stderr="ERROR 1146 (42S02) at line 1: Table 'db.fe_users' doesn't exist",
    )
    config = rule_config(
        name="pw",
        operation="reset_passwords",
        table="fe_users",
        column="password",
        value="x",
        tolerate_missing_table=True,
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})


def test_tolerate_missing_table_still_raises_on_a_different_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], returncode=1, stderr="ERROR 1045: Access denied")
    config = rule_config(
        name="pw",
        operation="reset_passwords",
        table="fe_users",
        column="password",
        value="x",
        tolerate_missing_table=True,
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "Access denied" in str(caught.value)


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
        ("typo3", ["vendor/bin/typo3", "backend:user:create"]),
        ("console", ["vendor/bin/typo3cms", "backend:createadmin"]),
        ("wp_cli", ["wp", "user", "create"]),
        ("drush", ["vendor/bin/drush", "user:create"]),
    ],
)
def test_create_admin_uses_the_configured_driver(
    tmp_path: Path, driver: str, expected_head: list[str]
) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    if driver == "drush":
        # The only driver that goes on to a second command (the role).
        runner.script_prefix(["docker", "exec"], stdout="role added")
    config = rule_config(name="admin", operation="create_admin", driver=driver, user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    assert list(argv[argv.index("ddev-acme-web") + 1 :][: len(expected_head)]) == expected_head
    assert any(part in ("ddev", "--username=ddev") for part in argv)


def test_create_admin_uses_the_documented_local_password(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert f"--password={LOCAL_ADMIN_PASSWORD}" in runner.calls[0]


# The three framework-CLI failures in this module all read stderr alone, and
# `docker exec` puts an OCI runtime failure on stdout with exit 127 - which is
# exactly the shape of a web container without `wp` or `drush` installed. The
# message carried the rule name and then nothing.
_NOT_ON_PATH = 'OCI runtime exec failed: exec: "wp": executable file not found in $PATH\n'


def test_a_failed_create_admin_reports_a_reason_that_came_on_stdout(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stdout=_NOT_ON_PATH)
    config = rule_config(name="admin", operation="create_admin", driver="wp_cli", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "executable file not found" in str(caught.value)


def test_a_failed_role_grant_reports_a_reason_that_came_on_stdout(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    runner.script_prefix(["docker", "exec"], returncode=127, stdout=_NOT_ON_PATH)
    config = rule_config(name="admin", operation="create_admin", driver="drush", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    message = str(caught.value)
    assert "executable file not found" in message
    assert "role" in message


def test_an_existing_admin_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"],
        returncode=255,
        # Verbatim from `vendor/bin/typo3 backend:user:create` run twice
        # against a stock TYPO3 13.4.35.
        stderr='The username "ddev" is already taken. Please use another username.',
    )
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    out = capsys.readouterr().out
    assert "already exists" in out
    assert "ddev" in out


def test_an_existing_typo3_console_admin_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The `console` driver is typo3_console's `backend:createadmin`, whose
    # message differs from core's `backend:user:create` ("is already taken").
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"], returncode=1, stderr="A user with this name already exists"
    )
    config = rule_config(name="admin", operation="create_admin", driver="console", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "already exists" in capsys.readouterr().out


def test_core_typo3_does_not_tolerate_the_console_drivers_message(tmp_path: Path) -> None:
    # Core's command never says "already exists"; tolerating it there would
    # only risk swallowing an unrelated failure that happens to contain it.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="the table already exists")
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    with pytest.raises(OperationError):
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})


def test_an_existing_wp_cli_admin_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Verified against wp-cli/entity-command's src/User_Command.php::create()
    # (default branch `main`, checked 2026-10-01): `username_exists()` is
    # checked first and fails with exactly this text - WP-CLI's own
    # "duplicate user" never appears anywhere in that method.
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"], returncode=1, stderr="Error: The 'ddev' username is already registered."
    )
    config = rule_config(name="admin", operation="create_admin", driver="wp_cli", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    out = capsys.readouterr().out
    assert "already exists" in out
    assert "ddev" in out


def test_an_existing_drush_admin_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Verified against drush-ops/drush's src/Commands/user/UserCreateCommand.
    # php::doExecute() (default branch `14.x` - drush has no `main`; checked
    # 2026-10-01) and observed against drush 13.8.0 on Drupal 11.4.8, where a
    # second `user:create ddev` exits 1 and prints exactly this text on stderr
    # (stdout is empty): the account exists - again, never "duplicate user".
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"],
        returncode=1,
        stderr="There is already a user account with the name ddev",
    )
    runner.script_prefix(["docker", "exec"], stderr=" [success] Added administrator role to ddev")
    config = rule_config(name="admin", operation="create_admin", driver="drush", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    out = capsys.readouterr().out
    assert "already exists" in out
    assert "ddev" in out


def _drush_role_command(runner: FakeCommandRunner) -> list[str]:
    """The tokens after the container name of the drush driver's second command."""
    argv = runner.calls[1]
    return list(argv[argv.index("ddev-acme-web") + 1 :])


def test_the_drush_driver_gives_the_account_the_administrator_role(tmp_path: Path) -> None:
    # `user:create` has no role option, so the driver follows it with
    # `user:role:add <role> <name>` (role first, observed against drush 13.8.0).
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    runner.script_prefix(["docker", "exec"], stderr=" [success] Added administrator role to ddev")
    config = rule_config(name="admin", operation="create_admin", driver="drush", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert len(runner.calls) == 2
    assert _drush_role_command(runner) == [
        "vendor/bin/drush",
        "user:role:add",
        "administrator",
        "ddev",
    ]


def test_the_drush_role_is_a_rule_parameter(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    runner.script_prefix(["docker", "exec"], stdout="role added")
    config = rule_config(
        name="admin", operation="create_admin", driver="drush", user="ddev", role="site_admin"
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert _drush_role_command(runner)[2:] == ["site_admin", "ddev"]


def test_an_existing_drush_account_still_gets_the_role(tmp_path: Path) -> None:
    # The second sync of a project: the account is already there, and the role
    # must be (re)assigned all the same.
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"],
        returncode=1,
        stderr="There is already a user account with the name ddev",
    )
    runner.script_prefix(["docker", "exec"], stdout="role added")
    config = rule_config(name="admin", operation="create_admin", driver="drush", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert len(runner.calls) == 2
    assert _drush_role_command(runner) == [
        "vendor/bin/drush",
        "user:role:add",
        "administrator",
        "ddev",
    ]


def test_a_failing_drush_role_assignment_is_an_error(tmp_path: Path) -> None:
    # Verbatim from `drush user:role:add nosuchrole ddev` on drush 13.8.0 /
    # Drupal 11.4.8: exit 1, on stderr. An account without the role is exactly
    # what this step exists to prevent, so nothing is tolerated here.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    runner.script_prefix(
        ["docker", "exec"],
        returncode=1,
        stderr=" [error]  Unable to load the user_role: nosuchrole ",
    )
    config = rule_config(
        name="admin", operation="create_admin", driver="drush", user="ddev", role="nosuchrole"
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    message = str(caught.value)
    assert "nosuchrole" in message
    assert "Unable to load the user_role" in message
    # Actionable: it says the role must exist and names the one override route
    # there is (no per-rule override exists; the list has to be replaced).
    assert "must exist" in message
    assert "mode: replace" in message


@pytest.mark.parametrize("role", ["", "  "])
def test_an_empty_drush_role_is_rejected_before_anything_runs(tmp_path: Path, role: str) -> None:
    runner = FakeCommandRunner()
    config = rule_config(
        name="admin", operation="create_admin", driver="drush", user="ddev", role=role
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "'role'" in str(caught.value)
    assert runner.calls == []


def test_a_failed_drush_create_does_not_try_to_assign_the_role(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="Drush command terminated")
    config = rule_config(name="admin", operation="create_admin", driver="drush", user="ddev")
    with pytest.raises(OperationError):
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert len(runner.calls) == 1


@pytest.mark.parametrize("driver", ["typo3", "console", "wp_cli"])
def test_the_other_drivers_run_no_second_command(tmp_path: Path, driver: str) -> None:
    # Each of them makes an administrator in the one command it already runs.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    config = rule_config(name="admin", operation="create_admin", driver=driver, user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert len(runner.calls) == 1


def test_a_drivers_tolerate_marker_is_not_honoured_for_a_different_driver(
    tmp_path: Path,
) -> None:
    # Markers are scoped per driver (_ADMIN_DRIVERS already keys by driver):
    # typo3_console's "already exists" has never been observed from wp-cli, so
    # tolerating it there would narrow nothing and risk swallowing a real
    # wp-cli failure that happens to share the substring.
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"], returncode=1, stderr="some unrelated error: already exists"
    )
    config = rule_config(name="admin", operation="create_admin", driver="wp_cli", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "unrelated error" in str(caught.value)


def test_another_create_admin_failure_is_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="database is gone")
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "database is gone" in str(caught.value)


def test_an_unrelated_duplicate_key_error_is_not_swallowed(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["docker", "exec"],
        returncode=1,
        stderr="Duplicate entry 'admin' for key 'PRIMARY'",
    )
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "Duplicate entry" in str(caught.value)


def test_a_driver_without_tolerate_markers_still_fails_as_an_operation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_ADMIN_DRIVERS` and `_ALREADY_EXISTS` are two dicts keyed by the same
    # driver names, and only the first one decides whether a driver is known -
    # so a driver added to it alone must not take the tolerate-marker lookup
    # down with a bare KeyError traceback mid-sync. A driver whose markers are
    # simply unknown tolerates nothing, which is the safe direction: the
    # failure is reported, not swallowed.
    monkeypatch.setitem(_ADMIN_DRIVERS, "magic", ["vendor/bin/magic", "user:create"])
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="user already exists")
    config = rule_config(name="admin", operation="create_admin", driver="magic", user="ddev")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "already exists" in str(caught.value)


def test_an_unknown_create_admin_driver_lists_the_valid_ones(tmp_path: Path) -> None:
    config = rule_config(name="admin", operation="create_admin", driver="magic", user="x")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    message = str(caught.value)
    assert "magic" in message
    assert "console, drush, typo3, wp_cli" in message


def test_flush_cache_runs_the_profile_supplied_command(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    config = rule_config(
        name="cache", operation="flush_cache", command="vendor/bin/typo3 cache:flush"
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert runner.calls[0][-3:] == ("sh", "-c", "vendor/bin/typo3 cache:flush")


def test_flush_cache_runs_in_the_project_root(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    config = rule_config(name="cache", operation="flush_cache", command="true")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    assert argv[argv.index("-w") + 1] == "/var/www/html"


def test_flush_cache_failures_are_tolerated(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="no cache to flush")
    config = rule_config(name="cache", operation="flush_cache", command="true")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})


def test_flush_cache_without_a_command_is_rejected(tmp_path: Path) -> None:
    config = rule_config(name="cache", operation="flush_cache")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    assert "command" in str(caught.value)


def test_search_replace_can_use_wp_cli(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="Success")
    config = rule_config(
        name="urls",
        operation="search_replace",
        driver="wp_cli",
        **{"from": "https://acme.example", "to": "https://acme.ddev.site"},
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    assert argv[argv.index("ddev-acme-web") + 1 :][:2] == ("wp", "search-replace")
    assert argv[argv.index("-w") + 1] == "/var/www/html/public"


def test_a_failing_wp_search_replace_is_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="Error: no wp-config.php")
    config = rule_config(
        name="urls",
        operation="search_replace",
        driver="wp_cli",
        **{"from": "a", "to": "b"},
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "no wp-config.php" in str(caught.value)


def test_a_failing_wp_search_replace_reports_a_reason_that_came_on_stdout(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stdout=_NOT_ON_PATH)
    config = rule_config(
        name="urls",
        operation="search_replace",
        driver="wp_cli",
        **{"from": "a", "to": "b"},
    )
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "executable file not found" in str(caught.value)


def test_create_admin_runs_the_framework_cli_in_the_web_container(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="created")
    config = rule_config(name="admin", operation="create_admin", driver="typo3", user="ddev")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    assert argv[:2] == ("docker", "exec")
    assert "ddev-acme-web" in argv
    assert argv[argv.index("ddev-acme-web") + 1 :] == (
        "vendor/bin/typo3",
        "backend:user:create",
        "--username=ddev",
        f"--password={LOCAL_ADMIN_PASSWORD}",
        "--admin",
        "--no-interaction",
    )


def test_wp_search_replace_runs_in_the_web_container_from_the_docroot(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="Success")
    config = rule_config(
        name="urls",
        operation="search_replace",
        driver="wp_cli",
        **{"from": "https://acme.test", "to": "https://acme.ddev.site"},
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    # wp-cli has to run from the docroot, where wp-config.php is; that was
    # `cwd=` before and is docker's `-w` now.
    assert argv[argv.index("-w") + 1] == "/var/www/html/public"
    assert argv[argv.index("ddev-acme-web") + 1] == "wp"


def test_flush_cache_runs_in_the_web_container(tmp_path: Path) -> None:
    # `flush_cache` runs a framework cache command, exactly like a setup
    # entry does, so it belongs in the same place.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout="")
    config = rule_config(
        name="cache", operation="flush_cache", command="vendor/bin/typo3 cache:flush"
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    argv = runner.calls[0]
    assert argv[:2] == ("docker", "exec")
    assert argv[-3:] == ("sh", "-c", "vendor/bin/typo3 cache:flush")


def test_a_sql_rule_still_runs_in_the_sidecar(tmp_path: Path) -> None:
    # The split this task rests on: `mariadb-client` is in the sidecar
    # image, so SQL must NOT be routed into the web container, which has no
    # reason to carry a mysql client at all.
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], stdout="")
    config = rule_config(name="wipe", sql="TRUNCATE TABLE cache")
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert runner.calls[0][0] == "mysql"
