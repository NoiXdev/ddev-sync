import os
from pathlib import Path

from ddev_sync.cli.render import render_doctor, render_plan
from ddev_sync.config.merge import merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.plan import Plan, PlannedStep, build_plan
from ddev_sync.engine.steps import StepKind, steps_for_selection
from ddev_sync.operations.preflight import PreflightReport
from ddev_sync.profiles.registry import ProfileRegistry

GOLDEN = Path(__file__).parent / "golden"


def step(
    index: int, kind: StepKind, where: str, detail: str, source: str, *, is_hook: bool = False
) -> PlannedStep:
    return PlannedStep(
        index=index, kind=kind, where=where, detail=detail, source=source, is_hook=is_hook
    )


def small_plan(notices: tuple[str, ...] = ()) -> Plan:
    return Plan(
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        profile_label="typo3@12",
        project_name="acme",
        steps=(
            step(1, StepKind.PREFLIGHT, "remote", "ssh deploy@web01.example", "built-in"),
            step(2, StepKind.DB_DUMP, "remote", "mysqldump $TYPO3_DB_NAME", "profile:typo3@12"),
        ),
        notices=notices,
    )


def test_the_header_names_target_and_profile() -> None:
    output = render_plan(small_plan())
    assert "Plan for target 'production'" in output
    assert "typo3@12" in output
    assert "Nothing will be executed." in output


def test_every_step_is_numbered_and_shows_kind_where_and_detail() -> None:
    output = render_plan(small_plan())
    assert " 1  preflight" in output
    assert " 2  db.dump" in output
    assert "remote" in output
    assert "mysqldump $TYPO3_DB_NAME" in output


def test_the_sources_legend_lists_each_distinct_source_once() -> None:
    output = render_plan(small_plan())
    legend = output.rsplit("Sources:", maxsplit=1)[1]
    assert legend.count("profile:typo3@12") == 1
    assert "built-in" in legend


def test_notices_appear_above_the_steps() -> None:
    output = render_plan(small_plan(notices=("typo3 14 detected - using typo3@12",)))
    notice_line = output.index("typo3 14 detected")
    first_step = output.index(" 1  preflight")
    assert notice_line < first_step


def test_a_plan_with_no_notices_prints_none() -> None:
    assert "detected" not in render_plan(small_plan())


def test_the_output_contains_no_ansi_escapes() -> None:
    assert "\x1b[" not in render_plan(small_plan(notices=("a notice",)))


def test_the_header_shows_the_port_when_the_target_has_one() -> None:
    output = render_plan(small_plan())
    assert "deploy@web01.example:22  /srv/app/" in output


def test_the_header_omits_the_port_for_an_ssh_alias_target() -> None:
    plan = Plan(
        target=ResolvedTarget(
            name="production",
            host="app-prod",
            via_ssh_alias=True,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        profile_label="generic",
        project_name="acme",
        steps=(),
    )
    output = render_plan(plan)
    assert "app-prod  /srv/app/" in output
    assert "app-prod:22" not in output
    assert "app-prod:" not in output


def test_the_port_and_path_are_separated_with_spaces() -> None:
    """Port and path must be separated so readers can distinguish scp-style host:path."""
    output = render_plan(small_plan())
    # With port: should have two spaces between port and path
    assert "deploy@web01.example:22  /srv/app/" in output
    # Verify the concatenated form is NOT present
    assert "deploy@web01.example:22/srv/app/" not in output

    # For ssh alias (no port), should also have two spaces
    alias_plan = Plan(
        target=ResolvedTarget(
            name="production",
            host="app-prod",
            via_ssh_alias=True,
            path="/srv/acme/",
            stage=Stage.PRODUCTION,
        ),
        profile_label="generic",
        project_name="myapp",
        steps=(),
    )
    alias_output = render_plan(alias_plan)
    assert "app-prod  /srv/acme/" in alias_output


def test_each_step_line_names_its_own_source() -> None:
    output = render_plan(small_plan())
    lines = output.splitlines()
    preflight_line = next(line for line in lines if " 1  preflight" in line)
    dump_line = next(line for line in lines if " 2  db.dump" in line)
    assert preflight_line.endswith("[built-in]")
    assert dump_line.endswith("[profile:typo3@12]")


def test_a_hook_step_shows_hook_in_the_kind_column_not_its_parents_kind() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=(
            step(
                1,
                StepKind.DB_IMPORT,
                "local",
                "typo3 cache:flush",
                "profile:typo3@12",
                is_hook=True,
            ),
        ),
    )
    output = render_plan(plan)
    assert " 1  hook" in output
    assert "db.import" not in output.split("Nothing will be executed.")[1]


