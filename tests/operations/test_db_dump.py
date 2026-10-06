from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    DUMP_FILENAME,
    DbDumpOperation,
    RemoteCredentials,
    dump_is_complete,
    read_remote_credentials,
)
from ddev_sync.transport.fake import FakeCommandRunner

ENV_PROFILE: dict[str, Any] = {
    "env": {"file": [".env.local", ".env"]},
    "database": {
        "remote_credentials": "env",
        "env_map": {
            "name": "TYPO3_DB_NAME",
            "user": "TYPO3_DB_USER",
            "password": "TYPO3_DB_PASSWORD",
            "host": "TYPO3_DB_HOST",
            "port": "TYPO3_DB_PORT",
        },
    },
}

REMOTE_ENV = (
    "TYPO3_DB_NAME=acme_prod\n"
    "TYPO3_DB_USER=acme\n"
    "TYPO3_DB_PASSWORD=s3cret\n"
    "TYPO3_DB_HOST=127.0.0.1\n"
    "TYPO3_DB_PORT=3306\n"
)


def make_context(
    runner: FakeCommandRunner,
    root: Path,
    config: dict[str, Any] | None = None,
    *,
    layout: Layout = Layout.PLAIN,
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
            layout=layout,
        ),
        merged=merge_layers([LayerDoc("profile", config if config is not None else ENV_PROFILE)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
    )


def env_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env.local\n{REMOTE_ENV}")
    return runner


def test_credentials_are_read_from_the_remote_env_file(tmp_path: Path) -> None:
    credentials = read_remote_credentials(make_context(env_runner(), tmp_path))
    assert credentials == RemoteCredentials(
        name="acme_prod", user="acme", password="s3cret", host="127.0.0.1", port="3306"
    )


def test_the_password_is_redacted_in_the_repr() -> None:
    credentials = RemoteCredentials(name="n", user="u", password="s3cret", host="h", port="3306")
    assert "s3cret" not in repr(credentials)
    assert "REDACTED" in repr(credentials)


def test_the_env_candidates_are_probed_in_order(tmp_path: Path) -> None:
    runner = env_runner()
    read_remote_credentials(make_context(runner, tmp_path))
    probe = runner.calls[0][-1]
    assert probe.index("/srv/app/.env.local") < probe.index("/srv/app/.env ]")


def test_the_env_candidates_are_probed_under_the_data_root_of_a_plain_target(
    tmp_path: Path,
) -> None:
    runner = env_runner()
    read_remote_credentials(make_context(runner, tmp_path))
    probe = runner.calls[0][-1]
    assert "/srv/app/.env.local" in probe
    assert "/srv/app/shared/" not in probe


def test_the_env_candidates_are_probed_under_the_data_root_of_a_deployer_target(
    tmp_path: Path,
) -> None:
    runner = env_runner()
    read_remote_credentials(make_context(runner, tmp_path, layout=Layout.DEPLOYER))
    probe = runner.calls[0][-1]
    assert "/srv/app/shared/.env.local" in probe
    assert "/srv/app/shared/.env ]" in probe


def test_a_quoted_value_is_unquoted(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"],
        stdout=(
            "/srv/app/.env\n"
            'TYPO3_DB_NAME="acme_prod"\n'
            "TYPO3_DB_USER='acme'\n"
            "TYPO3_DB_PASSWORD=s3cret\n"
            "TYPO3_DB_HOST=127.0.0.1\n"
            "TYPO3_DB_PORT=3306\n"
        ),
    )
    credentials = read_remote_credentials(make_context(runner, tmp_path))
    assert credentials.name == "acme_prod"
    assert credentials.user == "acme"


def test_a_missing_key_names_the_variable_and_the_file(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="/srv/app/.env\nTYPO3_DB_NAME=acme_prod\n")
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(runner, tmp_path))
    message = str(caught.value)
    assert "TYPO3_DB_USER" in message
    assert "/srv/app/.env" in message


def test_a_key_present_but_empty_names_the_variable_and_the_file(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(
        ["ssh"],
        stdout=(
            "/srv/app/.env\n"
            "TYPO3_DB_NAME=acme_prod\n"
            "TYPO3_DB_USER=acme\n"
            "TYPO3_DB_PASSWORD=\n"
            "TYPO3_DB_HOST=127.0.0.1\n"
            "TYPO3_DB_PORT=3306\n"
        ),
    )
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(runner, tmp_path))
    message = str(caught.value)
    assert "TYPO3_DB_PASSWORD" in message
    assert "/srv/app/.env" in message


