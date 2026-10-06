import json
from pathlib import Path

import pytest

from ddev_sync.cli.main import main

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


def test_the_dry_run_help_does_not_promise_that_nothing_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The flag's own help is the only description of `--dry-run` a user sees
    # without opening the documentation, and a dry run of a `deployer:`
    # target does evaluate the project's `deploy.php`. Pinning the text here
    # keeps the promise from quietly coming back.
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "execute nothing" not in output
    assert "Deployer is still resolved" in " ".join(output.split())


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


def test_an_empty_targets_block_exits_one_without_a_traceback(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text("version: 1\ntargets:\n", encoding="utf-8")
    code = invoke(project, tmp_path, "--dry-run")
    assert code == 1
    error = capsys.readouterr().err
    assert "ddev sync init" in error
    assert "Traceback" not in error


def test_an_empty_project_block_exits_one_without_a_traceback(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text("version: 1\nproject:\n", encoding="utf-8")
    code = invoke(project, tmp_path, "--dry-run")
    assert code == 1
    error = capsys.readouterr().err
    assert "ddev sync init" in error
    assert "Traceback" not in error


def test_a_files_entry_missing_remote_exits_one_without_a_traceback(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n"
        "files:\n"
        "  paths:\n"
        "    - local: public/uploads/\n",
        encoding="utf-8",
    )
    code = invoke(project, tmp_path, "--dry-run", "--steps", "files")
    assert code == 1
    error = capsys.readouterr().err
    assert "remote" in error
    assert "Traceback" not in error


def test_an_unknown_step_name_is_rejected(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--dry-run", "--steps", "database")
    assert code == 1
    assert "unknown step 'database'" in capsys.readouterr().err


def test_a_step_the_project_has_no_work_for_is_refused_by_name(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No shipped profile configures S3, so `--steps s3` would plan nothing
    but a preflight and report success - the failure this refusal exists to
    prevent. It must also say what *is* available, and must not claim the
    name is unknown, because `s3` is a real step.
    """
    code = invoke(project, tmp_path, "--dry-run", "--steps", "s3")
    assert code == 1
    error = capsys.readouterr().err
    assert "not available for this project: s3 " in error
    assert "db" in error
    assert "unknown step" not in error


def test_yes_does_not_select_a_step_the_project_has_no_work_for(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(project, tmp_path, "--dry-run", "--yes") == 0
    output = capsys.readouterr().out
    assert "db.dump" in output
    assert "s3.sync" not in output


def _break_files(project: Path) -> None:
    """Give the project a `files.paths` entry with no `remote`."""
    config = project / ".ddev" / "ddev-sync.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "files:\n  paths:\n    - {local: uploads/}\n",
        encoding="utf-8",
    )


def test_a_step_that_is_not_selected_is_not_held_to_account_for_its_configuration(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The availability probe plans every group, so a broken `files` entry
    must not fail a run that only asked for `db`. This ran fine before the
    probe existed and has to keep doing so.
    """
    _break_files(project)
    assert invoke(project, tmp_path, "--dry-run", "--steps", "db") == 0
    assert "db.dump" in capsys.readouterr().out


def test_the_step_with_broken_configuration_still_fails_with_its_own_message(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _break_files(project)
    for extra in (["--steps", "files"], ["--yes"]):
        code = invoke(project, tmp_path, "--dry-run", *extra)
        error = capsys.readouterr().err
        assert code == 1
        assert "missing 'remote'" in error
        assert "not available" not in error


def test_an_empty_steps_value_is_refused_at_the_command_line(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--dry-run", "--steps", "")
    assert code == 1
    assert "names no step" in capsys.readouterr().err


def test_steps_accepts_a_comma_separated_list(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `files` is only selectable once the project has a path to sync.
    config = project / ".ddev" / "ddev-sync.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "files:\n  paths:\n    - {remote: uploads/, local: uploads/}\n",
        encoding="utf-8",
    )
    assert invoke(project, tmp_path, "--dry-run", "--steps", "db,files") == 0
    output = capsys.readouterr().out
    assert "db.dump" in output
    assert "files.sync" in output


def test_no_steps_flag_plans_everything(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `--yes` is required here: without an explicit `--steps`, a real (non-test)
    # invocation would otherwise prompt interactively for which step groups to
    # run (Task 13) - `--yes` is what says "use the full default selection".
    assert invoke(project, tmp_path, "--dry-run", "--yes") == 0
    assert "db.dump" in capsys.readouterr().out


def test_an_unknown_option_is_a_click_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--not-a-real-option"])
    assert code == 2
    assert "no such option" in capsys.readouterr().err.lower()


def test_an_option_placed_after_doctor_hints_that_it_must_precede_the_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["doctor", "--target", "x"])
    assert code == 2
    error = capsys.readouterr().err.lower()
    assert "no such option" in error
    assert "must precede the subcommand" in error
    assert "--target" in error


def test_an_option_placed_after_targets_hints_the_same_way(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Not special-cased to `doctor`: every subcommand shares the same group
    # option list, so this must trigger identically for `targets`.
    code = main(["targets", "--home", "/tmp/x"])
    assert code == 2
    error = capsys.readouterr().err.lower()
    assert "no such option" in error
    assert "must precede the subcommand" in error
    assert "--home" in error


def test_an_unknown_option_after_a_subcommand_gets_no_misleading_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The hint only fires for a name that is genuinely a `cli` group option;
    # a plain typo must not get told it merely needs to move.
    code = main(["doctor", "--not-a-real-option"])
    assert code == 2
    error = capsys.readouterr().err.lower()
    assert "no such option" in error
    assert "must precede the subcommand" not in error


def test_the_version_flag_prints_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--version"])
    assert code == 0
    assert "ddev-sync" in capsys.readouterr().out


def test_the_framework_version_flag_overrides_detection(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--dry-run", "--framework-version", "12", "--steps", "db")
    assert code == 0
    assert "db.dump" in capsys.readouterr().out


def _write_filament(project: Path) -> None:
    (project / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "filament/filament", "version": "v3.2.0"}]}),
        encoding="utf-8",
    )


def _write_node_build(project: Path) -> None:
    (project / "package.json").write_text("{}", encoding="utf-8")
    (project / "package-lock.json").write_text("{}", encoding="utf-8")


def test_no_features_suppresses_the_feature_block(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--no-features` turns detection off, so `filament` never activates.

    It does not, however, make the "Features" heading disappear outright:
    `render.render_features` (shipped before Task 7, and still covered by
    `test_render.py::test_render_plan_shows_the_detection_disabled_notice_above_the_steps`)
    deliberately prints a one-line notice whenever
    `Plan.features_detection_enabled` is `False`, precisely so a user who
    expected a feature to show up learns that detection was off rather than
    concluding their project was not recognised. The brief's own sketch of
    this test ("asserts 'Features' does not appear in the output") cannot
    hold given that shipped behaviour - the notice line starts with the
    literal word "Features" - so this asserts the more precise thing that is
    actually true: the per-feature line for `filament` is gone, replaced by
    the disabled notice.

    `--no-features` and the `defaults.detect_features: false` kill switch
    both disable detection, but they are not the same source: the notice
    must name the one that actually fired here - `--no-features`, a flag this
    invocation typed - rather than always blaming
    `~/.ddev/ddev-sync/config.yaml`, a file this test never writes.
    """
    _write_filament(project)
    code = invoke(
        project,
        tmp_path,
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--no-features",
        "--dry-run",
        "--yes",
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "filament" not in output
    assert "detection disabled by --no-features" in output
    assert "config.yaml" not in output


def test_the_config_kill_switch_blames_the_config_file_not_the_flag(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the pair above: `defaults.detect_features: false`
    in the global config file disables detection with no `--no-features` on
    the command line at all, so the notice must name the file, not the flag.
    """
    _write_filament(project)
    home = tmp_path / "home"
    config = home / ".ddev" / "ddev-sync" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\ndefaults:\n  detect_features: false\n", encoding="utf-8")
    code = invoke(
        project,
        tmp_path,
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--dry-run",
        "--yes",
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "filament" not in output
    assert "detection disabled by ~/.ddev/ddev-sync/config.yaml" in output
    assert "--no-features" not in output


def test_features_flag_restricts_to_the_named_ones(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_filament(project)
    _write_node_build(project)
    code = invoke(
        project,
        tmp_path,
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--features",
        "node-build",
        "--dry-run",
        "--yes",
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "node-build" in output
    assert "filament" not in output


def test_an_unknown_feature_name_lists_the_available_ones(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(
        project,
        tmp_path,
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--features",
        "flamingo",
        "--dry-run",
    )
    assert code == 1
    error = capsys.readouterr().err
    assert "flamingo" in error
    assert "filament" in error
    assert "node-build" in error


def test_an_empty_features_value_is_a_usage_error(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--features=` is refused rather than read as "none, deliberately".

    Nobody types `--features=` on purpose; it is what `--features=$FEATURES`
    becomes when a script's environment variable is unset, and treating that
    the same as `--no-features` would hide exactly the kind of mistake that
    costs someone an afternoon. `--no-features` remains the one CLI spelling
    for "disable detection"; this is a different, narrower error from the
    `--features`-and-`--no-features`-together one below.
    """
    code = invoke(
        project,
        tmp_path,
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--features",
        "",
        "--dry-run",
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "--features" in error
    assert "--no-features" in error


def test_a_features_value_that_is_only_commas_and_whitespace_is_a_usage_error(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not just the empty string: anything that reduces to zero names errors.

    `--features=` , `--features=,,` and `--features=  ` are all shapes a
    broken shell expansion can produce; all of them are refused the same
    way, not just the literal empty string.
    """
    code = invoke(
        project,
        tmp_path,
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--features",
        " , , ",
        "--dry-run",
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "--no-features" in error


def test_features_and_no_features_together_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--features",
            "node-build",
            "--no-features",
            "--dry-run",
        ]
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "--features" in error
    assert "--no-features" in error


def test_build_is_equivalent_to_the_long_form(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`build` must produce exactly the plan `--steps=setup --features=node-build
    --only-feature-setup` does.

    The long form grew a third flag in the final whole-branch review:
    `--steps=setup --features=node-build` alone (the old long form) plans the
    *whole* setup step, including the laravel profile's own `composer
    install`/`php artisan migrate --force`/etc - not just node-build's two
    commands - so it was never actually equivalent to what design doc §6
    promises ("rebuild the frontend, nothing else"). `--only-feature-setup`
    is what makes the equivalence hold; `test_build_only_runs_feature_
    contributed_setup_commands` below pins the behavioural half of that, this
    test pins that `build` and its now-three-flag long form stay one code
    path.

    Writing this as a byte-for-byte comparison of the two dry-run renders -
    rather than, say, asserting each output separately contains "node-build" -
    is what would catch a future change applied to one path and not the other.
    """
    _write_node_build(project)
    base = [
        "--project-root",
        str(project),
        "--home",
        str(tmp_path / "home"),
        "--profile-root",
        str(PROFILE_ROOT),
        "--feature-root",
        str(FEATURE_ROOT),
        "--type",
        "laravel",
        "--dry-run",
        "--yes",
    ]
    long_form_code = main(
        [*base, "--steps", "setup", "--features", "node-build", "--only-feature-setup"]
    )
    long_form_output = capsys.readouterr().out
    assert long_form_code == 0

    alias_code = main([*base, "build"])
    alias_output = capsys.readouterr().out
    assert alias_code == 0

    assert alias_output == long_form_output
    assert "node-build" in alias_output


def test_build_only_runs_feature_contributed_setup_commands(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bug this review found by hand: `ddev sync build` used to plan the
    *whole* setup step, which on Laravel includes `php artisan migrate
    --force` - an unprompted migration for someone who typed `build` to
    re-run Vite. `build` must restrict the setup step to commands a feature
    contributed; the profile's own setup commands must not appear at all.
    """
    _write_node_build(project)
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--feature-root",
            str(FEATURE_ROOT),
            "--type",
            "laravel",
            "--dry-run",
            "--yes",
            "build",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "node-build" in output
    assert "migrate" not in output
    assert "composer install" not in output
    assert "[profile:laravel]" not in output


def test_build_hands_the_availability_probe_the_flags_the_run_will_use(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build` is `--steps=setup --only-feature-setup`, and the probe only
    answers the question the run asks if it is given the same flags. Wrapping
    the real function (rather than replacing it) records what `build_cmd`'s
    forced options actually deliver, while still letting the run proceed.

    Whether `setup` can ever come out *unavailable* under `build` is a
    separate question that no CLI-level test can reach: forcing
    `--features=node-build` either activates the feature, which contributes
    setup commands, or fails earlier with "no lockfile found" when the project
    has nothing to build. The probe-level test in `tests/engine/test_plan.py`
    pins the case the CLI cannot construct.
    """
    from ddev_sync.cli import main as cli_main
    from ddev_sync.engine.plan import available_selection_groups

    seen: list[dict[str, object]] = []

    def record(**kwargs: object) -> frozenset[str]:
        seen.append(kwargs)
        return available_selection_groups(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_main, "available_selection_groups", record)
    _write_node_build(project)
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--feature-root",
            str(FEATURE_ROOT),
            "--type",
            "laravel",
            "--dry-run",
            "--yes",
            "build",
        ]
    )
    assert code == 0
    assert len(seen) == 1
    assert seen[0]["only_feature_setup"] is True
    activated = seen[0]["features"].activated  # type: ignore[attr-defined]
    assert [entry.feature.name for entry in activated] == ["node-build"]


def test_build_on_a_project_with_nothing_to_build_still_fails_before_planning(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pins what `build` does today when there is nothing to build, so a later
    change to the availability rule cannot quietly alter it: the forced
    node-build feature refuses at context-building time, before any selection
    is resolved, and the exit code is non-zero rather than a silent success.
    """
    code = main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            "--feature-root",
            str(FEATURE_ROOT),
            "--type",
            "laravel",
            "--dry-run",
            "--yes",
            "build",
        ]
    )
    assert code == 1
    assert "no lockfile found" in capsys.readouterr().err


def test_only_feature_setup_combined_with_build_is_a_usage_error(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--only-feature-setup", "--dry-run", "--yes", "build")
    assert code == 2
    error = capsys.readouterr().err
    assert "--only-feature-setup" in error
    assert "build" in error


def test_steps_combined_with_build_is_a_usage_error(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--steps` before `build` is not silently discarded; it is refused.

    This is the bug the coordinator's review caught by hand:
    `ddev-sync --steps db --dry-run --yes build` used to exit 0 and plan
    only `setup`, with the user's `--steps db` vanishing with no warning -
    precisely the failure shape the module docstring opens with.
    """
    code = invoke(project, tmp_path, "--steps", "db", "--dry-run", "--yes", "build")
    assert code == 2
    error = capsys.readouterr().err
    assert "--steps" in error
    assert "build" in error


def test_features_combined_with_build_is_a_usage_error(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--features", "node-build", "--dry-run", "--yes", "build")
    assert code == 2
    error = capsys.readouterr().err
    assert "--features" in error
    assert "build" in error


def test_no_features_combined_with_build_is_a_usage_error(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = invoke(project, tmp_path, "--no-features", "--dry-run", "--yes", "build")
    assert code == 2
    error = capsys.readouterr().err
    assert "--no-features" in error
    assert "build" in error
