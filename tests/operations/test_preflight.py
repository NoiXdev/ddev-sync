import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations import preflight
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.preflight import MIN_FREE_BYTES, PreflightOperation, PreflightReport
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
