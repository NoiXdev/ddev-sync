"""A real pull: dump over ssh, import, sync files, sanitize."""

import subprocess
from pathlib import Path

import pytest

from ddev_sync.cli.main import main

pytestmark = pytest.mark.integration

HERE = Path(__file__).parent
PROFILE_ROOT = HERE.parent.parent / "profiles"


@pytest.fixture
def project(tmp_path: Path, ssh_key: Path) -> Path:
    root = tmp_path / "acme"
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
    # `vendor/bin/typo3 backend:createadmin ...` locally and treats anything
    # but a zero exit (or an "already exists" message) as a hard failure. A
    # stub that always succeeds is enough to prove the rule dispatches and
    # runs the right argv; it is not the TYPO3 CLI's job to re-verify here.
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
