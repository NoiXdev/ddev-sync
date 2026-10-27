from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import LOCAL_DB_TLS_OPTION
from ddev_sync.operations.sanitize import (
    SANITIZE_OPERATIONS,
    SanitizeOperation,
    SanitizeRule,
    active_rules,
    local_mysql_argv,
    parse_rules,
)
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


RULES: dict[str, Any] = {
    "sanitize": {
        "rules": [
            {"name": "clear-sessions", "sql": "TRUNCATE TABLE be_sessions"},
            {"name": "clear-log", "sql": "TRUNCATE TABLE sys_log"},
        ]
    }
}


def test_sql_rules_are_parsed_in_order(tmp_path: Path) -> None:
    rules = parse_rules(make_context(FakeCommandRunner(), tmp_path, **RULES))
    assert [rule.name for rule in rules] == ["clear-sessions", "clear-log"]
    assert rules[0].sql == "TRUNCATE TABLE be_sessions"
    assert rules[0].operation is None


def test_an_operation_rule_carries_its_extra_params(tmp_path: Path) -> None:
    config = {
        "sanitize": {
            "rules": [{"name": "local-admin", "operation": "create_admin", "user": "ddev"}]
        }
    }
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))[0]
    assert rule.operation == "create_admin"
    assert rule.params["user"] == "ddev"
    assert rule.sql is None


def test_a_rule_without_a_name_is_rejected_with_its_content(tmp_path: Path) -> None:
    config = {"sanitize": {"rules": [{"sql": "SELECT 1"}]}}
    with pytest.raises(OperationError) as caught:
        parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))
    message = str(caught.value)
    assert "name" in message
    assert "SELECT 1" in message


def test_a_rule_with_neither_sql_nor_operation_is_rejected(tmp_path: Path) -> None:
    config = {"sanitize": {"rules": [{"name": "empty"}]}}
    with pytest.raises(OperationError) as caught:
        parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))
    assert "empty" in str(caught.value)
    assert "sql" in str(caught.value)


def test_a_rule_with_both_sql_and_operation_is_rejected(tmp_path: Path) -> None:
    config = {
        "sanitize": {"rules": [{"name": "both", "sql": "SELECT 1", "operation": "flush_cache"}]}
    }
    with pytest.raises(OperationError) as caught:
        parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))
    assert "both" in str(caught.value)


def test_no_rules_at_all_is_an_empty_list(tmp_path: Path) -> None:
    assert parse_rules(make_context(FakeCommandRunner(), tmp_path)) == []


def test_sql_itself_is_placeholder_expanded(tmp_path: Path) -> None:
    # `sql:` used to bypass expand_value entirely - only the extra `params`
    # dict was expanded (see `_parse_rule`'s `expand_value(raw_params, ...)`),
    # so a placeholder written directly into raw SQL (needed for
    # ${WP_TABLE_PREFIX} in profiles/wordpress.yaml) would have been sent to
    # the database literally, as the text "${...}", instead of the real
    # table name.
    config = {
        "sanitize": {"rules": [{"name": "x", "sql": "UPDATE ${DDEV_SITENAME}_users SET a=1"}]}
    }
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))[0]
    assert rule.sql == "UPDATE acme_users SET a=1"


def sql_runner(count: int) -> FakeCommandRunner:
    runner = FakeCommandRunner()
    for _ in range(count):
        runner.script_prefix(["mysql"], stdout="")
    return runner


def test_every_sql_rule_runs_against_the_local_database(tmp_path: Path) -> None:
    runner = sql_runner(2)
    SanitizeOperation().execute(make_context(runner, tmp_path, **RULES), {})
    assert len(runner.calls) == 2
    assert "--host=ddev-acme-db" in runner.calls[0]


def test_rules_run_in_configured_order(tmp_path: Path) -> None:
    runner = sql_runner(2)
    SanitizeOperation().execute(make_context(runner, tmp_path, **RULES), {})
    assert "be_sessions" in " ".join(runner.calls[0])
    assert "sys_log" in " ".join(runner.calls[1])


def test_the_target_database_is_honoured(tmp_path: Path) -> None:
    runner = sql_runner(2)
    config = {**RULES, "database": {"target_database": "acme_local"}}
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert "acme_local" in runner.calls[0]


def test_a_failing_rule_names_the_rule(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], returncode=1, stderr="Table doesn't exist")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **RULES), {})
    message = str(caught.value)
    assert "clear-sessions" in message
    assert "Table doesn't exist" in message


def test_tolerate_missing_table_defaults_to_false(tmp_path: Path) -> None:
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **RULES))[0]
    assert rule.tolerate_missing_table is False


