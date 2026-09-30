from pathlib import Path

import pytest

from ddev_sync.cli import commands
from ddev_sync.cli.main import main
from ddev_sync.config.merge import MergeResult
from ddev_sync.transport.runner import CommandResult

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
        "    stage: production\n"
        "  staging:\n"
        "    host: web02.example\n"
        "    user: deploy\n"
        "    path: /srv/staging/\n"
        "    stage: staging\n",
        encoding="utf-8",
    )
    return root


def invoke(project: Path, tmp_path: Path, *extra: str) -> int:
    return main(
        [
            *extra,
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
        ]
    )


def test_targets_lists_every_target_with_its_source(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(project, tmp_path, "targets") == 0
    output = capsys.readouterr().out
    assert "production" in output
    assert "staging" in output
    assert "deploy@web01.example:/srv/app/" in output
    assert ".ddev/ddev-sync.yaml" in output


def test_targets_with_none_defined_points_at_init(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "bare"
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text("name: a\ntype: php\n", encoding="utf-8")
    assert invoke(root, tmp_path, "targets") == 0
    assert "ddev sync init" in capsys.readouterr().out


def test_doctor_reports_each_check(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(True, True, 50 * 1024**3),
    )
    assert invoke(project, tmp_path, "doctor", "--target", "production") == 0
    output = capsys.readouterr().out
    assert "ssh" in output
    assert "deploy path" in output
    assert "disk" in output


def test_doctor_exits_nonzero_when_a_check_fails(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(False, False, 1024),
    )
    assert invoke(project, tmp_path, "doctor", "--target", "production") == 1
    assert "FAIL" in capsys.readouterr().out


def test_a_run_without_a_target_and_without_a_tty_asks_for_target(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.cli import prompts

    monkeypatch.setattr(prompts, "_select", lambda _q, _c: None)
    code = invoke(project, tmp_path, "--dry-run")
    assert code == 1
    assert "no target selected" in capsys.readouterr().err


def test_an_interactive_run_uses_the_chosen_target_and_steps(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.cli import prompts

    monkeypatch.setattr(prompts, "_select", lambda _q, choices: choices[0])
    monkeypatch.setattr(prompts, "_checkbox", lambda _q, _c, _d: ["db"])
    assert invoke(project, tmp_path, "--dry-run") == 0
    output = capsys.readouterr().out
    assert "db.dump" in output
    assert "files.sync" not in output


def test_yes_skips_the_step_prompt_and_runs_everything(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.cli import prompts

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("should not have prompted")

    monkeypatch.setattr(prompts, "_checkbox", explode)
    assert invoke(project, tmp_path, "--dry-run", "--yes", "--target", "production") == 0
    assert "db.dump" in capsys.readouterr().out


def test_cancelling_the_step_prompt_exits_one(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.cli import prompts

    monkeypatch.setattr(prompts, "_checkbox", lambda _q, _c, _d: None)
    code = invoke(project, tmp_path, "--dry-run", "--target", "production")
    assert code == 1
    assert "no steps selected" in capsys.readouterr().err


def _fake_ssh_ok(
    _self: object,
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout_path: Path | None = None,
) -> CommandResult:
    return CommandResult(argv=tuple(argv), returncode=0, stdout="ddev-sync-ok\n", stderr="")


def test_a_real_run_reports_and_exits_nonzero_on_a_failed_step(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`db.dump` fails fast (no credentials configured), which exercises the
    real (non-dry-run) execution path end to end: the live reporter, the
    executor stopping after a failure, and the unsanitized-data warning.
    """
    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", _fake_ssh_ok)
    code = invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "db")
    output = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in output
    assert "not sanitized" in output.lower()


def test_a_real_run_with_nothing_to_do_exits_zero(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight is the only step that can run for the generic profile with no
    configured file paths; this covers the success path of a real run.
    """
    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", _fake_ssh_ok)
    code = invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files")
    output = capsys.readouterr().out
    assert code == 0
    assert "not sanitized" in output.lower()


def test_a_real_run_with_sanitize_selected_skips_the_warning(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", _fake_ssh_ok)
    code = invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "sanitize")
    output = capsys.readouterr().out
    assert code == 0
    assert "not sanitized" not in output.lower()


def test_keep_dump_defaults_to_false_without_a_defaults_block() -> None:
    assert commands._keep_dump(MergeResult(data={})) is False


def test_keep_dump_reads_the_configured_value() -> None:
    merged = MergeResult(data={"defaults": {"keep_dump": True}})
    assert commands._keep_dump(merged) is True


def test_keep_dump_ignores_a_non_mapping_defaults_block() -> None:
    merged = MergeResult(data={"defaults": "oops"})
    assert commands._keep_dump(merged) is False
