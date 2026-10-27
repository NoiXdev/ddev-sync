import dataclasses
import os
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Provider, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.docs import doc_link
from ddev_sync.operations import preflight
from ddev_sync.operations.base import ExecutionContext, OperationError, discard_progress
from ddev_sync.operations.preflight import (
    MIN_FREE_BYTES,
    PreflightOperation,
    PreflightReport,
    verify_credentials,
)
from ddev_sync.transport.container import web_command
from ddev_sync.transport.fake import FakeCommandRunner
from ddev_sync.transport.runner import CommandResult


@pytest.fixture(autouse=True)
def no_container_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default both new probes to "not available", deterministically.

    `check()` probes the web container and the ssh agent on every call. A
    developer's machine really has /var/run/docker.sock and really has
    SSH_AUTH_SOCK set, so without this the same test passes locally and fails
    in CI (or the other way round) for reasons that have nothing to do with
    what it asserts. Tests that care about either probe override it.
    """
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", tmp_path / "absent" / "docker.sock")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


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
    assert (doc_link(caught.value) or "").endswith("#cannot-reach-the-host-over-ssh")


def test_a_missing_remote_path_names_it(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-no-path\n")
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert "/srv/app/" in str(caught.value)
    assert "does not exist" in str(caught.value)
    assert (doc_link(caught.value) or "").endswith("#deploy-path-does-not-exist")


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
    assert (doc_link(caught.value) or "").endswith("#not-enough-free-space")
    assert "2 GB" in message


# --- The credentials probe: doctor's only check of `database.credentials` --
#
# The table probe reads credentials only when a sanitize rule requires a
# table, so `profiles/generic.yaml` - `sanitize.rules: []` and
# `database.remote_credentials: explicit` with nothing writing
# `database.credentials` - got a green `doctor` and then died at `db.dump`.
# This probe runs for every profile, regardless of sanitize.


def _script_env_file(runner: FakeCommandRunner, contents: str) -> None:
    """Script the ssh round trip `_fetch_first_env_file` makes, as a success.

    Its real stdout is the matched path on its own first line, followed by
    the file's contents - `_fetch_first_env_file` partitions on the first
    newline to recover both.
    """
    runner.script_prefix(["ssh"], stdout=f"/var/www/.env\n{contents}")


def _script_env_file_missing(runner: FakeCommandRunner) -> None:
    """Script the ssh round trip as every candidate path missing.

    `_fetch_first_env_file`'s probe script ends `exit 9` when none of the
    candidates exist, which `result.ok` reports as failure regardless of the
    exact code.
    """
    runner.script_prefix(["ssh"], returncode=9)


def test_the_credentials_row_reports_the_database_a_sync_would_dump(tmp_path: Path) -> None:
    runner = healthy_runner()
    _script_env_file(
        runner,
        "DATABASE_NAME=app\nDATABASE_USER=deploy\n"
        "DATABASE_PASSWORD=s3cret\nDATABASE_HOST=db.internal\n"
        "DATABASE_PORT=3306\n",
    )
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.credentials_ok is True
    assert "deploy@db.internal:3306/app" in report.credentials_detail


def test_the_credentials_detail_never_carries_the_password(tmp_path: Path) -> None:
    # `render_doctor` writes this string to the terminal with no scrubbing
    # of its own - `context.secrets` protects the verbose command log, not
    # this detail string.
    runner = healthy_runner()
    _script_env_file(
        runner,
        "DATABASE_NAME=app\nDATABASE_USER=deploy\n"
        "DATABASE_PASSWORD=s3cret\nDATABASE_HOST=db.internal\n"
        "DATABASE_PORT=3306\n",
    )
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert "s3cret" not in report.credentials_detail


def test_a_credentials_failure_is_reported_not_raised(tmp_path: Path) -> None:
    # `check()` is documented never to raise, and `doctor` renders its whole
    # checklist only after `check()` returns.
    runner = healthy_runner()
    _script_env_file_missing(runner)
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.credentials_ok is False
    assert "no environment file found" in report.credentials_detail
    # The other rows still arrived, which is the point of not raising.
    assert report.ssh_ok is True


def test_a_long_credentials_failure_is_bounded_keeping_the_front(tmp_path: Path) -> None:
    # `_from_wp_cli`'s error embeds the remote's own `stderr` verbatim, with
    # no bound of its own - unlike `_from_config`'s authored messages, that
    # text is whatever the remote happened to print. The opening sentence,
    # which names what went wrong, must survive; reinstating `_clip` here
    # once cut it instead, keeping only the unredactable tail.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], returncode=1, stderr="x" * 400)
    context = make_context(runner, tmp_path, database={"remote_credentials": "wp_cli"})
    report = PreflightOperation().check(context)
    assert report.credentials_ok is False
    assert report.credentials_detail.startswith("could not read wp-config on web01.example")
    assert len(report.credentials_detail) <= 200
    assert report.credentials_detail.endswith("...")
    assert "database connection itself" not in report.credentials_detail


def test_a_multiline_credentials_failure_is_collapsed_to_one_line(tmp_path: Path) -> None:
    # A real remote failure prints several lines; `doctor`'s checklist has no
    # room for more than one per row, and a bare newline would break its
    # column alignment.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], returncode=1, stderr="first line\nsecond line")
    context = make_context(runner, tmp_path, database={"remote_credentials": "wp_cli"})
    report = PreflightOperation().check(context)
    assert report.credentials_ok is False
    assert "\n" not in report.credentials_detail
    assert "first line second line" in report.credentials_detail


def test_credentials_are_not_probed_when_ssh_failed(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=255, stderr="Permission denied (publickey).")
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.credentials_ok is None
    assert report.credentials_detail == ""


def test_an_explicit_source_needs_no_round_trip(tmp_path: Path) -> None:
    # `_from_config` reads the merged configuration; a `generic` project
    # with credentials set is green without touching the network.
    runner = healthy_runner()
    context = make_context(
        runner,
        tmp_path,
        database={
            "remote_credentials": "explicit",
            "credentials": {
                "name": "app",
                "user": "deploy",
                "password": "s3cret",
                "host": "db.internal",
                "port": "3306",
            },
        },
    )
    report = PreflightOperation().check(context)
    assert report.credentials_ok is True
    assert "s3cret" not in report.credentials_detail
    assert len(runner.calls) == 1  # only the connectivity probe


def test_an_explicit_source_with_nothing_set_is_red(tmp_path: Path) -> None:
    # The measured blind spot: `profiles/generic.yaml` defaults to
    # `explicit` and nothing writes `database.credentials`, and with
    # `sanitize.rules: []` no other probe ever read them.
    context = make_context(healthy_runner(), tmp_path, database={"remote_credentials": "explicit"})
    report = PreflightOperation().check(context)
    assert report.credentials_ok is False
    assert "database.credentials" in report.credentials_detail


# --- A real sync stops for the same reason `doctor` reports it --------------
#
# `check()` reports a bad credentials source without raising; `execute()`
# must actually stop the run, because every step after preflight reads the
# database. Gated on `db_selected`, since a files-only run reads no database
# and must not be refused over credentials it never needed.


def test_execute_refuses_a_database_run_whose_credentials_do_not_resolve(tmp_path: Path) -> None:
    runner = healthy_runner()
    _script_env_file_missing(runner)
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(
            make_context(runner, tmp_path), {"db_selected": True, "sanitize_selected": False}
        )
    assert "no environment file found" in str(caught.value)
    assert (doc_link(caught.value) or "").endswith("#no-environment-file-on-the-remote")


def test_execute_lets_a_files_only_run_through_without_credentials(tmp_path: Path) -> None:
    # Review Focus 1: this run never reads the database, so a credentials
    # problem is none of its business.
    runner = healthy_runner()
    _script_env_file_missing(runner)
    PreflightOperation().execute(
        make_context(runner, tmp_path), {"db_selected": False, "sanitize_selected": False}
    )


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
    # §8 item 3: `context_with_rules` is an `explicit` source, so the table
    # prefix cannot be what is wrong here - naming it would send the reader
    # the wrong way.
    assert "database.wp_table_prefix" not in message
    assert (doc_link(caught.value) or "").endswith("#required-table-is-missing")


def test_a_missing_table_names_the_prefix_override_for_a_wp_cli_source(tmp_path: Path) -> None:
    # On a `wp_cli` source the declared table name came from WordPress's own
    # `$table_prefix`, resolved over ssh - so when the resolved table does
    # not exist, the prefix itself is a real suspect and `doctor` should say
    # which key fixes it: `database.wp_table_prefix`.
    runner = healthy_runner()
    # wp-cli credentials read: name, user, password, host, table_prefix.
    runner.script_prefix(["ssh"], stdout="acme_prod\nacme\nsecret\ndb\nmxwp_\n")
    runner.script_prefix(["ssh"], stdout="wp_options\n")  # SHOW TABLES: no wp_users
    context = make_context(
        runner,
        tmp_path,
        database={"remote_credentials": "wp_cli"},
        sanitize={
            "rules": [
                {
                    "name": "anonymize-users",
                    "sql": "UPDATE x SET y=1",
                    "requires_table": "${WP_TABLE_PREFIX}users",
                }
            ]
        },
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "does not exist" in message
    assert "database.wp_table_prefix" in message


def test_a_lowercase_declared_table_matching_an_uppercase_remote_one_aborts_naming_both(
    tmp_path: Path,
) -> None:
    # Fix 2: MySQL/MariaDB with lower_case_table_names=1 (default on macOS
    # and Windows servers) would resolve this rule's SQL fine regardless of
    # case, so this must not be reported as "does not exist" - but it also
    # must not pass silently, because on a case-sensitive server (the Linux
    # default) the rule's literal table reference really will fail. Both
    # spellings must be named.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="Users\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [{"name": "anonymize-users", "sql": "UPDATE users SET x=1", "requires_table": "users"}],
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "'users'" in message
    assert "'Users'" in message
    assert "does not exist" not in message


def test_an_uppercase_declared_table_matching_a_lowercase_remote_one_aborts_naming_both(
    tmp_path: Path,
) -> None:
    # The other direction: the ratchet in test_all_profiles.py and the error
    # path above must not have a hidden case-sensitivity assumption baked in.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="users\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [{"name": "anonymize-users", "sql": "UPDATE Users SET x=1", "requires_table": "Users"}],
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "'Users'" in message
    assert "'users'" in message


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
    # Credentials (incl. table_prefix, for a wp_cli source) are memoised on
    # `context.cache`, so the ssh round trip that resolves
    # ${WP_TABLE_PREFIX} and the one `_remote_tables` makes for SHOW TABLES
    # share a single credentials fetch - three ssh calls total: the
    # connectivity probe, the (memoised) wp-cli credentials read, and
    # SHOW TABLES.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="acme_prod\nacme\nsecret\ndb\nwptest_\n")
    runner.script_prefix(["ssh"], stdout="wptest_users\n")
    context = make_context(
        runner,
        tmp_path,
        database={"remote_credentials": "wp_cli"},
        sanitize={
            "rules": [
                {
                    "name": "anonymize-users",
                    "sql": "UPDATE x SET y=1",
                    "requires_table": "${WP_TABLE_PREFIX}users",
                }
            ]
        },
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})
    assert len(runner.calls) == 3


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


# --- `verify_credentials`: the public seam `cli/init.py` probes a candidate
# `explicit` source through (fix round 1) ------------------------------------
#
# Distinct from the sanitize-table probe above even though both run `SHOW
# TABLES` over the same connection: this one exists to catch a password that
# merely looks complete, not to check a table exists.


def _explicit_context(
    runner: FakeCommandRunner, root: Path, password: str = "p"
) -> ExecutionContext:
    return make_context(
        runner,
        root,
        database={
            "remote_credentials": "explicit",
            "credentials": {
                "name": "db",
                "user": "u",
                "password": password,
                "host": "h",
                "port": "3306",
            },
        },
    )


def test_verify_credentials_passes_when_the_remote_accepts_them(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="users\n")
    verify_credentials(_explicit_context(runner, tmp_path))  # must not raise
    assert len(runner.calls) == 1


def test_verify_credentials_raises_when_the_remote_rejects_them(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=1, stderr="Access denied for user 'u'@'h'")
    with pytest.raises(OperationError) as caught:
        verify_credentials(_explicit_context(runner, tmp_path))
    assert "Access denied" in str(caught.value)


def test_verify_credentials_redacts_the_password_from_a_failure(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=1, stderr="auth failed with secret hunter2")
    with pytest.raises(OperationError) as caught:
        verify_credentials(_explicit_context(runner, tmp_path, password="hunter2"))
    message = str(caught.value)
    assert "hunter2" not in message
    assert "REDACTED" in message


def test_verify_credentials_raises_before_any_round_trip_when_a_value_is_missing(
    tmp_path: Path,
) -> None:
    # `_from_config` (the `explicit` source) refuses an empty value on its
    # own, with no network involved - `verify_credentials` must not mask that
    # earlier, cheaper failure behind a round trip that was never going to run.
    runner = FakeCommandRunner()
    context = make_context(
        runner,
        tmp_path,
        database={
            "remote_credentials": "explicit",
            "credentials": {"name": "db", "user": "", "password": "p", "host": "h", "port": "3306"},
        },
    )
    with pytest.raises(OperationError):
        verify_credentials(context)
    assert runner.calls == []


# --- `doctor` must say *why* the ssh check failed --------------------------
#
# A first real run printed `FAIL ssh connection` with only the host beside it.
# The cause was "Host key verification failed.", which sends a user looking at
# their key - the wrong trail. `_probe` already had ssh's stderr and
# `execute()` used it; `check()` threw it away.

# What OpenSSH really prints when a known host presents a different key,
# trimmed of nothing: the reason is its last two lines, not its first.
_CHANGED_HOST_KEY = """\
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!
Someone could be eavesdropping on you right now (man-in-the-middle attack)!
It is also possible that a host key has just been changed.
The fingerprint for the ED25519 key sent by the remote host is
SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG.
Please contact your system administrator.
Add correct host key in /home/ddev/.ssh/known_hosts to get rid of this message.
Offending ED25519 key in /home/ddev/.ssh/known_hosts:1
Host key for web01.example has changed and you have requested strict checking.
Host key verification failed.
"""


def failing_ssh(stderr: str, returncode: int = 255) -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=returncode, stderr=stderr)
    return runner


def test_a_report_built_without_ssh_detail_has_an_empty_one() -> None:
    # Defaulted like `web_detail`, so every construction that predates the
    # field keeps working.
    assert PreflightReport(True, True, 1).ssh_detail == ""


def test_check_names_the_host_when_ssh_works(tmp_path: Path) -> None:
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.ssh_detail == "web01.example"


def test_check_carries_the_reason_ssh_failed(tmp_path: Path) -> None:
    runner = failing_ssh("deploy@web01.example: Permission denied (publickey).")
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.ssh_ok is False
    assert report.ssh_detail == (
        "web01.example: deploy@web01.example: Permission denied (publickey)."
    )


def test_a_changed_host_key_is_condensed_to_one_line_that_says_so(tmp_path: Path) -> None:
    report = PreflightOperation().check(make_context(failing_ssh(_CHANGED_HOST_KEY), tmp_path))
    assert "\n" not in report.ssh_detail
    assert "has changed" in report.ssh_detail
    assert "Host key verification failed." in report.ssh_detail
    # The banner and the advice paragraph are noise on a checklist row.
    assert "@@@" not in report.ssh_detail
    assert "eavesdropping" not in report.ssh_detail


def test_a_connect_timeout_reports_ssh_own_words(tmp_path: Path) -> None:
    runner = failing_ssh("ssh: connect to host web01.example port 22: Operation timed out")
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.ssh_detail.endswith("Operation timed out")


def test_the_notice_ssh_prints_for_a_newly_trusted_host_is_not_the_reason(
    tmp_path: Path,
) -> None:
    # `accept-new` makes ssh print this on the first contact with a host. When
    # the connection then fails for another reason it comes first on stderr,
    # and reporting it as the cause would again send the user the wrong way.
    stderr = (
        "Warning: Permanently added 'web01.example' (ED25519) to the list of known hosts.\n"
        "deploy@web01.example: Permission denied (publickey).\n"
    )
    report = PreflightOperation().check(make_context(failing_ssh(stderr), tmp_path))
    assert "Permanently added" not in report.ssh_detail
    assert "Permission denied" in report.ssh_detail


def test_the_post_quantum_notice_is_not_the_reason_either(tmp_path: Path) -> None:
    # OpenSSH 10.1 and later, which the image ships, print this on stderr for
    # every connection to a server without a post-quantum key exchange - the
    # exact wording below is what 10.3 printed against such a server.
    stderr = (
        "Warning: Permanently added '[web01.example]:22' (ED25519) to the list of known hosts.\n"
        "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
        '** This session may be vulnerable to "store now, decrypt later" attacks.\n'
        "** The server may need to be upgraded. See https://openssh.com/pq.html\n"
        "deploy@web01.example: Permission denied (publickey).\n"
    )
    report = PreflightOperation().check(make_context(failing_ssh(stderr), tmp_path))
    assert report.ssh_detail == (
        "web01.example: deploy@web01.example: Permission denied (publickey)."
    )


def test_a_jump_hosts_unknown_key_is_reported_without_the_proxy_placeholder(
    tmp_path: Path,
) -> None:
    # Through `-J` ssh ends with a line about "UNKNOWN port 65535", the
    # placeholder for a connection that has no address of its own. It says
    # nothing, and left in it would push the real verdict out of the last two.
    stderr = "Host key verification failed.\nConnection closed by UNKNOWN port 65535\n"
    report = PreflightOperation().check(make_context(failing_ssh(stderr), tmp_path))
    assert report.ssh_detail == "web01.example: Host key verification failed."


def test_a_jump_host_that_times_out_is_reported_by_its_first_line(tmp_path: Path) -> None:
    # The other form the placeholder takes: an unreachable bastion makes the
    # outer ssh give up at the banner, and its second line only repeats that
    # with "UNKNOWN port 65535" where an address should be.
    stderr = (
        "Connection timed out during banner exchange\nConnection to UNKNOWN port 65535 timed out\n"
    )
    report = PreflightOperation().check(make_context(failing_ssh(stderr), tmp_path))
    assert report.ssh_detail == "web01.example: Connection timed out during banner exchange"


def test_a_changed_key_behind_a_jump_host_still_says_it_changed(tmp_path: Path) -> None:
    stderr = _CHANGED_HOST_KEY + "Connection closed by UNKNOWN port 65535\n"
    report = PreflightOperation().check(make_context(failing_ssh(stderr), tmp_path))
    assert "has changed" in report.ssh_detail
    assert report.ssh_detail.endswith("Host key verification failed.")


def test_a_failure_with_no_stderr_still_says_something(tmp_path: Path) -> None:
    report = PreflightOperation().check(make_context(failing_ssh("", returncode=1), tmp_path))
    assert report.ssh_detail == "web01.example: ssh failed without saying why"


def test_an_absurdly_long_stderr_line_is_cut_from_the_front_not_the_verdict(
    tmp_path: Path,
) -> None:
    # ssh's verdict is the end of its output, so when something has to go it is
    # the beginning that goes.
    stderr = "y" * 5000 + " Host key verification failed."
    report = PreflightOperation().check(make_context(failing_ssh(stderr), tmp_path))
    assert len(report.ssh_detail) <= 200
    assert report.ssh_detail.startswith("...")
    assert report.ssh_detail.endswith("Host key verification failed.")


def test_a_reason_exactly_at_the_limit_is_not_cut(tmp_path: Path) -> None:
    host = "web01.example: "
    reason = "y" * (200 - len(host))
    report = PreflightOperation().check(make_context(failing_ssh(reason), tmp_path))
    assert report.ssh_detail == host + reason


def test_execute_still_reports_the_full_stderr(tmp_path: Path) -> None:
    # The condensed form is for `doctor`'s one-line row only; the exception
    # a failed sync raises keeps every line ssh printed.
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(make_context(failing_ssh(_CHANGED_HOST_KEY), tmp_path), {})
    assert "eavesdropping" in str(caught.value)


# --- `doctor` must be able to report the sanitize-table probe too ----------
#
# `check()` is documented never to raise, so every one of these exercises the
# non-raising form directly through `check()` - the opposite of the `execute()`
# raising tests above, which is exactly the gap this closes: before this,
# `doctor` called `check()`, which never ran the sanitize-table probe at all,
# so the command whose whole job is "tell me whether a sync will work" stayed
# silent about a condition that will abort a real sync.


def test_check_reports_no_sanitize_tables_result_when_nothing_requires_a_table(
    tmp_path: Path,
) -> None:
    report = PreflightOperation().check(context_with_rules(healthy_runner(), tmp_path, []))
    assert report.sanitize_tables_ok is None
    assert report.sanitize_tables_detail == ""


def test_check_skips_the_sanitize_table_probe_when_ssh_itself_fails(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=255, stderr="Permission denied (publickey).")
    context = context_with_rules(
        runner, tmp_path, [{"name": "x", "sql": "TRUNCATE TABLE t", "requires_table": "t"}]
    )
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is None
    assert report.sanitize_tables_detail == ""
    # Only the one connectivity probe - no SHOW TABLES round trip against a
    # host that already failed to answer.
    assert len(runner.calls) == 1


def test_check_reports_sanitize_tables_ok_without_raising(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="sys_log\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [{"name": "clear-log", "sql": "TRUNCATE TABLE sys_log", "requires_table": "sys_log"}],
    )
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is True
    assert "1 required table present" in report.sanitize_tables_detail


def test_check_reports_a_missing_sanitize_table_without_raising(tmp_path: Path) -> None:
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
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is False
    assert "wp_users" in report.sanitize_tables_detail
    assert "anonymize-users" in report.sanitize_tables_detail


def test_check_reports_a_failed_table_listing_without_raising(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], returncode=1, stderr="Access denied for user 'u'@'h'")
    context = context_with_rules(
        runner, tmp_path, [{"name": "x", "sql": "TRUNCATE TABLE t", "requires_table": "t"}]
    )
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is False
    assert "Access denied" in report.sanitize_tables_detail


def test_check_reports_an_unparsable_sanitize_rule_without_raising(tmp_path: Path) -> None:
    # `parse_rules` itself can fail - a rule missing 'name' is rejected before
    # any table is compared - and `check()` must still return a report:
    # `doctor` renders its whole checklist only after `check()` returns, so a
    # raise here costs the user the ssh, deploy-path and disk-space rows at
    # exactly the moment they ran `doctor` to see them.
    context = context_with_rules(healthy_runner(), tmp_path, [{"sql": "TRUNCATE TABLE cache"}])
    report = PreflightOperation().check(context)
    assert report.ssh_ok is True
    assert report.remote_path_ok is True
    assert report.sanitize_tables_ok is False
    assert "missing 'name'" in report.sanitize_tables_detail


def test_check_reports_an_unresolvable_placeholder_without_raising(tmp_path: Path) -> None:
    # The other way `parse_rules` fails: it expands `${...}` at parse time, so
    # a `${WP_TABLE_PREFIX}` against a credentials source that has no prefix
    # to read (`env`, not `wp_cli`) raises inside parsing, before the table
    # listing this used to be the only guarded call.
    context = make_context(
        healthy_runner(),
        tmp_path,
        database={"remote_credentials": "env"},
        sanitize={
            "rules": [
                {
                    "name": "anonymize-users",
                    "sql": "UPDATE ${WP_TABLE_PREFIX}users SET user_email = 'x'",
                    "requires_table": "${WP_TABLE_PREFIX}users",
                }
            ]
        },
    )
    report = PreflightOperation().check(context)
    assert report.ssh_ok is True
    assert report.sanitize_tables_ok is False
    assert report.sanitize_tables_detail != ""


def test_a_preflight_report_defaults_to_no_sanitize_tables_result() -> None:
    # Every existing call site that builds a `PreflightReport` with just the
    # original three positional fields (tests and `_probe` alike) must keep
    # working - the two new fields must default to "nothing to report".
    report = PreflightReport(True, True, 50 * 1024**3)
    assert report.sanitize_tables_ok is None
    assert report.sanitize_tables_detail == ""


def test_check_reports_a_missing_docker_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the socket the sidecar cannot reach the web container at all,
    # and every setup command would fail after the dump is already imported.
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", tmp_path / "docker.sock")
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.web_ok is False
    assert "not mounted" in report.web_detail


def test_check_reports_a_mounted_but_unusable_docker_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The socket is root:docker 0660 on the host and a container user's group
    # membership does not carry over, so "present" and "usable" are different
    # questions with different fixes.
    socket = tmp_path / "docker.sock"
    socket.touch()
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", socket)
    monkeypatch.setattr(os, "access", lambda *_args, **_kwargs: False)
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.web_ok is False
    assert "group_add" in report.web_detail


def test_check_reports_a_web_container_that_does_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket = tmp_path / "docker.sock"
    socket.touch()
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", socket)
    runner = healthy_runner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="Error: No such container")
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.web_ok is False
    assert "ddev-acme-web" in report.web_detail


# The shape a real `doctor` run hit: `FAIL  web container  ddev-t3-13-ssg-web
# did not answer: ` with nothing after the colon. `docker exec` puts an OCI
# runtime failure - a workdir not in the image, a binary not on its PATH - on
# stdout with exit 127, and the row read stderr only.
def test_check_reports_a_web_container_whose_reason_came_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket = tmp_path / "docker.sock"
    socket.touch()
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", socket)
    runner = healthy_runner()
    runner.script_prefix(
        ["docker", "exec"],
        returncode=127,
        stdout='OCI runtime exec failed: exec: "true": executable file not found in $PATH\n',
    )
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.web_ok is False
    assert "executable file not found" in report.web_detail
    assert not report.web_detail.endswith(": ")


def test_check_reports_a_runner_that_raises_instead_of_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `check()` must not raise whatever the runner does: the CommandRunner
    # protocol says nothing about exceptions, so this cannot rely on
    # SubprocessRunner's own OSError handling.
    class RaisingRunner(FakeCommandRunner):
        def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
            if argv[0] == "docker":
                raise RuntimeError("daemon went away")
            return super().run(argv, **kwargs)

    socket = tmp_path / "docker.sock"
    socket.touch()
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", socket)
    runner = RaisingRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    report = PreflightOperation().check(make_context(runner, tmp_path))
    assert report.web_ok is False
    assert "daemon went away" in report.web_detail
    assert "ddev-acme-web" in report.web_detail


def test_check_reports_a_reachable_web_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket = tmp_path / "docker.sock"
    socket.touch()
    monkeypatch.setattr(preflight, "DOCKER_SOCKET", socket)
    runner = healthy_runner()
    runner.script_prefix(["docker", "exec"], stdout="")
    context = make_context(runner, tmp_path)
    report = PreflightOperation().check(context)
    assert report.web_ok is True
    assert "ddev-acme-web" in report.web_detail
    # The container asked is the web one and the directory is the project
    # root, not the docroot - `web_detail` is built from the project, not from
    # this argv, so only an argv assertion catches a probe aimed elsewhere.
    docker_calls = [argv for argv in runner.calls if argv[0] == "docker"]
    assert docker_calls == [
        tuple(web_command(context.project, ["true"], workdir=context.project.root))
    ]


def test_check_reports_an_unset_ssh_auth_sock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.agent_ok is False
    assert "ddev auth ssh" in report.agent_detail


def test_check_reports_an_ssh_auth_sock_that_is_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "gone"))
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.agent_ok is False
    assert "does not exist" in report.agent_detail


def test_check_reports_an_agent_socket_this_user_cannot_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The section 12 finding, as a check: the mounted directory holds `socket`
    # (0600, uid 501) beside `proxy-socket` (0666). Pointing at the first
    # works while developing as root and fails the moment the image runs as
    # a non-root user, which is how it does run.
    socket = tmp_path / "socket"
    socket.touch(mode=0o600)
    monkeypatch.setenv("SSH_AUTH_SOCK", str(socket))
    monkeypatch.setattr(os, "access", lambda *_args, **_kwargs: False)
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.agent_ok is False
    assert "proxy-socket" in report.agent_detail


def test_check_reports_a_usable_agent_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket = tmp_path / "proxy-socket"
    socket.touch(mode=0o666)
    monkeypatch.setenv("SSH_AUTH_SOCK", str(socket))
    report = PreflightOperation().check(make_context(healthy_runner(), tmp_path))
    assert report.agent_ok is True
    assert "proxy-socket" in report.agent_detail


def test_the_remote_table_probe_does_not_relax_tls(tmp_path: Path) -> None:
    # The local clients relax TLS verification because DDEV's own server either
    # has no certificate or a self-signed one. This probe is the opposite case:
    # it runs through ssh on the production host, with that host's client and
    # its own TLS policy, so carrying any such option across would weaken a
    # connection we do not own.
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="users\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [{"name": "wipe-users", "sql": "TRUNCATE TABLE users", "requires_table": "users"}],
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})
    # `ssh_command` shell-quotes the remote command into one argument, so the
    # probe's own flags live in that string rather than in the argv.
    probe = runner.calls[1][-1]
    assert "SHOW TABLES" in probe
    # The literal prefix, not `LOCAL_DB_TLS_OPTION`: `--skip-ssl` is a prefix of
    # the option the engine passes, so this one substring catches both
    # spellings. Asserting the constant would let the older, blunter `--skip-ssl`
    # reach the remote probe unnoticed - verified by injecting it, which the
    # constant-based assertion did not catch.
    assert "--skip-ssl" not in probe


def test_preflight_says_which_remote_round_trip_it_is_about_to_make(tmp_path: Path) -> None:
    """The progress lines exist so a hanging probe can be named.

    They are emitted *before* each round trip, not after: the point is to see
    the one that is still waiting, and the next line appearing is how a reader
    learns the previous one finished.
    """
    seen: list[str] = []
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    context = make_context(
        runner,
        tmp_path,
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
        sanitize={"rules": []},
    )
    context = dataclasses.replace(context, progress=seen.append)

    PreflightOperation().execute(context, {"db_selected": True, "sanitize_selected": True})

    assert seen == [
        "reaching deploy@web01.example",
        "reading the remote database credentials",
        "checking the tables the sanitize rules name",
    ]


def test_a_files_only_sync_only_announces_the_connection(tmp_path: Path) -> None:
    """The two database round trips do not happen, so they are not announced."""
    seen: list[str] = []
    context = dataclasses.replace(make_context(healthy_runner(), tmp_path), progress=seen.append)

    PreflightOperation().execute(context, {})

    assert seen == ["reaching deploy@web01.example"]


def test_preflight_announces_nothing_when_nobody_is_listening(tmp_path: Path) -> None:
    """A context built without a listener discards the lines, so an operation
    never has to ask whether anyone is watching - and `doctor`, the matrix
    cells and every test that builds a context without one are unaffected.

    The call still happens; it is the default that throws the text away. That
    is what keeps the check out of `preflight.py`.
    """
    context = make_context(healthy_runner(), tmp_path)
    assert context.progress is discard_progress

    PreflightOperation().execute(context, {})


def test_a_present_but_unreadable_path_is_reported_as_such(tmp_path: Path) -> None:
    """A directory that cannot be listed is the uid mismatch a docker endpoint
    makes likely: the mounts belong to the application's user, and a `user:`
    that does not match sees a path that exists and yields nothing.

    `remote_path_ok` stays true on purpose. The path *is* there, and folding
    the two together is what makes today's message misleading - it would send
    a reader to check the path they already typed correctly.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"{preflight._UNREADABLE}\n")

    report = PreflightOperation().check(make_context(runner, tmp_path))

    assert report.remote_path_ok is True
    assert report.path_readable is False


