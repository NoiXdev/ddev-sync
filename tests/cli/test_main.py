from pathlib import Path

import pytest

from ddev_sync.cli.main import main

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: php\ndocroot: public\n", encoding="utf-8"
    )
    (root / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
        encoding="utf-8",
    )
    return root


def invoke(project: Path, tmp_path: Path, *extra: str) -> int:
    return main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            *extra,
        ]
    )


def test_dry_run_prints_the_plan_and_exits_zero(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--dry-run", "--steps", "db")
    assert code == 0
    output = capsys.readouterr().out
    assert "Plan for target 'production'" in output
    assert "Nothing will be executed." in output
    assert "db.dump" in output


def test_without_dry_run_it_says_execution_is_not_implemented_yet(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--steps", "db")
    assert code == 1
    assert "not implemented" in capsys.readouterr().err


def test_a_configuration_error_exits_one_with_a_readable_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    code = main(["--project-root", str(empty), "--home", str(tmp_path / "home"), "--dry-run"])
    assert code == 1
    error = capsys.readouterr().err
    assert "does not look like a DDEV project" in error
    assert "Traceback" not in error


def test_an_unknown_step_name_is_rejected(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--dry-run", "--steps", "database")
    assert code == 1
    assert "unknown step 'database'" in capsys.readouterr().err


def test_steps_accepts_a_comma_separated_list(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(project, tmp_path, "--dry-run", "--steps", "db,files") == 0
    assert "db.dump" in capsys.readouterr().out


def test_no_steps_flag_plans_everything(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(project, tmp_path, "--dry-run") == 0
    assert "db.dump" in capsys.readouterr().out


def test_an_unknown_option_is_a_click_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--not-a-real-option"])
    assert code == 2
    assert "no such option" in capsys.readouterr().err.lower()


def test_the_version_info_flag_prints_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--version-info"])
    assert code == 0
    assert "ddev-sync" in capsys.readouterr().out
