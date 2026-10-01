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


def test_steps_accepts_a_comma_separated_list(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(project, tmp_path, "--dry-run", "--steps", "db,files") == 0
    assert "db.dump" in capsys.readouterr().out


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
    concluding their project was not recognised. `--no-features` and the
    `defaults.detect_features: false` kill switch both set that same flag,
    so both render that same notice. The brief's own sketch of this test
    ("asserts 'Features' does not appear in the output") cannot hold given
    that shipped behaviour - the notice line starts with the literal word
    "Features" - so this asserts the more precise thing that is actually
    true: the per-feature line for `filament` is gone, replaced by the
    disabled notice.
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
    assert "detection disabled" in output


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


def test_an_empty_features_value_means_no_features_without_disabling_detection(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--features=` means "none, and I mean it" - not a mistake, not `--no-features`.

    It carries the same meaning `features: []` already has in project config
    (`features/activation.py::parse_feature_key`'s docstring). The
    distinction from `--no-features` is observable: unlike `--no-features`,
    this does not flip `features_detection_enabled`, so the plan shows no
    "Features" block at all rather than the "detection disabled" notice.
    """
    _write_filament(project)
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
        "--yes",
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "filament" not in output
    assert "Features" not in output


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
    """`build` must produce exactly the plan `--steps=setup --features=node-build` does.

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
    long_form_code = main([*base, "--steps", "setup", "--features", "node-build"])
    long_form_output = capsys.readouterr().out
    assert long_form_code == 0

    alias_code = main([*base, "build"])
    alias_output = capsys.readouterr().out
    assert alias_code == 0

    assert alias_output == long_form_output
    assert "node-build" in alias_output
