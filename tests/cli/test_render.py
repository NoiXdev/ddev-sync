import json
import os
import re
from pathlib import Path

from ddev_sync.cli.render import render_doctor, render_features, render_plan, render_targets
from ddev_sync.config.merge import merge_layers
from ddev_sync.config.models import DeployerRef, Provider, ResolvedTarget, Stage, TargetSpec
from ddev_sync.config.targets import CollectedTarget
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.plan import Plan, PlannedStep, PostSyncEntry, build_plan
from ddev_sync.engine.steps import StepKind, steps_for_selection
from ddev_sync.features.activation import Request
from ddev_sync.features.registry import Feature, FeatureRegistry
from ddev_sync.features.resolver import ActivatedFeature, resolve_features
from ddev_sync.operations.preflight import PreflightReport
from ddev_sync.profiles.registry import ProfileRegistry

GOLDEN = Path(__file__).parent / "golden"
FEATURE_ROOT = Path(__file__).parent / "features"


def activated(name: str, reason: str) -> ActivatedFeature:
    """A minimal `ActivatedFeature` for rendering tests that do not need to
    exercise detection or settings resolution - only `render_features` reads
    `.feature.name` and `.reason`.
    """
    return ActivatedFeature(feature=Feature(name=name), settings={}, reason=reason)


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


def test_the_sources_legend_includes_a_source_that_only_contributes_post_sync() -> None:
    # A layer whose sole contribution is a `hooks.post_sync` command never
    # becomes a `PlannedStep` (see `Plan.post_sync`'s own docstring for why),
    # so `_legend`, which used to walk only `plan.steps`, silently dropped it
    # from the "Sources:" line - a step ran with no indication of where it
    # came from. The legend must name every source the plan will execute
    # something from, numbered step or post_sync epilogue alike.
    plan = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=small_plan().steps,
        post_sync=(
            PostSyncEntry(detail="cleanup", source="feature:cleanup-only"),
            # Same source as the first numbered step above ("built-in") -
            # already contributed something, so it must not be listed twice.
            PostSyncEntry(detail="also cleanup", source="built-in"),
        ),
    )
    output = render_plan(plan)
    legend_line = next(line for line in output.splitlines() if line.strip().startswith("Sources:"))
    assert "[feature:cleanup-only]" in legend_line
    assert legend_line.count("[built-in]") == 1


def test_post_sync_entries_render_as_an_epilogue_not_a_numbered_step() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=small_plan().steps,
        post_sync=(PostSyncEntry(detail="cleanup", source=".ddev/ddev-sync.yaml"),),
    )
    output = render_plan(plan)
    assert "post_sync (always runs, regardless of outcome):" in output
    assert "cleanup   [.ddev/ddev-sync.yaml]" in output
    # Not folded into the numbered step table.
    assert "db.dump" in output  # sanity: the real steps are still there
    post_sync_section = output.rsplit("post_sync (always runs", maxsplit=1)[1]
    assert "cleanup" in post_sync_section


def test_no_post_sync_entries_means_no_epilogue() -> None:
    assert "post_sync" not in render_plan(small_plan())


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


def test_render_features_with_nothing_active_renders_nothing() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="laravel",
        project_name="acme",
        steps=(),
    )
    assert render_features(plan) == ""


def test_render_features_when_detection_is_disabled_by_the_config_file_says_so_once() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="laravel",
        project_name="acme",
        steps=(),
        features_detection_enabled=False,
        features_disabled_reason="~/.ddev/ddev-sync/config.yaml",
    )
    output = render_features(plan)
    assert output == "  Features  (detection disabled by ~/.ddev/ddev-sync/config.yaml)"
    assert output.count("\n") == 0


def test_render_features_when_detection_is_disabled_by_the_flag_blames_the_flag_not_a_file() -> (
    None
):
    # T6/T7 seam: the notice used to always name the config file, even for a
    # run disabled purely by `--no-features` - a flag the user typed, blamed
    # on a file that need not exist. `FeatureResolution.disabled_reason`
    # carries which kill switch actually fired (spec §2) through to here.
    plan = Plan(
        target=small_plan().target,
        profile_label="laravel",
        project_name="acme",
        steps=(),
        features_detection_enabled=False,
        features_disabled_reason="--no-features",
    )
    output = render_features(plan)
    assert output == "  Features  (detection disabled by --no-features)"
    assert "config.yaml" not in output