def test_explicit_credentials_come_from_the_config_without_ssh(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    config = {
        "database": {
            "remote_credentials": "explicit",
            "credentials": {
                "name": "mydb",
                "user": "myuser",
                "password": "mypass",
                "host": "db.internal",
                "port": "3307",
            },
        }
    }
    credentials = read_remote_credentials(make_context(runner, tmp_path, config))
    assert credentials.name == "mydb"
    assert runner.calls == []


def test_explicit_credentials_missing_a_key_are_reported(tmp_path: Path) -> None:
    config = {"database": {"remote_credentials": "explicit", "credentials": {"name": "mydb"}}}
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(FakeCommandRunner(), tmp_path, config))
    message = str(caught.value)
    assert "user" in message
    # The message used to send people to `.ddev/ddev-sync.yaml`, the file the
    # add-on commits - so the engine was telling them to put a production
    # password into git. The shipped template says `.local.yaml` for exactly
    # that reason, and the committed name must not appear here at all.
    assert ".ddev/ddev-sync.local.yaml" in message
    assert ".ddev/ddev-sync.yaml" not in message


def test_an_unknown_credentials_source_lists_the_valid_ones(tmp_path: Path) -> None:
    config = {"database": {"remote_credentials": "telepathy"}}
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(FakeCommandRunner(), tmp_path, config))
    message = str(caught.value)
    assert "telepathy" in message
    assert "env, explicit, wp_cli" in message


