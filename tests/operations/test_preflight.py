from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.preflight import MIN_FREE_BYTES, PreflightOperation
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
    )


def healthy_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    return runner


def context_with_rules(
    runner: FakeCommandRunner, root: Path, rules: list[dict[str, Any]]
) -> ExecutionContext:
    return make_context(
        runner,
        root,
        database={
            "remote_credentials": "explicit",
            "credentials": {
                "name": "db",
                "user": "u",
                "password": "p",
                "host": "db.internal",
                "port": "3306",
            },
        },
        sanitize={"rules": rules},
    )


def test_a_healthy_remote_passes_without_raising(tmp_path: Path) -> None:
    PreflightOperation().execute(make_context(healthy_runner(), tmp_path), {})


def test_the_probe_runs_over_ssh_to_the_configured_endpoint(tmp_path: Path) -> None:
    runner = healthy_runner()
    PreflightOperation().execute(make_context(runner, tmp_path), {})
    argv = runner.calls[0]
    assert argv[0] == "ssh"
    assert "deploy@web01.example" in argv


def test_the_probe_checks_the_remote_path_in_the_same_round_trip(tmp_path: Path) -> None:
    runner = healthy_runner()
    PreflightOperation().execute(make_context(runner, tmp_path), {})
    remote_command = runner.calls[0][-1]
    assert "/srv/app/" in remote_command
    assert "ddev-sync-ok" in remote_command


def test_check_reports_everything_it_found(tmp_path: Path) -> None:
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.ssh_ok is True
    assert report.remote_path_ok is True
    assert report.free_bytes > 0


def test_a_refused_ssh_connection_explains_ddev_auth_ssh(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=255, stderr="Permission denied (publickey).")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    message = str(caught.value)
    assert "ddev auth ssh" in message
    assert "Permission denied" in message


def test_a_missing_remote_path_names_it(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-no-path\n")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert "/srv/app/" in str(caught.value)
    assert "does not exist" in str(caught.value)


def test_unexpected_probe_output_is_reported_verbatim(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="bash: line 1: syntax error\n")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert "syntax error" in str(caught.value)


def test_the_minimum_free_space_is_two_gigabytes() -> None:
    assert MIN_FREE_BYTES == 2 * 1024**3


def test_low_disk_space_is_refused_with_the_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeUsage:
        free = 1024

    monkeypatch.setattr(
        "ddev_sync.operations.preflight.shutil.disk_usage",
        lambda _path: _FakeUsage(),
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(healthy_runner(), tmp_path), {})
    message = str(caught.value)
    assert "0 MB free" in message
    assert "2 GB" in message


# --- Part 3: the sanitize-table probe ---------------------------------------


def test_sanitize_not_selected_skips_the_table_probe_entirely(tmp_path: Path) -> None:
    # The critical cost guarantee: a files-only sync (`--steps=files`) must
    # not pay for a database round trip, and must not be able to fail on a
    # database it never touches. `sanitize_selected` defaults to False.
    runner = healthy_runner()
    context = context_with_rules(
        runner,
        tmp_path,
        [{"name": "x", "sql": "TRUNCATE TABLE missing", "requires_table": "missing"}],
    )
    PreflightOperation().execute(context, {})
    assert len(runner.calls) == 1  # only the initial ssh probe


def test_sanitize_selected_with_no_rules_skips_the_table_probe(tmp_path: Path) -> None:
    runner = healthy_runner()
    context = context_with_rules(runner, tmp_path, [])
    PreflightOperation().execute(context, {"sanitize_selected": True})
    assert len(runner.calls) == 1  # no SHOW TABLES round trip when nothing requires a table


def test_sanitize_selected_passes_when_every_required_table_exists(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="be_sessions\nsys_log\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [
            {"name": "clear-log", "sql": "TRUNCATE TABLE sys_log", "requires_table": "sys_log"},
            # An operation rule with no 'table' param (nothing to check here)
            # must not be mistaken for one that names no table at all.
            {"name": "local-admin", "operation": "create_admin", "user": "ddev"},
        ],
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})


def test_sanitize_selected_aborts_before_the_dump_when_a_required_table_is_missing(
    tmp_path: Path,
) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="wp_options\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [
            {
                "name": "anonymize-users",
                "sql": "UPDATE wp_users SET x=1",
                "requires_table": "wp_users",
            }
        ],
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "wp_users" in message
    assert "anonymize-users" in message
    assert "tolerate_missing_table" in message
    # Nothing was downloaded or imported: only the two ssh round trips (the
    # connectivity probe, then SHOW TABLES) happened.
    assert len(runner.calls) == 2


def test_a_tolerated_rule_is_exempt_from_the_table_probe(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="\n")  # no tables at all on the remote
    context = context_with_rules(
        runner,
        tmp_path,
        [
            {
                "name": "clear-scheduler-tasks",
                "sql": "TRUNCATE TABLE tx_scheduler_task",
                "tolerate_missing_table": True,
            }
        ],
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})


def test_an_operation_rules_table_param_is_checked_too(tmp_path: Path) -> None:
    # 'operation' rules declare their table implicitly through their 'table'
    # param (e.g. reset_passwords) rather than 'requires_table' - the probe
    # must read both forms uniformly.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="other_table\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [
            {
                "name": "reset-user-passwords",
                "operation": "reset_passwords",
                "table": "users",
                "column": "password",
                "value": "x",
            }
        ],
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "users" in message
    assert "reset-user-passwords" in message


def test_a_placeholder_table_name_is_resolved_before_comparing(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["wp", "config", "get", "table_prefix"], stdout="wptest_\n")
    runner.script_prefix(["ssh"], stdout="wptest_users\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [
            {
                "name": "anonymize-users",
                "sql": "UPDATE x SET y=1",
                "requires_table": "${WP_TABLE_PREFIX}users",
            }
        ],
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})


def test_a_failed_table_listing_raises_with_the_remote_stderr(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], returncode=1, stderr="Access denied for user 'u'@'h'")
    context = context_with_rules(
        runner, tmp_path, [{"name": "x", "sql": "TRUNCATE TABLE t", "requires_table": "t"}]
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    assert "Access denied" in str(caught.value)


def test_the_remote_credentials_password_is_redacted_from_a_table_listing_failure(
    tmp_path: Path,
) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], returncode=1, stderr="auth failed with secret hunter2")
    context = make_context(
        runner,
        tmp_path,
        database={
            "remote_credentials": "explicit",
            "credentials": {
                "name": "db",
                "user": "u",
                "password": "hunter2",
                "host": "h",
                "port": "3306",
            },
        },
        sanitize={"rules": [{"name": "x", "sql": "TRUNCATE TABLE t", "requires_table": "t"}]},
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "hunter2" not in message
    assert "REDACTED" in message
