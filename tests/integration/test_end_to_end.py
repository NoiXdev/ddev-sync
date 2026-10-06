"""A real pull: dump over ssh, import, sync files, sanitize."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ddev_sync.cli.main import main
from tests.integration.paths import PROJECT_DIR

pytestmark = pytest.mark.integration

HERE = Path(__file__).parent
PROFILE_ROOT = HERE.parent.parent / "profiles"
# `docker compose` interpolates the whole file, `exec` included, and the web
# service's mount source is `${DDEV_SYNC_PROJECT_DIR}`: without it the file is
# rejected as an invalid volume spec before any container is reached.
COMPOSE_ENV = {**os.environ, "DDEV_SYNC_PROJECT_DIR": str(PROJECT_DIR)}


@pytest.fixture
def project(ssh_key: Path) -> Path:
    # Not `tmp_path`: the web container mounts PROJECT_DIR at /var/www/html
    # (see conftest), and that mount is fixed when the stack comes up. Cleaned
    # per test instead. Only the contents are removed, not the directory
    # itself: a bind mount follows the directory's inode, so deleting and
    # recreating it would leave the container looking at a stale, unlinked
    # directory.
    root = PROJECT_DIR
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: typo3\ndocroot: public\n", encoding="utf-8"
    )
    (root / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: 127.0.0.1\n"
        "    user: deploy\n"
        "    port: 22022\n"
        "    path: /srv/app/plain/\n"
        "    stage: production\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("TYPO3_DB_NAME=placeholder\n", encoding="utf-8")
    # `sanitize`'s `local-admin` rule (profiles/typo3.yaml) execs
    # `vendor/bin/typo3 backend:user:create ...` in the web container (the
    # `ddev-acme-web` stand-in in docker-compose.yaml, which mounts this
    # project at /var/www/html) and treats anything but a zero exit (or an
    # "is already taken" message) as a hard failure. A stub that always
    # succeeds is enough to prove the rule dispatches and runs the right
    # argv; it is not the TYPO3 CLI's job to re-verify here.
    (root / "vendor" / "bin").mkdir(parents=True)
    stub = root / "vendor" / "bin" / "typo3"
    stub.write_text('#!/bin/sh\necho "stub typo3: $*"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    return root


def local_query(sql: str) -> str:
    # `mariadb:11` only ships the `mariadb`/`mariadb-admin` binary names -
    # there is no `mysql` compatibility symlink in this image.
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(HERE / "docker-compose.yaml"),
            "exec",
            "-T",
            "local-db",
            "mariadb",
            # `local-db` runs with no certificate (see the compose file), and
            # the client in `mariadb:11` is 11.8, which requires a verified TLS
            # connection by default - even over the local socket, which this
            # is. Any option that relaxes that does here; the engine's own
            # choice between them is argued at `LOCAL_DB_TLS_OPTION`.
            "--skip-ssl",
            "--user=db",
            "--password=db",
            "db",
            "-N",
            "-e",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=COMPOSE_ENV,
    )
    return result.stdout.strip()


def test_a_full_pull_moves_the_database_and_the_files(project: Path, tmp_path: Path) -> None:
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            "--steps",
            "db,files",
            "--yes",
        ]
    )
    assert code == 0
    assert local_query("SELECT COUNT(*) FROM fe_users") == "2"
    assert (project / "public" / "fileadmin" / "brochure.pdf").is_file()


def test_sanitizing_removes_the_real_email_addresses(project: Path, tmp_path: Path) -> None:
    # This is also the regression test for `clear-scheduler-tasks`'s
    # `tolerate_missing_table`: `fixtures/seed.sql` deliberately has no
    # `tx_scheduler_task` table (the realistic case - EXT:scheduler is an
    # optional Composer package most projects never require), and
    # `clear-scheduler-tasks` sits second in profiles/typo3.yaml's rule
    # list. Before `tolerate_missing_table`, the missing table would have
    # aborted every rule below it - including every assertion below, on data
    # already imported into the local database. A green run here, with every
    # rule after it still having taken effect, is the proof.
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            "--steps",
            "db,sanitize",
            "--yes",
        ]
    )
    assert code == 0
    emails = local_query("SELECT GROUP_CONCAT(email) FROM fe_users")
    assert "customer.example" not in emails
    assert "example.invalid" in emails
    be_user_emails = local_query("SELECT GROUP_CONCAT(email) FROM be_users")
    assert "customer.example" not in be_user_emails
    assert "example.invalid" in be_user_emails
    assert local_query("SELECT password FROM be_users WHERE uid = 1") == "$argon2i$disabled"
    assert local_query("SELECT COUNT(*) FROM be_sessions") == "0"
    assert local_query("SELECT COUNT(*) FROM sys_log") == "0"


def remote_db_exec(sql: str) -> None:
    """Run `sql` against the remote stand-in's "production" database.

    Used to apply `fixtures/seed_with_scheduler.sql` on demand, rather than
    mounting a second `docker-entrypoint-initdb.d` script: there is only one
    `db` container for the whole session (`conftest.py`'s `stack` fixture is
    session-scoped), so a second initdb script would apply to every test, not
    just the one that wants the table present.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(HERE / "docker-compose.yaml"),
            "exec",
            "-T",
            "db",
            "mariadb",
            "--user=root",
            "--password=root",
            "acme_prod",
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
        env=COMPOSE_ENV,
    )
    if result.returncode != 0:
        raise AssertionError(f"remote_db_exec failed: {result.stderr}")