def test_tolerate_missing_table_swallows_errno_1146_and_the_next_rule_still_runs(
    tmp_path: Path,
) -> None:
    """The exact regression `tolerate_missing_table` exists to close: a
    TRUNCATE against a table that genuinely does not exist (e.g. a TYPO3
    project that never required the optional EXT:scheduler package) must not
    abort every rule after it - the Horizon lesson, repeated once in a base
    profile that applies to every project of that type rather than an
    opt-in feature.
    """
    config = {
        "sanitize": {
            "rules": [
                {
                    "name": "clear-scheduler-tasks",
                    "sql": "TRUNCATE TABLE tx_scheduler_task",
                    "tolerate_missing_table": True,
                },
                {"name": "clear-log", "sql": "TRUNCATE TABLE sys_log"},
            ]
        }
    }
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["mysql"],
        returncode=1,
        stderr="ERROR 1146 (42S02) at line 1: Table 'db.tx_scheduler_task' doesn't exist",
    )
    runner.script_prefix(["mysql"], returncode=0)
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert len(runner.calls) == 2


def test_tolerate_missing_table_still_raises_on_a_different_sql_error(tmp_path: Path) -> None:
    config = {
        "sanitize": {
            "rules": [
                {
                    "name": "clear-scheduler-tasks",
                    "sql": "TRUNCATE TABLE tx_scheduler_task",
                    "tolerate_missing_table": True,
                }
            ]
        }
    }
    runner = FakeCommandRunner()
    runner.script_prefix(["mysql"], returncode=1, stderr="ERROR 1045: Access denied")
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    message = str(caught.value)
    assert "clear-scheduler-tasks" in message
    assert "Access denied" in message


def test_tolerate_missing_table_is_accepted_on_an_operation_rule(tmp_path: Path) -> None:
    # Previously rejected at parse time, on the assumption that an
    # `operation` rule's failure could never be a missing-table error. That
    # assumption does not hold: `reset_passwords`, `clear_mail_config` and
    # `search_replace`'s sql driver all build and run SQL against a named
    # table exactly like a plain `sql:` rule does (operations/catalog.py).
    config = {
        "sanitize": {
            "rules": [
                {
                    "name": "x",
                    "operation": "reset_passwords",
                    "table": "fe_users",
                    "column": "password",
                    "value": "disabled",
                    "tolerate_missing_table": True,
                }
            ]
        }
    }
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))[0]
    assert rule.tolerate_missing_table is True


def test_tolerate_missing_table_on_an_operation_rule_actually_swallows_1146(
    tmp_path: Path,
) -> None:
    config = {
        "sanitize": {
            "rules": [
                {
                    "name": "reset-fe-passwords",
                    "operation": "reset_passwords",
                    "table": "fe_users",
                    "column": "password",
                    "value": "disabled",
                    "tolerate_missing_table": True,
                }
            ]
        }
    }
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["mysql"],
        returncode=1,
        stderr="ERROR 1146 (42S02) at line 1: Table 'db.fe_users' doesn't exist",
    )
    SanitizeOperation().execute(make_context(runner, tmp_path, **config), {})
    assert len(runner.calls) == 1


def test_requires_table_defaults_to_none(tmp_path: Path) -> None:
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **RULES))[0]
    assert rule.requires_table is None


def test_requires_table_is_recorded(tmp_path: Path) -> None:
    config = {
        "sanitize": {"rules": [{"name": "x", "sql": "TRUNCATE TABLE t", "requires_table": "t"}]}
    }
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))[0]
    assert rule.requires_table == "t"


def test_requires_table_is_placeholder_expanded(tmp_path: Path) -> None:
    config = {
        "sanitize": {
            "rules": [{"name": "x", "sql": "SELECT 1", "requires_table": "${DDEV_SITENAME}_users"}]
        }
    }
    rule = parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))[0]
    assert rule.requires_table == "acme_users"


def test_requires_table_on_an_operation_rule_is_rejected(tmp_path: Path) -> None:
    # Symmetric with tolerate_missing_table's own rejection just below: an
    # 'operation' rule with a 'table' param already declares its table
    # implicitly (Part 3's preflight probe reads both forms), so
    # 'requires_table' here would either be redundant or - worse - silently
    # ignored while a reader assumes it did something.
    config = {
        "sanitize": {"rules": [{"name": "x", "operation": "flush_cache", "requires_table": "t"}]}
    }
    with pytest.raises(OperationError) as caught:
        parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))
    message = str(caught.value)
    assert "x" in message
    assert "requires_table" in message


def test_an_unknown_operation_lists_what_is_available(tmp_path: Path) -> None:
    config = {"sanitize": {"rules": [{"name": "x", "operation": "teleport"}]}}
    with pytest.raises(OperationError) as caught:
        SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    assert "teleport" in str(caught.value)
    assert "clear_mail_config" in str(caught.value)


def test_a_registered_operation_is_dispatched_with_its_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[SanitizeRule] = []

    class Recorder:
        def run(self, _context: ExecutionContext, rule: SanitizeRule) -> None:
            seen.append(rule)

    monkeypatch.setitem(SANITIZE_OPERATIONS, "recorder", Recorder())
    config = {"sanitize": {"rules": [{"name": "r", "operation": "recorder", "extra": 7}]}}
    SanitizeOperation().execute(make_context(FakeCommandRunner(), tmp_path, **config), {})
    assert len(seen) == 1
    assert seen[0].params["extra"] == 7


