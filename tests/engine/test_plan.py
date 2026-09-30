from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.plan import Plan, build_plan
from ddev_sync.engine.steps import StepKind, steps_for_selection
from ddev_sync.profiles.registry import ResolvedProfile


def target() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
    )


def project() -> DdevProject:
    return DdevProject(name="acme", type="typo3", docroot="public", root=Path("/var/www/html"))


PROFILE_DATA: dict[str, Any] = {
    "database": {"env_map": {"name": "TYPO3_DB_NAME"}},
    "files": {"paths": [{"remote": "shared/public/fileadmin/", "local": "public/fileadmin/"}]},
    "sanitize": {"rules": [{"name": "clear-sessions", "sql": "TRUNCATE TABLE be_sessions"}]},
    "setup": {"commands": [{"run": "composer install --no-interaction"}]},
}


def plan_for(
    selection: list[str],
    *,
    user_layers: list[LayerDoc] | None = None,
    notice: str | None = None,
) -> Plan:
    profile = ResolvedProfile(
        name="typo3",
        label="typo3@12",
        data=PROFILE_DATA,
        layers=(LayerDoc("profile:typo3@12", PROFILE_DATA),),
        notice=notice,
    )
    layers = [LayerDoc("profile:typo3@12", PROFILE_DATA), *(user_layers or [])]
    return build_plan(
        target=target(),
        project=project(),
        profile=profile,
        merged=merge_layers(layers),
        steps=steps_for_selection(selection),
    )


def kinds(plan: Plan) -> list[str]:
    return [step.kind.value for step in plan.steps]


def test_a_database_only_plan_has_preflight_dump_download_import() -> None:
    assert kinds(plan_for(["db"])) == [
        "preflight",
        "db.dump",
        "db.download",
        "db.import",
    ]


def test_steps_are_numbered_from_one_in_order() -> None:
    plan = plan_for(["db", "files"])
    assert [step.index for step in plan.steps] == list(range(1, len(plan.steps) + 1))


def test_one_files_step_is_emitted_per_configured_path() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"files": {"paths": [{"remote": "shared/public/uploads/", "local": "public/uploads/"}]}},
    )
    plan = plan_for(["files"], user_layers=[extra])
    file_steps = [step for step in plan.steps if step.kind is StepKind.FILES_SYNC]
    assert len(file_steps) == 2
    assert file_steps[0].source == "profile:typo3@12"
    assert file_steps[1].source == ".ddev/ddev-sync.yaml"


def test_a_files_step_shows_the_real_rsync_endpoints() -> None:
    plan = plan_for(["files"])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.FILES_SYNC)
    assert step.where == "rsync"
    assert "/srv/app/shared/public/fileadmin/" in step.detail
    assert "/var/www/html/public/fileadmin/" in step.detail


def test_sanitize_summarises_its_rules_by_name() -> None:
    plan = plan_for(["sanitize"])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.SANITIZE)
    assert step.where == "sql"
    assert "1 rule" in step.detail
    assert "clear-sessions" in step.detail


def test_selecting_sanitize_with_no_rules_emits_no_step() -> None:
    profile = ResolvedProfile(
        name="generic", label="generic", data={}, layers=(LayerDoc("profile:generic", {}),)
    )
    plan = build_plan(
        target=target(),
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:generic", {})]),
        steps=steps_for_selection(["sanitize", "files", "setup"]),
    )
    assert kinds(plan) == ["preflight"]


def test_setup_emits_one_step_per_command() -> None:
    plan = plan_for(["setup"])
    setup_steps = [entry for entry in plan.steps if entry.kind is StepKind.SETUP]
    assert len(setup_steps) == 1
    assert "composer install --no-interaction" in setup_steps[0].detail


def test_the_profile_notice_is_carried_onto_the_plan() -> None:
    plan = plan_for(["db"], notice="typo3 14 detected - using typo3@12")
    assert plan.notices == ("typo3 14 detected - using typo3@12",)


def test_a_plan_without_a_notice_has_none() -> None:
    assert plan_for(["db"]).notices == ()


def test_the_plan_carries_target_profile_and_project_for_display() -> None:
    plan = plan_for(["db"])
    assert plan.target.name == "production"
    assert plan.profile_label == "typo3@12"
    assert plan.project_name == "acme"


def hooks_layer(**hooks: Any) -> LayerDoc:
    return LayerDoc(".ddev/ddev-sync.yaml", {"hooks": hooks})


def test_a_post_hook_lands_immediately_after_its_step() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(post_db_import={"commands": [{"run": "typo3 cache:flush"}]})],
    )
    assert kinds(plan) == [
        "preflight",
        "db.dump",
        "db.download",
        "db.import",
        "db.import",
    ]
    assert plan.steps[-1].detail == "typo3 cache:flush"
    assert plan.steps[-1].source == ".ddev/ddev-sync.yaml"