def test_an_empty_plan_still_renders_a_header() -> None:
    empty = Plan(
        target=small_plan().target,
        profile_label="generic",
        project_name="acme",
        steps=(),
    )
    output = render_plan(empty)
    assert "Plan for target 'production'" in output
    assert "no steps selected" in output


def write_or_compare(name: str, actual: str) -> None:
    """Compare against the golden file, or create it when UPDATE_GOLDEN is set.

    Creating and comparing live in one helper so that regenerating every
    snapshot is a single environment variable rather than a manual edit.
    """
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{name} differs from the golden file. If the change is intended, "
        f"run: UPDATE_GOLDEN=1 uv run pytest {__file__}"
    )


PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"


def golden_plan(profile_name: str, major: str | None, selection: list[str]) -> Plan:
    profile = ProfileRegistry(PROFILE_ROOT).resolve(profile_name, major)
    return build_plan(
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        project=DdevProject(
            name="acme", type=profile_name, docroot="public", root=Path("/var/www/html")
        ),
        profile=profile,
        merged=merge_layers([*profile.layers]),
        steps=steps_for_selection(selection),
    )


def test_golden_generic_database_only() -> None:
    write_or_compare("generic-db-only.txt", render_plan(golden_plan("generic", None, ["db"])))


def test_golden_typo3_12_full() -> None:
    write_or_compare(
        "typo3-12-full.txt",
        render_plan(golden_plan("typo3", "12", ["db", "files", "sanitize", "setup"])),
    )


def _target() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
    )


def test_render_doctor_marks_every_check_ok_when_everything_passes() -> None:
    report = PreflightReport(ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3)
    output = render_doctor(report, _target())
    assert "OK" in output
    assert "FAIL" not in output


def test_render_doctor_marks_a_failing_check_with_whitespace_before_the_label() -> None:
    """Regression test: 'OK  ' and 'FAIL' used to be two string literals of
    different lengths baked directly into the format string, so a failing
    line ran straight into its label with no separating space
    ('FAILssh connection'). Checking for the substrings alone would not have
    caught that; this checks the actual layout.
    """
    report = PreflightReport(ssh_ok=False, remote_path_ok=False, free_bytes=1024)
    output = render_doctor(report, _target())
    failing_lines = [line for line in output.splitlines() if "FAIL" in line]
    assert failing_lines
    for line in failing_lines:
        after_marker = line.split("FAIL", 1)[1]
        assert after_marker[:1].isspace(), f"no space after FAIL in: {line!r}"


def test_render_doctor_aligns_ok_and_fail_markers_to_the_same_column() -> None:
    """OK and FAIL must land the label in the same column regardless of
    which marker was used - a fixed field width is what a future third
    state (e.g. "SKIP") cannot break, unlike two independently-sized string
    literals.
    """
    ok_report = PreflightReport(ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3)
    fail_report = PreflightReport(ssh_ok=False, remote_path_ok=False, free_bytes=1024)
    ok_line = next(
        line
        for line in render_doctor(ok_report, _target()).splitlines()
        if "ssh connection" in line
    )
    fail_line = next(
        line
        for line in render_doctor(fail_report, _target()).splitlines()
        if "ssh connection" in line
    )
    assert ok_line.index("ssh connection") == fail_line.index("ssh connection")
