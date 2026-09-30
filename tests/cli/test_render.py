import os
from pathlib import Path

from ddev_sync.cli.render import render_plan
from ddev_sync.config.merge import merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.plan import Plan, PlannedStep, build_plan
from ddev_sync.engine.steps import StepKind, steps_for_selection
from ddev_sync.profiles.registry import ProfileRegistry

GOLDEN = Path(__file__).parent / "golden"


def step(index: int, kind: StepKind, where: str, detail: str, source: str) -> PlannedStep:
    return PlannedStep(index=index, kind=kind, where=where, detail=detail, source=source)


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