def test_render_features_lists_each_active_feature_with_its_reason() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="laravel",
        project_name="acme",
        steps=(),
        features=(
            activated("node-build", "detected: package.json"),
            activated("filament", "detected: filament/filament"),
        ),
    )
    lines = render_features(plan).splitlines()
    assert len(lines) == 2
    assert lines[0] == "  Features  node-build  detected: package.json"
    assert lines[1] == "            filament    detected: filament/filament"


def test_render_plan_shows_the_feature_block_above_the_steps() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=small_plan().steps,
        features=(activated("node-build", "detected: package.json"),),
    )
    output = render_plan(plan)
    features_line = output.index("Features")
    first_step = output.index(" 1  preflight")
    assert features_line < first_step
    # A blank line separates the feature block from the step table, matching
    # how notices are separated from it above.
    assert "node-build  detected: package.json\n\n" in output


def test_a_plan_with_no_active_features_has_no_features_block() -> None:
    assert "Features" not in render_plan(small_plan())


def test_render_plan_shows_the_detection_disabled_notice_above_the_steps() -> None:
    plan = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=small_plan().steps,
        features_detection_enabled=False,
    )
    output = render_plan(plan)
    notice_line = output.index("detection disabled")
    first_step = output.index(" 1  preflight")
    assert notice_line < first_step


def _line_below_the_features_block(plan: Plan) -> str:
    """The line `render_plan` prints directly under the whole Features block."""
    rendered = render_plan(plan).splitlines()
    block = render_features(plan).splitlines()
    assert block, "this plan renders no Features block to look under"
    return rendered[rendered.index(block[0]) + len(block)]


def test_render_plan_puts_a_blank_line_below_every_features_block_it_prints() -> None:
    """The renderer's half of the contract `_active_features` parses against.

    `tests/matrix/test_matrix.py::_active_features` ends the block at the
    first line that is not indented to the continuation column, and this
    blank line is that line. Dropping it does not fail a single test in
    `tests/matrix/test_active_features.py`: a step line is indented two or
    three columns, so the block would end there instead and the same names
    would come back. Both shapes `render_features` can emit are checked,
    because the golden files carry only the active list.
    """
    active = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=small_plan().steps,
        features=(
            activated("node-build", "detected: package.json"),
            activated("filament", "detected: filament/filament"),
        ),
    )
    disabled = Plan(
        target=small_plan().target,
        profile_label="typo3@12",
        project_name="acme",
        steps=small_plan().steps,
        features_detection_enabled=False,
        features_disabled_reason="--no-features",
    )
    for shape, plan in (("active list", active), ("disabled notice", disabled)):
        assert _line_below_the_features_block(plan) == "", (
            f"the {shape} block is not followed by a blank line"
        )


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


def test_golden_laravel_full() -> None:
    write_or_compare(
        "laravel-full.txt",
        render_plan(golden_plan("laravel", None, ["db", "files", "sanitize", "setup"])),
    )


