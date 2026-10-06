import json
import sys
from pathlib import Path

import pytest

from ddev_sync.cli import commands
from ddev_sync.cli.context import build_context
from ddev_sync.cli.main import main
from ddev_sync.config.merge import MergeResult
from ddev_sync.transport.runner import CommandResult, SubprocessRunner

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
FEATURE_ROOT = Path(__file__).parent / "features"


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
    # The path options (`--project-root`/`--home`/`--profile-root`) are
    # declared on the group only, so they must come *before* a subcommand
    # name on the command line - anything after the subcommand name belongs
    # to that subcommand's own parser. This is also the order the DDEV
    # wrapper in Plan 3 will actually use: `ddev sync <subcommand>` expands
    # with the root supplied up front.
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


def test_targets_with_path_options_before_the_subcommand_name(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression test for a real bug: when the three path options were
    declared on both the group and every subcommand, whichever copy the
    parser consulted depended on where the flags sat relative to the
    subcommand name - `targets --project-root X` and `--project-root X
    targets` silently resolved `X` two different ways, one of them wrong,
    with no error. This is also the order the DDEV wrapper (Plan 3) will
    actually invoke this CLI in: `ddev sync targets` expands with the root
    supplied up front.
    """
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "targets",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "production" in output
    assert "deploy@web01.example:/srv/app/" in output


def test_doctor_with_path_options_before_the_subcommand_name(
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
    assert "ssh" in capsys.readouterr().out


def test_path_options_after_the_subcommand_name_are_rejected_loudly(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three path options live on the group only now; putting them after
    a subcommand name must fail loudly with a usage error - not silently
    fall back to the defaults, which is the bug this whole group of tests
    guards against.
    """
    code = main(["targets", "--project-root", str(project)])
    assert code == 2
    assert "no such option" in capsys.readouterr().err.lower()


def test_target_after_the_subcommand_name_is_rejected_loudly(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--target is group-only now, for the same reason the path options are:
    a second declaration on `doctor` would silently use its own default
    depending on where the flag sat relative to the subcommand name. Putting
    it after the subcommand name must fail loudly instead of silently
    running against no target at all.
    """
    code = invoke(project, tmp_path, "doctor", "--target", "production")
    assert code == 2
    assert "no such option" in capsys.readouterr().err.lower()


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
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 0
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
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    assert "FAIL" in capsys.readouterr().out


def test_doctor_exits_nonzero_when_the_sanitize_table_probe_fails(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The new failure mode this closes: ssh and the deploy path are both
    # fine, but a sanitize rule needs a table production does not have.
    # `doctor`'s whole job is to catch this before a real sync does, so it
    # must exit non-zero here too, not just on the original two checks.
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(
            True,
            True,
            50 * 1024**3,
            sanitize_tables_ok=False,
            sanitize_tables_detail="sanitize rule 'x' requires table 'y', which does not exist",
        ),
    )
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "sanitize tables" in output


def test_doctor_exits_nonzero_when_only_the_disk_space_check_fails(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ssh and the deploy path are fine, but the project volume is below
    # `MIN_FREE_BYTES` - which `PreflightOperation.execute` refuses outright,
    # so a sync gated on this command's exit status would be allowed to start
    # and then abort in preflight. The rendered row already says FAIL; the
    # exit code has to agree with it.
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(True, True, 1024**3),
    )
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    output = capsys.readouterr().out
    assert "disk space" in output
    assert "FAIL" in output


def test_doctor_exits_zero_when_the_sanitize_table_probe_has_nothing_to_report(
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
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 0
    assert "sanitize tables" not in capsys.readouterr().out


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


def test_a_real_feature_contributes_a_step_to_the_plan(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`run_command`'s `features=context.features` (the line that wires the
    feature subsystem's resolution into the plan it builds) has no other
    test that would fail if it were deleted or replaced with an empty
    resolution: `invoke()`'s `project` fixture is a plain `php` project with
    no `composer.lock`/`package.json`. Task 7 later added a `--feature-root`
    CLI flag that a test could use to point a CLI-level run at a fixture
    directory with something to detect (see `test_main.py`'s feature tests),
    but this one predates that and still exercises the wiring more directly:
    `build_context` is called directly here - exactly what this module's own
    docstring says the split from `main.py` is for - against a project whose
    `composer.lock` genuinely activates `filament`.

    Asserting on the "Features" block, specifically, matters: the setup step
    and its `[feature:filament]` source label come from `context.merged`
    (already carrying the feature's layer, merged in by `build_context`
    itself) and render identically whether or not `build_plan` ever sees
    `context.features` - a first version of this test asserted on exactly
    that step line and kept passing when the wiring line below was mutated
    to pass an empty resolution instead. The "Features" block above the
    steps is the *only* thing `build_plan`'s `features` argument controls
    (see `Plan.features`/`features_detection_enabled`), which is what makes
    this assertion the one that actually exercises the wiring.
    """
    (project / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "filament/filament", "version": "v3.2.0"}]}),
        encoding="utf-8",
    )
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="laravel",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag=None,
        no_features=False,
    )
    code = commands.run_command(
        context, ["setup"], runner=SubprocessRunner(), dry_run=True, assume_yes=True, echo=print
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "Features  filament" in output
    assert "detected: composer package filament/filament" in output


def test_keep_dump_defaults_to_false_without_a_defaults_block() -> None:
    assert commands._keep_dump(MergeResult(data={})) is False


def test_keep_dump_reads_the_configured_value() -> None:
    merged = MergeResult(data={"defaults": {"keep_dump": True}})
    assert commands._keep_dump(merged) is True


def test_keep_dump_ignores_a_non_mapping_defaults_block() -> None:
    merged = MergeResult(data={"defaults": "oops"})
    assert commands._keep_dump(merged) is False


def test_doctor_exits_nonzero_when_the_web_container_is_unreachable(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(
            True, True, 50 * 1024**3, web_ok=False, web_detail="ddev-acme-web did not answer"
        ),
    )
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    assert "web container" in capsys.readouterr().out


def test_doctor_exits_nonzero_when_the_agent_socket_is_unusable(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(
            True, True, 50 * 1024**3, agent_ok=False, agent_detail="SSH_AUTH_SOCK is not set"
        ),
    )
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    assert "ssh agent" in capsys.readouterr().out


def _stub_doctor_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `doctor` render one OK row and one FAIL row, with no ssh involved."""
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(True, False, 50 * 1024**3),
    )


def test_doctor_colours_its_markers_on_a_terminal(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_doctor_report(monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    output = capsys.readouterr().out
    assert "\x1b[32mOK\x1b[0m" in output
    assert "\x1b[31mFAIL\x1b[0m" in output


def test_doctor_stays_plain_when_the_output_is_not_a_terminal(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_doctor_report(monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    assert "\x1b" not in capsys.readouterr().out


def test_doctor_stays_plain_on_a_terminal_when_no_color_is_set(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_doctor_report(monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    assert "\x1b" not in capsys.readouterr().out


def test_force_color_reaches_the_output_even_though_it_is_not_a_terminal(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `click.echo` removes ANSI sequences from any stream that is not a
    # terminal unless it is told otherwise, which would silently cancel
    # FORCE_COLOR - the one case that exists for output that is not a terminal.
    _stub_doctor_report(monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    output = capsys.readouterr().out
    assert "\x1b[32mOK\x1b[0m" in output
    assert "\x1b[31mFAIL\x1b[0m" in output


def test_a_deployer_target_is_resolved_and_synced_through_one_runner(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner that asks Deployer where to connect is the one that then
    connects: one run, one runner. Recording `id(self)` for every command - the
    `dep config` that builds the context and the preflight commands that follow
    it - is what would show a second `SubprocessRunner` being constructed.
    """
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\ntargets:\n  production:\n    deployer: {host: web-1}\n    stage: production\n",
        encoding="utf-8",
    )
    payload = json.dumps(
        {
            "web-1": {
                "hostname": "web-1.example",
                "remote_user": "deploy",
                "port": None,
                "deploy_path": "/srv/app",
                "current_path": "/srv/app/current",
            }
        }
    )
    seen: list[tuple[int, tuple[str, ...]]] = []

    def run(
        self: object,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdout_path: Path | None = None,
    ) -> CommandResult:
        seen.append((id(self), tuple(argv)))
        is_dep = tuple(argv[-3:]) == ("vendor/bin/dep", "config", "--format=json")
        return CommandResult(tuple(argv), 0, payload if is_dep else "ddev-sync-ok\n", "")

    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", run)

    code = invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files")

    assert code == 0
    assert tuple(seen[0][1][-3:]) == ("vendor/bin/dep", "config", "--format=json")
    assert any("web-1.example" in " ".join(argv) for _, argv in seen[1:])
    assert len({runner_id for runner_id, _ in seen}) == 1
    assert len(seen) > 1
