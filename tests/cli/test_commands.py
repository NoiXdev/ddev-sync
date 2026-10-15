import json
import sys
from pathlib import Path

import pytest

from ddev_sync.cli import commands
from ddev_sync.cli.context import build_context
from ddev_sync.cli.main import main
from ddev_sync.cli.reporter import ConsoleReporter
from ddev_sync.config.merge import MergeResult
from ddev_sync.errors import DdevSyncError
from ddev_sync.operations.base import discard_progress
from ddev_sync.transport.fake import FakeCommandRunner
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


def test_doctor_exits_non_zero_on_failed_credentials(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The blind spot this closes: ssh and the deploy path are both fine, but
    # `database.credentials` does not actually resolve - the one failure mode
    # that, before this probe existed, no profile with `sanitize.rules: []`
    # could ever see in `doctor`.
    from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

    monkeypatch.setattr(
        PreflightOperation,
        "check",
        lambda _self, _context: PreflightReport(
            True,
            True,
            50 * 1024**3,
            credentials_ok=False,
            credentials_detail="database.credentials is missing password",
        ),
    )
    assert invoke(project, tmp_path, "--target", "production", "doctor") == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "credentials" in output


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


def _configure(project: Path, extra_yaml: str) -> None:
    """Append top-level configuration to the project's `.ddev/ddev-sync.yaml`.

    Steps a project has no configuration for are no longer selectable, so a
    test that needs one has to give the project something to do for it.
    """
    config = project / ".ddev" / "ddev-sync.yaml"
    config.write_text(config.read_text(encoding="utf-8") + extra_yaml, encoding="utf-8")


def _fake_ssh_ok(
    _self: object,
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout_path: Path | None = None,
    sensitive: bool = False,
    passthrough: bool = False,
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
    """The success path of a real run, with the smallest step that is available.

    The generic profile configures no file paths, so `--steps files` would now
    be refused as a step with nothing to do - this test used to rely on exactly
    that, running a plan of preflight alone and calling it a success. One
    configured path gives `files` real work for the fake runner to accept.
    """
    _configure(project, "files:\n  paths:\n    - {remote: uploads/, local: uploads/}\n")
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
    _configure(
        project, 'sanitize:\n  rules:\n    - {name: clear, sql: "TRUNCATE TABLE sessions"}\n'
    )
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


def _stub_sync_runner(project: Path, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Give the project one files path and make every command a sync runs succeed.

    The `files` step is only offered when the project has something to sync, and
    no ssh or ddev is involved: the stubbed runner answers every command. The
    returned list collects the `passthrough` flag of every command it is given.
    """
    config = project / ".ddev" / "ddev-sync.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "files:\n  paths:\n    - {remote: uploads/, local: uploads/}\n",
        encoding="utf-8",
    )

    passthroughs: list[bool] = []

    def run(
        self: object,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdout_path: Path | None = None,
        sensitive: bool = False,
        passthrough: bool = False,
    ) -> CommandResult:
        passthroughs.append(passthrough)
        return CommandResult(tuple(argv), 0, "ddev-sync-ok\n", "")

    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", run)
    return passthroughs


def test_force_color_colours_the_sync_markers_on_a_stream_that_is_not_a_terminal(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two things have to hold for this to pass: the decision reaches the
    # reporter, and `click.echo` is told not to strip the escapes it writes to
    # a stream that is not a terminal.
    _stub_sync_runner(project, monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files") == 0
    output = capsys.readouterr().out
    assert "\x1b[32mOK\x1b[0m" in output
    # sanitize was not selected, so the unsanitized-data warning fires.
    assert "\x1b[33m!\x1b[0m" in output
    running = [line for line in output.splitlines() if line.lstrip().startswith("..")]
    assert running
    assert all("\x1b" not in line for line in running)


def test_the_sync_output_stays_plain_when_it_is_not_a_terminal(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_sync_runner(project, monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files") == 0
    output = capsys.readouterr().out
    assert "OK" in output
    assert "\x1b" not in output


def test_no_color_keeps_the_sync_output_plain_on_a_terminal(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On a terminal `click.echo` does not strip escapes, so unlike the
    # non-terminal case this is where a reporter that ignored the decision
    # would show.
    _stub_sync_runner(project, monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files") == 0
    output = capsys.readouterr().out
    assert "OK" in output
    assert "\x1b" not in output


def test_the_terminal_fact_is_decided_at_the_edge_and_reaches_the_rsync_runner(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passthroughs = _stub_sync_runner(project, monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files") == 0
    assert True in passthroughs


def test_without_a_terminal_rsync_is_not_asked_to_pass_its_output_through(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passthroughs = _stub_sync_runner(project, monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert invoke(project, tmp_path, "--yes", "--target", "production", "--steps", "files") == 0
    assert passthroughs
    assert not any(passthroughs)


def _sync_output(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *flags: str,
) -> str:
    _stub_sync_runner(project, monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert (
        invoke(project, tmp_path, *flags, "--yes", "--target", "production", "--steps", "files")
        == 0
    )
    return capsys.readouterr().out


def test_a_plain_sync_does_not_print_command_output(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "ddev-sync-ok" not in _sync_output(project, tmp_path, capsys, monkeypatch)


def test_dash_v_shows_the_command_but_not_its_output(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _sync_output(project, tmp_path, capsys, monkeypatch, "-v")
    assert "rsync" in output
    assert "ddev-sync-ok" not in output


def test_dash_vv_prints_what_the_commands_wrote(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _sync_output(project, tmp_path, capsys, monkeypatch, "-vv")
    assert "ddev-sync-ok" in output
    assert "\n      $ " not in output


def test_dash_vvv_prints_each_argument_vector_and_the_step_timings(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _sync_output(project, tmp_path, capsys, monkeypatch, "-vvv")
    assert "      $ " in output
    assert "s)" in output


def test_the_secrets_set_is_handed_to_the_execution_context_not_copied(
    project: Path,
) -> None:
    # The wrapper and the context have to hold the same set: the password is
    # registered by the context's side, long after the wrapper was built.
    context = build_context(
        project_root=project,
        home=project.parent / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag=None,
        no_features=False,
    )
    secrets: set[str] = set()
    execution = commands._execution_context(
        context,
        runner=SubprocessRunner(),
        assume_yes=True,
        progress=discard_progress,
        secrets=secrets,
        stdout_is_terminal=False,
    )
    assert execution.secrets is secrets


@pytest.mark.parametrize("flag", ["-vv", "-vvv"])
def test_a_production_password_read_during_the_run_is_scrubbed_from_later_output(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    """The one place a one-word edit is a security regression.

    `run_command` creates one set and hands it to the narrator and to the
    execution context. The password is registered on the context's side after
    the credential read, long after the narrator was built, so the two must
    hold the same object: `secrets=set(secrets)` at either hand-off would
    leave this password on the screen, and no test of either side alone would
    notice. The read itself is `sensitive`, so it is withheld; the password
    then comes back in a later command's stderr, which is what gets scrubbed.
    """
    password = "hunter2-production-only"
    env_file = (
        "/srv/app/.env\n"
        "DATABASE_NAME=app\nDATABASE_USER=deploy\n"
        f"DATABASE_PASSWORD={password}\n"
        "DATABASE_HOST=db.internal\nDATABASE_PORT=3306\n"
    )

    def run(
        self: object,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdout_path: Path | None = None,
        sensitive: bool = False,
        passthrough: bool = False,
    ) -> CommandResult:
        if sensitive:
            return CommandResult(tuple(argv), 0, env_file, "")
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text("-- Dump completed\n", encoding="utf-8")
            return CommandResult(tuple(argv), 0, "", f"using password {password}\n")
        return CommandResult(tuple(argv), 0, "ddev-sync-ok\n", "")

    _configure(project, "database:\n  remote_credentials: env\n")
    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", run)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    code = invoke(project, tmp_path, flag, "--yes", "--target", "production", "--steps", "db")
    output = capsys.readouterr().out
    assert code == 0
    assert "using password ***" in output
    assert password not in output


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
        "version: 1\ntargets:\n  production:\n    deployer: {host: web-1}\n    stage: production\n"
        "files:\n  paths:\n    - {remote: uploads/, local: uploads/}\n",
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
        sensitive: bool = False,
        passthrough: bool = False,
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


def test_dep_config_runs_once_even_when_the_target_fails_to_resolve(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One sync asks Deployer once, including on the way to an error.

    The claim is about the whole invocation, not about one resolver: the memo
    lives in a closure built inside `build_context`, so a second
    `build_context` has a fresh one. `_build_run_context` used to make that
    second call for *any* `TargetError`, and a single-target project whose
    `deployer:` target is missing `stage` reaches it - the retry picks the
    only name there is, without prompting, and fails identically. Counting
    inside one resolver cannot see that, which is why this counts across
    `main`.
    """
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\ntargets:\n  production:\n    deployer: {host: web-1}\n",
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
    dep_runs: list[tuple[str, ...]] = []

    def run(
        self: object,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdout_path: Path | None = None,
        sensitive: bool = False,
        passthrough: bool = False,
    ) -> CommandResult:
        is_dep = tuple(argv[-2:]) == ("config", "--format=json")
        if is_dep:
            dep_runs.append(tuple(argv))
        return CommandResult(tuple(argv), 0, payload if is_dep else "ddev-sync-ok\n", "")

    monkeypatch.setattr("ddev_sync.transport.runner.SubprocessRunner.run", run)

    code = invoke(project, tmp_path, "--yes", "--steps", "files")

    assert code == 1
    assert "is missing stage" in capsys.readouterr().err
    assert len(dep_runs) == 1


def test_describe_targets_shows_a_deployer_block_as_a_declaration(
    project: Path, tmp_path: Path
) -> None:
    # `describe_targets` feeds the interactive target picker and carried the
    # same `spec.host or "?"` pattern as the renderer, so it needs the same
    # branch - otherwise the picker offers "?:?" as a choice.
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  prod:\n"
        "    deployer: {host: web-1}\n"
        "    stage: production\n"
        "  anywhere:\n"
        "    deployer: {}\n"
        "    stage: staging\n"
        "  staging:\n"
        "    host: web02.example\n"
        "    user: deploy\n"
        "    path: /srv/staging/\n"
        "    stage: staging\n",
        encoding="utf-8",
    )
    described = commands.describe_targets(project, tmp_path / "home")
    assert described["prod"] == "from deployer: web-1"
    assert described["anywhere"] == "from deployer"
    assert described["staging"] == "deploy@web02.example:/srv/staging/"


_AVAILABLE = frozenset({"db", "files", "sanitize", "setup"})


def test_an_explicit_step_that_would_do_nothing_here_is_refused() -> None:
    with pytest.raises(DdevSyncError) as error:
        commands.resolve_selection("db,s3", assume_yes=True, available=_AVAILABLE)
    message = str(error.value)
    assert "s3" in message
    assert "not available for this project: s3 " in message
    assert "db, files, sanitize, setup" in message


def test_an_unknown_step_name_still_says_it_is_unknown() -> None:
    # Distinct from the case above: "nosuchstep" is not a step at all, while
    # "s3" is a real one this project has no work for. The two deserve
    # different sentences, and conflating them would send someone hunting
    # for a typo they did not make.
    with pytest.raises(ValueError, match="unknown step 'nosuchstep'") as error:
        commands.resolve_selection("nosuchstep", assume_yes=True, available=_AVAILABLE)
    assert "not available" not in str(error.value)


def test_assume_yes_selects_only_what_is_available() -> None:
    assert commands.resolve_selection(None, assume_yes=True, available=_AVAILABLE) == [
        "db",
        "files",
        "sanitize",
        "setup",
    ]


def test_an_explicit_available_step_is_kept() -> None:
    assert commands.resolve_selection("db", assume_yes=True, available=_AVAILABLE) == ["db"]


def test_the_picker_is_offered_only_the_available_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offered: list[list[str]] = []

    def record(available: list[str]) -> list[str]:
        offered.append(available)
        return ["db"]

    monkeypatch.setattr(commands, "choose_steps", record)
    commands.resolve_selection(None, assume_yes=False, available=_AVAILABLE)

    assert offered == [["db", "files", "sanitize", "setup"]]


def test_ticking_nothing_in_the_picker_is_refused_rather_than_planning_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enter at the checkbox with nothing ticked returns `[]`, not `None`:
    questionary's checkbox configures no validator and exits with an empty
    result. That used to plan a lone preflight and report a successful sync
    that had done nothing - the identical failure `--steps ""` is refused
    for, on the primary interactive path. The two refusals must stay
    distinguishable, so this one must not read as "no terminal".
    """
    monkeypatch.setattr(commands, "choose_steps", lambda _available: [])
    with pytest.raises(DdevSyncError) as error:
        commands.resolve_selection(None, assume_yes=False, available=_AVAILABLE)
    message = str(error.value)
    assert "no step was ticked" in message
    assert "db, files, sanitize, setup" in message
    assert "terminal" not in message


def test_without_a_terminal_the_hint_lists_only_the_available_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands, "choose_steps", lambda _available: None)
    with pytest.raises(DdevSyncError) as error:
        commands.resolve_selection(None, assume_yes=False, available=_AVAILABLE)
    assert "--steps (db,files,sanitize,setup)" in str(error.value)


@pytest.mark.parametrize("steps", ["", " , "])
def test_an_empty_steps_value_is_refused_rather_than_planning_nothing(steps: str) -> None:
    with pytest.raises(DdevSyncError) as error:
        commands.resolve_selection(steps, assume_yes=True, available=_AVAILABLE)
    assert "names no step" in str(error.value)
    assert "db, files, sanitize, setup" in str(error.value)


def test_nothing_available_reads_none_rather_than_an_empty_list() -> None:
    """An empty `available` is not reachable through the CLI today: `db` is
    produced by every profile, including the generic one, so
    `available_selection_groups` never returns an empty set in practice. This
    pins the wording of a state the function must still handle - "Available:
    none." rather than "Available: ." - and nobody should read it as evidence
    that the state occurs.
    """
    with pytest.raises(DdevSyncError, match=r"Available: none\."):
        commands.resolve_selection("db", assume_yes=True, available=frozenset())
    with pytest.raises(DdevSyncError, match=r"Available: none\."):
        commands.resolve_selection("", assume_yes=True, available=frozenset())


def test_progress_lines_reach_the_reporter_on_a_terminal() -> None:
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append)

    listen = commands._progress_listener(reporter, stdout_is_terminal=True)
    listen("reaching deploy@web01.example")

    assert lines == ["        reaching deploy@web01.example"]


def test_progress_lines_go_nowhere_without_a_terminal() -> None:
    """A CI log is read after the fact, where "it is still waiting" has no
    reader left to reassure - and the lines would outlive their reason."""
    lines: list[str] = []
    reporter = ConsoleReporter(write=lines.append)

    listen = commands._progress_listener(reporter, stdout_is_terminal=False)
    listen("reaching deploy@web01.example")

    assert listen is discard_progress
    assert lines == []


def test_a_sync_on_a_terminal_names_the_round_trip_it_is_waiting_for(
    project: Path, tmp_path: Path
) -> None:
    """The whole chain, not its links: `run_command` builds the listener, puts
    it on the context, and the preflight operation writes through it.

    Each half of this is covered on its own, which is exactly why this exists:
    two correct halves can still be wired to different contexts, and nothing
    else here would notice.
    """
    lines: list[str] = []
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="generic",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag=None,
        no_features=False,
    )

    commands.run_command(
        context,
        [],
        runner=runner,
        dry_run=False,
        assume_yes=True,
        echo=lines.append,
        stdout_is_terminal=True,
    )

    assert "        reaching deploy@web01.example" in lines


def test_the_same_sync_in_a_log_names_nothing(project: Path, tmp_path: Path) -> None:
    lines: list[str] = []
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ddev-sync-ok\n")
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="generic",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag=None,
        no_features=False,
    )

    commands.run_command(
        context,
        [],
        runner=runner,
        dry_run=False,
        assume_yes=True,
        echo=lines.append,
        stdout_is_terminal=False,
    )

    assert not any("reaching" in line for line in lines)