def test_a_sync_refuses_to_start_when_the_path_cannot_be_read(tmp_path: Path) -> None:
    """The probe detects the uid mismatch; the step has to act on it.

    Otherwise it resurfaces as a failure in the middle of the rsync, which is
    exactly the place the design says is the wrong one to find it.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"{preflight._UNREADABLE}\n")

    with pytest.raises(OperationError, match="cannot be read") as refused:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert (doc_link(refused.value) or "").endswith("#remote-path-is-not-readable")


def test_the_unreadable_path_message_names_the_setting_that_fixes_it(tmp_path: Path) -> None:
    """The advice has to name a setting that exists.

    An endpoint cannot take compose's `user:` at all - measured: a container
    started with a uid the image was not built for exits 255 with "No user
    exists for uid ...", because an sshd needs a passwd entry for the uid it
    serves. The session's uid arrives as DDEV_SYNC_UID instead, so that is
    what a reader must be sent to.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"{preflight._UNREADABLE}\n")

    with pytest.raises(OperationError, match="DDEV_SYNC_UID") as refusal:
        PreflightOperation().execute(make_context(runner, tmp_path), {})

    assert "user:" not in str(refusal.value)


def test_a_changed_host_key_on_a_docker_target_points_at_the_volume(tmp_path: Path) -> None:
    """`accept-new` refuses a changed key with a message about an attack.

    For this provider the overwhelmingly likely cause is a redeploy that
    dropped the endpoint's host-key volume, and nothing else in the output
    points there - the address in the message is the Docker host's own
    loopback, which is as unhelpful as an address can be.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"],
        returncode=255,
        stderr="@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n",
    )
    # The provider lives on the resolved target, not in the merged config:
    # `make_context`'s keyword arguments are config layers. `ResolvedTarget`
    # is a pydantic model, so it is copied with `model_copy`.
    context = make_context(runner, tmp_path)
    context = dataclasses.replace(
        context, target=context.target.model_copy(update={"provider": Provider.DOCKER})
    )

    with pytest.raises(OperationError, match="host-key volume") as refusal:
        PreflightOperation().execute(context, {})

    assert "redeploy" in str(refusal.value)
    assert (doc_link(refusal.value) or "").endswith("#host-key-changed")


def test_a_changed_host_key_on_a_server_target_says_nothing_about_volumes(
    tmp_path: Path,
) -> None:
    """A server's key changes for different reasons - a rebuilt host, or a
    connection that is not going where the reader thinks - and advice about a
    container volume would send them somewhere that does not exist."""
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"],
        returncode=255,
        stderr="@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n",
    )

    with pytest.raises(OperationError) as refusal:
        PreflightOperation().execute(make_context(runner, tmp_path), {})

    assert "volume" not in str(refusal.value)
    assert (doc_link(refusal.value) or "").endswith("#host-key-changed")


def test_an_ssh_failure_about_forwarding_says_what_to_turn_on(tmp_path: Path) -> None:
    """ProxyJump opens its hop with stdio forwarding, and a hardened sshd
    refuses it with a message that explains nothing to the reader."""
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"],
        returncode=255,
        stderr="channel 0: open failed: administratively prohibited: open failed\n",
    )

    with pytest.raises(OperationError, match="AllowTcpForwarding") as refusal:
        PreflightOperation().execute(make_context(runner, tmp_path), {})
    assert (doc_link(refusal.value) or "").endswith("#jump-host-refuses-to-forward")


@pytest.mark.parametrize(
    "stderr",
    [
        "Connection closed by remote host\n",
        "kex_exchange_identification: read: Connection reset by peer\n",
    ],
)
def test_a_closed_connection_names_rate_limits(tmp_path: Path, stderr: str) -> None:
    """A server that accepts and then drops the connection is usually a ban for
    too many recent logins, which no amount of key checking would reveal."""
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=255, stderr=stderr)

    with pytest.raises(OperationError) as refusal:
        PreflightOperation().execute(make_context(runner, tmp_path), {})

    message = str(refusal.value)
    assert "fail2ban" in message
    assert "waiting" in message
    assert "one connection per run" in message
    assert stderr.strip() in message
    assert (doc_link(refusal.value) or "").endswith("#connection-closed-by-the-remote-host")


def test_the_table_probe_verifies_the_server_certificate_by_default(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="sessions\n")
    verify_credentials(context_with_rules(runner, tmp_path, []))
    assert "ssl-verify-server-cert" not in runner.calls[-1][-1]


def test_the_table_probe_follows_the_projects_certificate_setting(tmp_path: Path) -> None:
    # Measured: the endpoint's MariaDB 11.4 client against Percona MySQL 8.4
    # failed here with "self-signed certificate in certificate chain".
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="sessions\n")
    context = context_with_rules(runner, tmp_path, [])
    context = make_context(
        runner,
        tmp_path,
        database={**context.merged.data["database"], "verify_server_cert": False},
    )
    verify_credentials(context)
    remote = shlex.split(runner.calls[-1][-1])
    assert "--loose-skip-ssl-verify-server-cert" in remote
    assert remote.index("--loose-skip-ssl-verify-server-cert") < remote.index("--execute")


def test_a_skipped_rules_table_is_not_required(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="other_table\n")
    context = make_context(
        runner,
        tmp_path,
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
        sanitize={
            "disable_sanitize": True,
            "rules": [
                {
                    "name": "fake-users",
                    "operation": "fake",
                    "table": "fe_users",
                    "columns": {"email": "email"},
                }
            ],
        },
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})


def _fake_rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "name": "fake-guests",
        "operation": "fake",
        "table": "reservations",
        "columns": {"email": "email", "name": "name"},
    }
    return {**rule, **overrides}


def _columns_runner(columns_stdout: str, tables: str = "reservations\n") -> FakeCommandRunner:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout=tables)  # SHOW TABLES
    runner.script_prefix(["ssh"], stdout=columns_stdout)  # information_schema.COLUMNS
    return runner


def test_a_missing_fake_column_stops_the_sync_before_the_dump(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\nreservations\tname\n")
    context = context_with_rules(
        runner, tmp_path, [_fake_rule(columns={"emial": "email", "name": "name"})]
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "fake-guests" in message
    assert "reservations" in message
    assert "emial" in message
    assert "Nothing was downloaded or imported." in message
    assert len(runner.calls) == 3  # probe, SHOW TABLES, one column query
    assert (doc_link(caught.value) or "").endswith("#column-named-by-a-fake-rule-is-missing")


def test_a_missing_key_column_stops_the_sync(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\nreservations\temail\n")
    context = context_with_rules(
        runner, tmp_path, [_fake_rule(key="uid", columns={"email": "email"})]
    )
    with pytest.raises(OperationError) as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    message = str(caught.value)
    assert "'uid'" in message
    assert "Nothing was downloaded or imported." in message


def test_present_columns_pass(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\nreservations\temail\nreservations\tname\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule()])
    PreflightOperation().execute(context, {"sanitize_selected": True})
    remote = shlex.split(runner.calls[-1][-1])
    query = remote[remote.index("--execute") + 1]
    assert "information_schema.COLUMNS" in query
    assert "TABLE_SCHEMA = DATABASE()" in query
    assert "'reservations'" in query
    assert "--skip-column-names" in remote


def test_column_names_are_compared_case_insensitively(tmp_path: Path) -> None:
    # MySQL and MariaDB never distinguish column names by case, so `Email` in
    # the schema satisfies a rule that says `email` (and the key the same way).
    runner = _columns_runner("reservations\tID\nreservations\tEmail\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule(columns={"email": "email"})])
    PreflightOperation().execute(context, {"sanitize_selected": True})


def test_the_missing_column_message_says_how_to_replace_a_profile_rule(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule(columns={"email": "email"})])
    with pytest.raises(OperationError, match=r"mode: replace"):
        PreflightOperation().execute(context, {"sanitize_selected": True})


def test_no_column_query_without_fake_rules(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="sys_log\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [{"name": "clear-log", "sql": "TRUNCATE TABLE sys_log", "requires_table": "sys_log"}],
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})
    assert not any("information_schema" in " ".join(call) for call in runner.calls)


def test_a_tolerated_missing_table_skips_the_column_query(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="other\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule(tolerate_missing_table=True)])
    PreflightOperation().execute(context, {"sanitize_selected": True})
    assert len(runner.calls) == 2


def test_a_failed_column_query_raises_with_the_redacted_stderr(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="reservations\n")
    runner.script_prefix(["ssh"], returncode=1, stderr="denied using password p")
    context = context_with_rules(runner, tmp_path, [_fake_rule()])
    with pytest.raises(OperationError, match="could not list columns") as caught:
        PreflightOperation().execute(context, {"sanitize_selected": True})
    assert "using password p" not in str(caught.value)


def test_doctor_reports_a_missing_column_on_the_sanitize_row(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule()])
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is False
    assert "email" in report.sanitize_tables_detail
    assert "fake-guests" in report.sanitize_tables_detail


def test_doctor_reports_a_failed_column_query_without_raising(tmp_path: Path) -> None:
    runner = healthy_runner()
    runner.script_prefix(["ssh"], stdout="reservations\n")
    runner.script_prefix(["ssh"], returncode=1, stderr="boom")
    context = context_with_rules(runner, tmp_path, [_fake_rule()])
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is False
    assert "boom" in report.sanitize_tables_detail


def test_doctor_counts_present_columns_as_ok(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\nreservations\temail\nreservations\tname\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule()])
    report = PreflightOperation().check(context)
    assert report.sanitize_tables_ok is True


def test_a_fake_rule_without_a_table_is_left_to_the_run(tmp_path: Path) -> None:
    runner = healthy_runner()
    context = context_with_rules(
        runner, tmp_path, [{"name": "fake-x", "operation": "fake", "columns": {"a": "name"}}]
    )
    PreflightOperation().execute(context, {"sanitize_selected": True})
    assert len(runner.calls) == 1  # nothing to list, nothing to query


def test_a_tolerated_fake_rule_whose_table_exists_is_still_column_checked(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\n")
    context = context_with_rules(runner, tmp_path, [_fake_rule(tolerate_missing_table=True)])
    with pytest.raises(OperationError, match="'email'"):
        PreflightOperation().execute(context, {"sanitize_selected": True})


def test_a_tolerated_missing_table_does_not_hide_another_rules_column_check(
    tmp_path: Path,
) -> None:
    runner = _columns_runner("reservations\tid\n")
    context = context_with_rules(
        runner,
        tmp_path,
        [
            _fake_rule(name="fake-gone", table="gone", tolerate_missing_table=True),
            _fake_rule(),
        ],
    )
    with pytest.raises(OperationError, match="fake-guests"):
        PreflightOperation().execute(context, {"sanitize_selected": True})


def test_a_malformed_disable_seeding_stops_the_sync_before_the_dump(tmp_path: Path) -> None:
    runner = _columns_runner("reservations\tid\nreservations\temail\n")
    context = make_context(
        runner,
        tmp_path,
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
        sanitize={"disable_seeding": "yes", "rules": [_fake_rule(columns={"email": "email"})]},
    )
    with pytest.raises(OperationError, match=r"sanitize\.disable_seeding must be true or false"):
        PreflightOperation().execute(context, {"sanitize_selected": True})