def test_blank_and_comment_lines_are_skipped_while_parsing_the_env_file(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    spaced = (
        "\n".join(
            [
                "# a comment explaining the block",
                "",
                "TYPO3_DB_NAME=acme_prod",
                "TYPO3_DB_USER=acme",
                "TYPO3_DB_PASSWORD=s3cret",
                "TYPO3_DB_HOST=127.0.0.1",
                "TYPO3_DB_PORT=3306",
            ]
        )
        + "\n"
    )
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env.local\n{spaced}")
    credentials = read_remote_credentials(make_context(runner, tmp_path))
    assert credentials.name == "acme_prod"


def test_no_environment_file_is_found_on_the_remote(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=9, stderr="")
    with pytest.raises(OperationError) as caught:
        read_remote_credentials(make_context(runner, tmp_path))
    message = str(caught.value)
    assert "no environment file found" in message
    assert "/srv/app/.env.local" in message
    assert "/srv/app/.env" in message


def test_a_single_configured_env_file_is_treated_as_one_candidate(tmp_path: Path) -> None:
    config = {
        **ENV_PROFILE,
        "env": {"file": ".env"},
    }
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env\n{REMOTE_ENV}")
    credentials = read_remote_credentials(make_context(runner, tmp_path, config))
    assert credentials.name == "acme_prod"


def test_missing_env_file_configuration_falls_back_to_defaults(tmp_path: Path) -> None:
    config = {"database": ENV_PROFILE["database"]}
    runner = env_runner()
    credentials = read_remote_credentials(make_context(runner, tmp_path, config))
    assert credentials.name == "acme_prod"
    probe = runner.calls[0][-1]
    assert "/srv/app/.env.local" in probe
    assert "/srv/app/.env" in probe


def test_the_generated_probe_script_is_pinned_exactly(tmp_path: Path) -> None:
    """Pins the fix for `fi if`: a mutation reverting `"; ".join(` back to
    `" ".join(` survived the entire unit suite (691 tests, 100% branch
    coverage) because `FakeCommandRunner` never starts a shell - only the
    integration suite's real shell caught the resulting syntax error, on its
    first run. Every other test here asserts substrings, which cannot see a
    missing separator between two `if` blocks; this is the one site that has
    already shipped a bug of this exact class.
    """
    runner = env_runner()
    read_remote_credentials(make_context(runner, tmp_path))
    probe = runner.calls[0][-1]
    assert probe == (
        "sh -c 'if [ -f /srv/app/.env.local ]; then echo /srv/app/.env.local; "
        "cat /srv/app/.env.local; exit 0; fi; "
        "if [ -f /srv/app/.env ]; then echo /srv/app/.env; cat /srv/app/.env; exit 0; fi; "
        "exit 9'"
    )


def test_a_non_dict_env_section_falls_back_to_default_candidates(tmp_path: Path) -> None:
    config = {"env": "not-a-mapping", "database": ENV_PROFILE["database"]}
    runner = env_runner()
    credentials = read_remote_credentials(make_context(runner, tmp_path, config))
    assert credentials.name == "acme_prod"
    probe = runner.calls[0][-1]
    assert "/srv/app/.env.local" in probe
    assert "/srv/app/.env" in probe


COMPLETE_DUMP = "CREATE TABLE a (id int);\n-- Dump completed on 2026-09-30\n"


def dump_runner(dump: str = COMPLETE_DUMP) -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env.local\n{REMOTE_ENV}")
    runner.script_prefix(["ssh"], stdout=dump)
    return runner


def test_the_dump_is_written_to_the_downloads_directory(tmp_path: Path) -> None:
    context = make_context(dump_runner(), tmp_path)
    DbDumpOperation().execute(context, {})
    assert (context.downloads_dir / DUMP_FILENAME).is_file()


def test_the_dump_command_carries_the_credentials_and_flags(tmp_path: Path) -> None:
    runner = dump_runner()
    DbDumpOperation().execute(make_context(runner, tmp_path), {})
    remote = runner.calls[1][-1]
    assert "mysqldump" in remote
    assert "--single-transaction" in remote
    assert "acme_prod" in remote
    assert "--user=acme" in remote


def test_the_dump_is_streamed_rather_than_buffered(tmp_path: Path) -> None:
    runner = dump_runner()
    context = make_context(runner, tmp_path)
    DbDumpOperation().execute(context, {})
    assert runner.invocations[1].stdout_path == context.downloads_dir / DUMP_FILENAME


def test_no_temporary_file_is_created_on_the_remote(tmp_path: Path) -> None:
    runner = dump_runner()
    DbDumpOperation().execute(make_context(runner, tmp_path), {})
    joined = " ".join(call[-1] for call in runner.calls)
    assert "> dump.sql" not in joined
    assert "rm " not in joined


def test_a_failing_dump_reports_the_remote_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env.local\n{REMOTE_ENV}")
    runner.script_prefix(["ssh"], returncode=2, stderr="Access denied for user")
    with pytest.raises(OperationError) as caught:
        DbDumpOperation().execute(make_context(runner, tmp_path), {})
    assert "Access denied" in str(caught.value)


def test_a_failing_dump_does_not_leave_a_partial_file_behind(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env.local\n{REMOTE_ENV}")
    runner.script_prefix(["ssh"], returncode=2, stdout="partial", stderr="boom")
    context = make_context(runner, tmp_path)
    with pytest.raises(OperationError):
        DbDumpOperation().execute(context, {})
    assert not (context.downloads_dir / DUMP_FILENAME).exists()


def test_the_password_never_appears_in_an_error_message(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"/srv/app/.env.local\n{REMOTE_ENV}")
    runner.script_prefix(["ssh"], returncode=2, stderr="something failed")
    with pytest.raises(OperationError) as caught:
        DbDumpOperation().execute(make_context(runner, tmp_path), {})
    assert "s3cret" not in str(caught.value)


def test_a_complete_dump_is_recognised(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text(COMPLETE_DUMP, encoding="utf-8")
    assert dump_is_complete(path) is True


def test_a_dump_without_the_marker_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text("CREATE TABLE a (id int);\n", encoding="utf-8")
    assert dump_is_complete(path) is False


def test_a_missing_file_is_incomplete(tmp_path: Path) -> None:
    assert dump_is_complete(tmp_path / "nothing.sql") is False


def test_an_empty_file_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_bytes(b"")
    assert dump_is_complete(path) is False


def test_a_large_dump_is_checked_from_the_tail_not_the_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text("x" * 10_000 + "\n-- Dump completed on 2026-09-30\n", encoding="utf-8")
    assert dump_is_complete(path) is True


TRUNCATED_DUMP = (
    "CREATE TABLE a (id int);\n"
    "INSERT INTO a VALUES (1),(2),(3\n"  # cut off mid-statement
)


def test_a_truncated_dump_is_refused_before_anything_is_imported(tmp_path: Path) -> None:
    runner = dump_runner(TRUNCATED_DUMP)
    context = make_context(runner, tmp_path)
    with pytest.raises(OperationError) as caught:
        DbDumpOperation().execute(context, {})
    message = str(caught.value)
    assert "incomplete" in message
    assert "Nothing was imported" in message
    assert not (context.downloads_dir / DUMP_FILENAME).exists()


def test_a_dump_whose_connection_dropped_leaves_no_file_to_import_by_accident(
    tmp_path: Path,
) -> None:
    context = make_context(dump_runner(TRUNCATED_DUMP), tmp_path)
    with pytest.raises(OperationError):
        DbDumpOperation().execute(context, {})
    # The next step in the plan would read this path; it must not exist.
    assert list(context.downloads_dir.glob("*.sql")) == []