def test_a_pre_hook_lands_immediately_before_its_step() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(pre_db_dump={"commands": [{"run": "echo starting"}]})],
    )
    assert plan.steps[1].detail == "echo starting"
    assert plan.steps[2].detail.startswith("mysqldump")


def test_hooks_on_steps_that_were_not_selected_do_not_run() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(post_setup={"commands": [{"run": "never"}]})],
    )
    assert all("never" not in step.detail for step in plan.steps)


def test_allow_failure_is_visible_in_the_plan() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[
            hooks_layer(post_db_import={"commands": [{"run": "optional", "allow_failure": True}]})
        ],
    )
    assert plan.steps[-1].detail == "optional  (allow_failure)"


def test_a_named_operation_hook_is_shown_by_its_operation_name() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(post_db_import={"commands": [{"operation": "flush_cache"}]})],
    )
    assert plan.steps[-1].detail == "flush_cache"


def test_a_malformed_hook_entry_is_labelled_rather_than_crashing() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(post_db_import={"commands": [{"unexpected": 1}]})],
    )
    assert plan.steps[-1].detail == "unknown operation"


def test_multiple_hooks_keep_their_configured_order() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[
            hooks_layer(post_db_import={"commands": [{"run": "first"}, {"run": "second"}]})
        ],
    )
    assert [step.detail for step in plan.steps[-2:]] == ["first", "second"]


@pytest.mark.parametrize(
    "files_value",
    ["not-a-list", {"paths": "nope"}, 42],
)
def test_a_files_block_of_the_wrong_shape_yields_no_file_steps(files_value: Any) -> None:
    plan = plan_for(["files"], user_layers=[LayerDoc("bad", {"files": files_value})])
    assert [step for step in plan.steps if step.kind is StepKind.FILES_SYNC] == []


def test_non_mapping_entries_in_a_list_are_ignored() -> None:
    plan = plan_for(
        ["setup"],
        user_layers=[LayerDoc("bad", {"setup": {"commands": ["a bare string"]}})],
    )
    setup_steps = [step for step in plan.steps if step.kind is StepKind.SETUP]
    assert len(setup_steps) == 1
    assert setup_steps[0].detail == "composer install --no-interaction"


def test_a_missing_env_map_falls_back_to_the_default_variable_name() -> None:
    profile = ResolvedProfile(
        name="generic", label="generic", data={}, layers=(LayerDoc("profile:generic", {}),)
    )
    plan = build_plan(
        target=target(),
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:generic", {})]),
        steps=steps_for_selection(["db"]),
    )
    dump = next(step for step in plan.steps if step.kind is StepKind.DB_DUMP)
    assert "$DATABASE_NAME" in dump.detail
    assert dump.source == "built-in"


def test_a_jump_host_target_shows_the_jump_in_preflight() -> None:
    jumped = ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        jump_host="bastion.example",
        jump_host_user="tim",
    )
    profile = ResolvedProfile(
        name="generic", label="generic", data={}, layers=(LayerDoc("profile:generic", {}),)
    )
    plan = build_plan(
        target=jumped,
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:generic", {})]),
        steps=steps_for_selection([]),
    )
    assert "-J tim@bastion.example:22" in plan.steps[0].detail


def test_a_jump_host_target_without_a_user_omits_the_at_sign() -> None:
    jumped = ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        jump_host="bastion.example",
    )
    profile = ResolvedProfile(
        name="generic", label="generic", data={}, layers=(LayerDoc("profile:generic", {}),)
    )
    plan = build_plan(
        target=jumped,
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:generic", {})]),
        steps=steps_for_selection([]),
    )
    assert "-J bastion.example:22" in plan.steps[0].detail


def test_a_sanitize_block_with_several_rules_pluralises() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"sanitize": {"rules": [{"name": "clear-log", "sql": "TRUNCATE TABLE sys_log"}]}},
    )
    plan = plan_for(["sanitize"], user_layers=[extra])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.SANITIZE)
    assert "2 rules: clear-sessions, clear-log" in step.detail


def test_a_sanitize_rule_without_a_name_is_labelled_unnamed() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"sanitize": {"rules": {"mode": "replace", "values": [{"sql": "SELECT 1"}]}}},
    )
    plan = plan_for(["sanitize"], user_layers=[extra])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.SANITIZE)
    assert "unnamed" in step.detail


def test_a_target_path_without_a_trailing_slash_still_joins_correctly() -> None:
    unslashed = ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app",
        stage=Stage.PRODUCTION,
    )
    profile = ResolvedProfile(
        name="typo3",
        label="typo3@12",
        data=PROFILE_DATA,
        layers=(LayerDoc("profile:typo3@12", PROFILE_DATA),),
    )
    plan = build_plan(
        target=unslashed,
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:typo3@12", PROFILE_DATA)]),
        steps=steps_for_selection(["db", "files"]),
    )
    details = " ".join(step.detail for step in plan.steps)
    assert "/srv/app/shared/public/fileadmin/" in details
    assert "/srv/appshared" not in details