def test_no_rules_means_no_commands(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    SanitizeOperation().execute(make_context(runner, tmp_path), {})
    assert runner.calls == []


def test_local_mysql_argv_without_sql_omits_execute(tmp_path: Path) -> None:
    argv = local_mysql_argv(make_context(FakeCommandRunner(), tmp_path), "db")
    assert "--execute" not in argv
    assert argv[-1] == "db"


def test_sql_is_passed_as_one_argument_not_shell_text(tmp_path: Path) -> None:
    argv = local_mysql_argv(
        make_context(FakeCommandRunner(), tmp_path),
        "db",
        sql="UPDATE t SET x = 'a; DROP TABLE y'",
    )
    assert argv[-1] == "UPDATE t SET x = 'a; DROP TABLE y'"


def test_the_local_mysql_argv_relaxes_tls_verification(tmp_path: Path) -> None:
    # Same reason as the import's own clients: the 11.8 client requires a
    # verified TLS connection, and against a DDEV MariaDB 10.x server - which
    # has no certificate - it refuses, so every sanitize rule's SQL failed to
    # connect. Asserted through the constant, whose comment carries the
    # measurements behind the option.
    argv = local_mysql_argv(make_context(FakeCommandRunner(), tmp_path), "db")
    assert LOCAL_DB_TLS_OPTION in argv


def _one_rule(tmp_path: Path, **rule: Any) -> SanitizeRule:
    config = {"sanitize": {"rules": [{"name": "r", **rule}]}}
    return parse_rules(make_context(FakeCommandRunner(), tmp_path, **config))[0]


def test_a_fake_rule_is_personal_data(tmp_path: Path) -> None:
    rule = _one_rule(tmp_path, operation="fake", table="users", columns={"mail": "email"})
    assert rule.is_personal_data is True


def test_a_marked_sql_rule_is_personal_data(tmp_path: Path) -> None:
    rule = _one_rule(tmp_path, sql="UPDATE users SET mail = ''", personal_data=True)
    assert rule.personal_data is True
    assert rule.is_personal_data is True


def test_an_unmarked_rule_is_protective(tmp_path: Path) -> None:
    rule = _one_rule(tmp_path, sql="TRUNCATE TABLE sys_log")
    assert rule.personal_data is False
    assert rule.is_personal_data is False


def test_reset_passwords_is_protective(tmp_path: Path) -> None:
    rule = _one_rule(tmp_path, operation="reset_passwords", table="users")
    assert rule.is_personal_data is False


def test_personal_data_must_be_a_boolean(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        _one_rule(tmp_path, sql="SELECT 1", personal_data="yes")
    assert "'r'" in str(caught.value)
    assert "personal_data" in str(caught.value)


def test_personal_data_only_on_sql_rules(tmp_path: Path) -> None:
    with pytest.raises(OperationError) as caught:
        _one_rule(tmp_path, operation="reset_passwords", table="users", personal_data=True)
    assert "'r'" in str(caught.value)
    assert "personal_data" in str(caught.value)


SWITCH_RULES: list[dict[str, Any]] = [
    {"name": "fake-users", "operation": "fake", "table": "fe_users", "columns": {"email": "email"}},
    {"name": "marked", "sql": "UPDATE t SET a = ''", "personal_data": True},
    {"name": "mail", "operation": "clear_mail_config", "table": "cfg", "key_column": "k"},
    {
        "name": "passwords",
        "operation": "reset_passwords",
        "table": "users",
        "column": "pw",
        "value": "x",
    },
    {"name": "unmarked", "sql": "TRUNCATE sys_log"},
]


def _switch_context(tmp_path: Path, runner: FakeCommandRunner, **sanitize: Any) -> ExecutionContext:
    return make_context(runner, tmp_path, sanitize={"rules": SWITCH_RULES, **sanitize})


def test_disable_sanitize_skips_only_personal_data_rules(tmp_path: Path) -> None:
    context = _switch_context(tmp_path, FakeCommandRunner(), disable_sanitize=True)
    assert [rule.name for rule in active_rules(context)] == ["mail", "passwords", "unmarked"]


def test_disable_sanitize_all_skips_everything(tmp_path: Path) -> None:
    context = _switch_context(
        tmp_path, FakeCommandRunner(), disable_sanitize=False, disable_sanitize_all=True
    )
    assert active_rules(context) == []


def test_no_switch_runs_every_rule(tmp_path: Path) -> None:
    context = _switch_context(tmp_path, FakeCommandRunner())
    assert [rule.name for rule in active_rules(context)] == [r["name"] for r in SWITCH_RULES]


@pytest.mark.parametrize("key", ["disable_sanitize", "disable_sanitize_all"])
def test_a_non_boolean_switch_is_refused(tmp_path: Path, key: str) -> None:
    context = _switch_context(tmp_path, FakeCommandRunner(), **{key: "yes"})
    with pytest.raises(OperationError) as caught:
        active_rules(context)
    assert f"sanitize.{key} must be true or false" in str(caught.value)


def test_the_sanitize_step_runs_only_active_rules(tmp_path: Path) -> None:
    runner = sql_runner(3)
    context = _switch_context(tmp_path, runner, disable_sanitize=True)
    SanitizeOperation().execute(context, {})
    assert runner.calls
    assert not any("fe_users" in " ".join(call) for call in runner.calls)