def golden_plan_with_features(tmp_path: Path) -> Plan:
    """Like `golden_plan`, but against a real project directory so that
    detection - which reads `composer.lock`, `package.json` and a lockfile
    straight off disk - has something real to find. Pins the feature block
    and the `feature:<name>` provenance labels in the `Sources:` legend,
    neither of which the three existing goldens exercise: none of `generic`,
    `typo3`, or `laravel` (built via `golden_plan`, against a project
    directory that does not exist) ever has an active feature.
    """
    (tmp_path / "composer.lock").write_text(
        json.dumps(
            {
                "packages": [
                    {"name": "laravel/framework", "version": "v11.30.0"},
                    {"name": "filament/filament", "version": "v3.2.0"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    project = DdevProject(name="acme", type="laravel", docroot="public", root=tmp_path)
    profile = ProfileRegistry(PROFILE_ROOT).resolve("laravel", None)
    resolution = resolve_features(FeatureRegistry(FEATURE_ROOT), project, profile.name, None, True)
    return build_plan(
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        project=project,
        profile=profile,
        merged=merge_layers([*profile.layers, *resolution.layers]),
        steps=steps_for_selection(["setup"]),
        features=resolution,
    )


def test_golden_laravel_with_features(tmp_path: Path) -> None:
    write_or_compare("laravel-with-features.txt", render_plan(golden_plan_with_features(tmp_path)))


def golden_plan_typo3_with_explicit_feature(tmp_path: Path) -> Plan:
    """A TYPO3 project that explicitly enables `typo3-processedfiles` via
    (a stand-in for) `.ddev/ddev-sync.yaml`, pinning the `from <source>`
    provenance string that an explicit request produces.

    `typo3-processedfiles` has no `detect` block by design (see the real
    file's comment: whether stale processed images are worth clearing is a
    judgement, not a property of the project), so it can only ever be
    activated this way - it is never the `detected: ...` kind. That also
    means this golden cannot show both reason strings in one render: once a
    project supplies `features:` (here, `requests` is not `None`), detection
    is replaced outright for the whole resolution, never merged with it
    (`features/resolver.py::resolve_features`). So "alongside a detected
    one" is satisfied across the two golden files together -
    `laravel-with-features.txt` already pins `detected: ...`, and this one
    pins `from ...` - rather than within a single one.
    """
    project = DdevProject(name="acme", type="typo3", docroot="public", root=tmp_path)
    profile = ProfileRegistry(PROFILE_ROOT).resolve("typo3", None)
    requests = [Request("typo3-processedfiles", {}, ".ddev/ddev-sync.yaml")]
    resolution = resolve_features(
        FeatureRegistry(FEATURE_ROOT), project, profile.name, requests, True
    )
    assert [a.reason for a in resolution.activated] == ["from .ddev/ddev-sync.yaml"]
    return build_plan(
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        project=project,
        profile=profile,
        merged=merge_layers([*profile.layers, *resolution.layers]),
        steps=steps_for_selection(["sanitize", "setup"]),
        features=resolution,
    )


def test_golden_typo3_with_explicit_feature(tmp_path: Path) -> None:
    write_or_compare(
        "typo3-with-explicit-feature.txt",
        render_plan(golden_plan_typo3_with_explicit_feature(tmp_path)),
    )


def test_a_feature_contributing_only_a_hook_still_reaches_the_legend(tmp_path: Path) -> None:
    """`render._legend` has a known, tracked gap: a layer whose only
    contribution is a `hooks.post_sync` entry never appears in it, because
    `post_sync` is rendered separately as `plan.post_sync` and never becomes
    a `PlannedStep` (see the module notes carried into Plan 3). This is a
    *different* hook: an ordinary phase hook such as `post_db_import` is
    expanded by `build_plan`/`_Builder.hooks` into a real `PlannedStep` with
    `is_hook=True`, which `_legend` already walks like any other step. This
    test pins that a feature contributing only such a hook is not a second
    instance of the same gap - so a later reader does not "fix" the wrong
    one.
    """
    (tmp_path / "hook-only-marker.txt").write_text("", encoding="utf-8")
    project = DdevProject(name="acme", type="laravel", docroot="public", root=tmp_path)
    profile = ProfileRegistry(PROFILE_ROOT).resolve("laravel", None)
    resolution = resolve_features(FeatureRegistry(FEATURE_ROOT), project, profile.name, None, True)
    assert [a.feature.name for a in resolution.activated] == ["hook-only"]
    plan = build_plan(
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        project=project,
        profile=profile,
        merged=merge_layers([*profile.layers, *resolution.layers]),
        steps=steps_for_selection(["db"]),
        features=resolution,
    )
    output = render_plan(plan)
    legend = output.rsplit("Sources:", maxsplit=1)[1]
    assert "feature:hook-only" in legend
    assert "echo hook-only ran" in output


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


def test_render_doctor_omits_the_sanitize_tables_row_when_nothing_was_probed() -> None:
    report = PreflightReport(ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3)
    assert "sanitize tables" not in render_doctor(report, _target())


def test_the_credentials_row_sits_between_disk_space_and_sanitize_tables() -> None:
    report = PreflightReport(
        ssh_ok=True,
        remote_path_ok=True,
        free_bytes=100 * 1024**3,
        credentials_ok=True,
        credentials_detail="env: deploy@db.internal:3306/app",
        sanitize_tables_ok=True,
        sanitize_tables_detail="1 required table present",
    )
    lines = render_doctor(report, _target()).splitlines()
    labels = [line.split()[1] for line in lines if line.strip().startswith(("OK", "FAIL"))]
    assert labels.index("credentials") == labels.index("disk") + 1
    assert labels.index("credentials") < labels.index("sanitize")


def test_no_credentials_row_when_the_probe_did_not_run() -> None:
    report = PreflightReport(ssh_ok=False, remote_path_ok=False, free_bytes=0)
    assert "credentials" not in render_doctor(report, _target())


def test_render_doctor_shows_sanitize_tables_ok() -> None:
    report = PreflightReport(
        ssh_ok=True,
        remote_path_ok=True,
        free_bytes=50 * 1024**3,
        sanitize_tables_ok=True,
        sanitize_tables_detail="3 required tables present",
    )
    output = render_doctor(report, _target())
    line = next(line for line in output.splitlines() if "sanitize tables" in line)
    assert line.strip().startswith("OK")
    assert "3 required tables present" in line


def test_render_doctor_shows_a_failing_sanitize_tables_check() -> None:
    report = PreflightReport(
        ssh_ok=True,
        remote_path_ok=True,
        free_bytes=50 * 1024**3,
        sanitize_tables_ok=False,
        sanitize_tables_detail=(
            "sanitize rule 'anonymize-users' requires table 'wp_users', which does not exist"
        ),
    )
    output = render_doctor(report, _target())
    line = next(line for line in output.splitlines() if "sanitize tables" in line)
    assert line.strip().startswith("FAIL")
    assert "wp_users" in line


def test_the_doctor_checklist_shows_the_web_and_agent_rows() -> None:
    report = PreflightReport(
        True,
        True,
        50 * 1024**3,
        web_ok=False,
        web_detail="ddev-acme-web did not answer: No such container",
        agent_ok=True,
        agent_detail="/home/.ssh-agent/proxy-socket",
    )
    output = render_doctor(report, _target())
    assert "web container" in output
    assert "ssh agent" in output
    assert "No such container" in output
    assert "proxy-socket" in output


def test_rows_that_were_not_probed_are_left_out() -> None:
    output = render_doctor(PreflightReport(True, True, 50 * 1024**3), _target())
    assert "web container" not in output
    assert "ssh agent" not in output


def test_render_doctor_shows_the_host_beside_a_working_ssh_connection() -> None:
    # A detail that differs from the target's host, so a renderer that ignored
    # `ssh_detail` and printed the host itself would not pass.
    report = PreflightReport(
        ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3, ssh_detail="reached via bastion"
    )
    line = next(
        line for line in render_doctor(report, _target()).splitlines() if "ssh connection" in line
    )
    assert line.strip().startswith("OK")
    assert line.endswith("reached via bastion")
    assert "web01.example" not in line


def test_render_doctor_shows_why_the_ssh_connection_failed() -> None:
    report = PreflightReport(
        ssh_ok=False,
        remote_path_ok=False,
        free_bytes=50 * 1024**3,
        ssh_detail="web01.example: Host key verification failed.",
    )
    line = next(
        line for line in render_doctor(report, _target()).splitlines() if "ssh connection" in line
    )
    assert line.strip().startswith("FAIL")
    assert "Host key verification failed." in line


def test_render_doctor_falls_back_to_the_host_when_a_report_has_no_ssh_detail() -> None:
    # Reports built before the field existed still render a useful row.
    report = PreflightReport(ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3)
    line = next(
        line for line in render_doctor(report, _target()).splitlines() if "ssh connection" in line
    )
    assert line.endswith("web01.example")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _mixed_report() -> PreflightReport:
    """One report with both markers, so a single render shows OK and FAIL rows."""
    return PreflightReport(
        ssh_ok=True,
        remote_path_ok=False,
        free_bytes=50 * 1024**3,
        web_ok=True,
        web_detail="web answers",
    )


def test_render_doctor_is_plain_unless_colour_is_asked_for() -> None:
    assert "\x1b" not in render_doctor(_mixed_report(), _target())


def test_render_doctor_colours_ok_green_and_fail_red_when_asked() -> None:
    lines = render_doctor(_mixed_report(), _target(), colour=True).splitlines()
    ssh = next(line for line in lines if "ssh connection" in line)
    path = next(line for line in lines if "deploy path" in line)
    assert ssh.startswith("  \x1b[32mOK\x1b[0m")
    assert path.startswith("  \x1b[31mFAIL\x1b[0m")


def test_render_doctor_colours_nothing_but_the_markers() -> None:
    plain = render_doctor(_mixed_report(), _target())
    coloured = render_doctor(_mixed_report(), _target(), colour=True)
    # Removing the escape sequences must give back the plain output exactly:
    # no label, detail or header carries any colour of its own.
    assert _strip_ansi(coloured) == plain
    # And each marker row carries exactly one painted span - the marker.
    marker_rows = [line for line in coloured.splitlines() if "\x1b" in line]
    assert len(marker_rows) == plain.count("OK") + plain.count("FAIL")
    assert all(line.count("\x1b[0m") == 1 for line in marker_rows)
    header = coloured.splitlines()[0]
    assert "\x1b" not in header


def test_render_doctor_keeps_the_label_column_where_it_is_with_colour_on() -> None:
    """The alignment guarantee: colour must not move the label column.

    ANSI escapes are characters, so padding an already-coloured marker to its
    column width counts the escape bytes towards the width and adds no padding
    at all - the label then runs into the marker. The column is measured on
    what a terminal shows (the escapes removed) and, separately, on the raw
    text, where the padding must follow the reset sequence.
    """
    plain = render_doctor(_mixed_report(), _target()).splitlines()
    coloured = render_doctor(_mixed_report(), _target(), colour=True).splitlines()
    assert len(plain) == len(coloured)
    rows = 0
    for plain_line, coloured_line in zip(plain, coloured, strict=True):
        for label in ("ssh connection", "deploy path", "disk space", "web container"):
            if label in plain_line:
                rows += 1
                visible = _strip_ansi(coloured_line)
                assert visible.index(label) == plain_line.index(label)
                # Raw text: after the reset comes plain padding that brings
                # the marker cell to width 6, then the label - never zero
                # spaces, which is what padding the coloured string produces.
                marker = plain_line.split()[0]
                after_reset = coloured_line.split("\x1b[0m", 1)[1]
                assert after_reset.startswith(" " * (6 - len(marker)) + label)
    assert rows == 4


def test_render_targets_shows_a_deployer_block_as_a_declaration() -> None:
    # The listing does no I/O and must not start to: resolving would tie it to
    # the project's `deploy.php` evaluating cleanly, and a broken recipe would
    # hide the configuration needed to fix it. So this shows what was declared,
    # not what it resolves to - the dry-run header shows the resolution.
    collected = {
        "prod": CollectedTarget(
            spec=TargetSpec(deployer=DeployerRef(host="web-1"), stage=Stage.PRODUCTION),
            source="~/.ddev/ddev-sync/config.yaml",
        )
    }
    assert render_targets(collected) == (
        "  Targets\n\n  prod  from deployer: web-1   [~/.ddev/ddev-sync/config.yaml]\n"
    )


def test_render_targets_shows_a_deployer_block_with_no_alias() -> None:
    # Without the branch this renders `?:?`, which reads like a broken
    # configuration rather than a deliberate one. Exact equality also pins that
    # no separator is left dangling after "deployer".
    collected = {
        "prod": CollectedTarget(
            spec=TargetSpec(deployer=DeployerRef(), stage=Stage.PRODUCTION),
            source="~/.ddev/ddev-sync/config.yaml",
        )
    }
    assert render_targets(collected) == (
        "  Targets\n\n  prod  from deployer   [~/.ddev/ddev-sync/config.yaml]\n"
    )


def test_render_targets_keeps_the_literal_endpoint_and_its_alignment() -> None:
    # Pins the rendering of literal targets, including the `?` fallbacks and the
    # column width, so that sharing the endpoint expression with the picker
    # cannot quietly change what `ddev sync targets` has always printed.
    # Coverage cannot see inside `a or b` (a short-circuit operand is not a
    # branch), so each operand of the user and host expressions needs a case of
    # its own: `alias_user` is the only one where the alias must suppress a
    # `user@` that is otherwise written, and `alias_wins` the only one where
    # the alias must beat a `host` beside it.
    collected = {
        "a": CollectedTarget(
            spec=TargetSpec(host="web01.example", user="deploy", path="/srv/app/"),
            source="project",
        ),
        "alias_user": CollectedTarget(
            spec=TargetSpec(ssh_alias="prod", user="deploy", path="/srv/x"), source="global"
        ),
        "alias_wins": CollectedTarget(
            spec=TargetSpec(ssh_alias="prod", host="h.example", path="/srv/x"), source="global"
        ),
        "no_user": CollectedTarget(spec=TargetSpec(host="h.example", path="/srv/y"), source="g"),
        "no_path": CollectedTarget(spec=TargetSpec(host="h.example", user="u"), source="global"),
        "bare": CollectedTarget(spec=TargetSpec(), source="global"),
    }
    assert render_targets(collected) == (
        "  Targets\n"
        "\n"
        "  a           deploy@web01.example:/srv/app/   [project]\n"
        "  alias_user  prod:/srv/x   [global]\n"
        "  alias_wins  prod:/srv/x   [global]\n"
        "  bare        ?:?   [global]\n"
        "  no_path     u@h.example:?   [global]\n"
        "  no_user     h.example:/srv/y   [g]\n"
    )


def test_the_doctor_reports_an_unreadable_path() -> None:
    report = PreflightReport(
        ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3, path_readable=False
    )

    rendered = render_doctor(report, docker_target())

    assert "readable" in rendered
    assert "FAIL" in rendered


def test_the_doctor_names_the_endpoint_for_a_docker_target() -> None:
    """When the endpoint is silent the Docker host usually answered perfectly
    well, so naming the host sends a reader to debug the wrong machine."""
    report = PreflightReport(ssh_ok=False, remote_path_ok=False, free_bytes=50 * 1024**3)

    rendered = render_doctor(report, docker_target())

    assert "endpoint" in rendered


def test_the_doctor_says_a_docker_path_is_inside_the_endpoint() -> None:
    """The one deliberate break with a reader's intuition: under
    `provider: docker`, `path` is the path inside the endpoint, not on the
    host. Someone staring at a correct /data/ while /srv/app/ sits on the host
    concludes the tool is broken."""
    report = PreflightReport(
        ssh_ok=True, remote_path_ok=True, free_bytes=50 * 1024**3, path_readable=True
    )

    rendered = render_doctor(report, docker_target())

    assert "in the endpoint" in rendered


def docker_target() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        provider=Provider.DOCKER,
        host="127.0.0.1",
        user="sync",
        port=2222,
        jump_host="prod.example",
        jump_host_user="deploy",
        path="/data/",
        stage=Stage.PRODUCTION,
    )


def test_a_failing_doctor_row_carries_its_link() -> None:
    report = PreflightReport(ssh_ok=False, remote_path_ok=True, free_bytes=50 * 1024**3)
    lines = render_doctor(report, _target()).splitlines()
    index = next(i for i, line in enumerate(lines) if "ssh connection" in line)
    assert lines[index + 1] == (
        "        More: https://docs.noix.dev/ddev-sync/reference/troubleshooting/"
        "#cannot-reach-the-host-over-ssh"
    )
    assert sum("More:" in line for line in lines) == 1


def test_every_doctor_row_has_a_registered_topic() -> None:
    from ddev_sync.cli.render import _DOCTOR_ROW_TOPICS
    from ddev_sync.docs import TOPICS

    assert set(_DOCTOR_ROW_TOPICS.values()) <= TOPICS
    assert set(_DOCTOR_ROW_TOPICS) == {
        "ssh connection",
        "sync endpoint",
        "deploy path",
        "path readable",
        "disk space",
        "credentials",
        "sanitize tables",
        "web container",
        "ssh agent",
    }