def test_a_present_scheduler_table_is_still_truncated(project: Path, tmp_path: Path) -> None:
    """The companion to the test above: `tolerate_missing_table` swallows
    ONLY "table doesn't exist" - a project that DID require EXT:scheduler
    still gets it truncated exactly as before. Seeds `tx_scheduler_task`
    directly on the remote "production" database for this test only, and
    cleans it back up afterwards, since the remote container is shared for
    the whole session rather than recreated per test.
    """
    seed_sql = (HERE / "fixtures" / "seed_with_scheduler.sql").read_text(encoding="utf-8")
    try:
        remote_db_exec(seed_sql)
        code = main(
            [
                "--project-root",
                str(project),
                "--home",
                str(tmp_path / "home"),
                "--profile-root",
                str(PROFILE_ROOT),
                "--target",
                "production",
                "--steps",
                "db,sanitize",
                "--yes",
            ]
        )
        assert code == 0
        assert local_query("SELECT COUNT(*) FROM tx_scheduler_task") == "0"
    finally:
        remote_db_exec("DROP TABLE IF EXISTS tx_scheduler_task;")


def test_the_sanitize_table_probe_aborts_before_the_dump_when_a_table_is_missing(
    project: Path, tmp_path: Path
) -> None:
    """Part 3: operations/preflight.py lists the remote database's tables
    and checks every non-tolerated `requires_table` before the dump starts.
    This is the one thing the FakeCommandRunner-based unit tests cannot
    prove: that the real `SHOW TABLES` invocation (via the dockerized
    `mysql`/`mariadb-admin` wrapper, over the real ssh connection) actually
    works against a real server, not just a scripted stand-in.
    """
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: 127.0.0.1\n"
        "    user: deploy\n"
        "    port: 22022\n"
        "    path: /srv/app/plain/\n"
        "    stage: production\n"
        "sanitize:\n"
        "  rules:\n"
        "    mode: replace\n"
        "    values:\n"
        "      - name: anonymize-missing\n"
        '        sql: "UPDATE no_such_table SET x = 1"\n'
        "        requires_table: no_such_table\n",
        encoding="utf-8",
    )
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            "--steps",
            "db,sanitize",
            "--yes",
        ]
    )
    assert code == 1
    assert not (project / ".ddev" / ".downloads" / "dump.sql").exists()


def test_the_dump_is_removed_after_the_import(project: Path, tmp_path: Path) -> None:
    main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            "--steps",
            "db",
            "--yes",
        ]
    )
    assert not (project / ".ddev" / ".downloads" / "dump.sql").exists()


def test_doctor_passes_against_the_real_remote(project: Path, tmp_path: Path) -> None:
    # Every option is declared on the `cli` group only (see `cli/main.py`'s
    # module docstring), so it must precede the `doctor` subcommand name -
    # click stops parsing the group's own options at the first non-option
    # token on the line.
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            "doctor",
        ]
    )
    assert code == 0


def test_an_unreachable_target_fails_cleanly(project: Path, tmp_path: Path) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  broken:\n"
        "    host: 127.0.0.1\n"
        "    user: deploy\n"
        "    port: 1\n"
        "    path: /srv/app/plain/\n"
        "    stage: production\n",
        encoding="utf-8",
    )
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "broken",
            "--steps",
            "db",
            "--yes",
        ]
    )
    assert code == 1


@pytest.fixture
def deployer_project(tmp_path: Path, ssh_key: Path) -> Path:
    """Same TYPO3 profile declaration (`public/fileadmin/`), but the target
    points at the `deployer/` tree built by `remote_layout` with
    `layout: deployer`: code under `current/`, data under `shared/`. This
    proves `code_root`/`data_root` derivation against a real directory
    tree, not only the unit tests that exercise the property in isolation.
    """
    root = tmp_path / "acme-deployer"
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: typo3\ndocroot: public\n", encoding="utf-8"
    )
    (root / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: 127.0.0.1\n"
        "    user: deploy\n"
        "    port: 22022\n"
        "    path: /srv/app/deployer/\n"
        "    stage: production\n"
        "    layout: deployer\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("TYPO3_DB_NAME=placeholder\n", encoding="utf-8")
    return root


def test_a_pull_from_a_deployer_layout_target_finds_the_files_under_shared(
    deployer_project: Path, tmp_path: Path
) -> None:
    code = main(
        [
            "--project-root",
            str(deployer_project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--target",
            "production",
            "--steps",
            "files",
            "--yes",
        ]
    )
    assert code == 0
    assert (deployer_project / "public" / "fileadmin" / "brochure.pdf").is_file()
