from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.plan import Plan, available_selection_groups, build_plan
from ddev_sync.engine.steps import SELECTION_GROUPS, StepKind, steps_for_selection
from ddev_sync.errors import ConfigError
from ddev_sync.operations.base import PathEscapesProject
from ddev_sync.profiles.registry import ResolvedProfile


def target(layout: Layout = Layout.PLAIN) -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        layout=layout,
    )


def project() -> DdevProject:
    return DdevProject(name="acme", type="typo3", docroot="public", root=Path("/var/www/html"))


PROFILE_DATA: dict[str, Any] = {
    "database": {"env_map": {"name": "TYPO3_DB_NAME"}},
    "files": {"paths": [{"remote": "public/fileadmin/", "local": "public/fileadmin/"}]},
    "sanitize": {"rules": [{"name": "clear-sessions", "sql": "TRUNCATE TABLE be_sessions"}]},
    "setup": {"commands": [{"run": "composer install --no-interaction"}]},
}


def plan_for(
    selection: list[str],
    *,
    user_layers: list[LayerDoc] | None = None,
    notice: str | None = None,
    target_layout: Layout = Layout.PLAIN,
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
        target=target(target_layout),
        project=project(),
        profile=profile,
        merged=merge_layers(layers),
        steps=steps_for_selection(selection),
    )


def kinds(plan: Plan) -> list[str]:
    return [step.kind.value for step in plan.steps]


def test_a_database_only_plan_has_preflight_dump_download_import() -> None:
    plan = plan_for(["db"])
    assert kinds(plan) == [
        "preflight",
        "db.dump",
        "db.download",
        "db.import",
    ]
    assert all(not step.is_hook for step in plan.steps)


def test_db_download_does_not_render_an_scp_that_never_runs() -> None:
    """The dump is streamed straight into the local file by db.dump
    (DbDumpOperation); no dump.sql is ever written on the remote, so a
    rendered `scp ... dump.sql` line would describe a transfer and a file
    that do not exist. The step must stay visible - the plan promised it -
    but must not claim an scp happens.
    """
    plan = plan_for(["db"])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.DB_DOWNLOAD)
    assert "scp" not in step.detail
    assert step.where != "scp"


def test_steps_are_numbered_from_one_in_order() -> None:
    plan = plan_for(["db", "files"])
    assert [step.index for step in plan.steps] == list(range(1, len(plan.steps) + 1))


def test_one_files_step_is_emitted_per_configured_path() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"files": {"paths": [{"remote": "public/uploads/", "local": "public/uploads/"}]}},
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
    assert "/srv/app/public/fileadmin/" in step.detail
    assert "/var/www/html/public/fileadmin/" in step.detail


def test_a_files_steps_remote_path_is_derived_from_a_plain_targets_data_root() -> None:
    plan = plan_for(["files"], target_layout=Layout.PLAIN)
    step = next(entry for entry in plan.steps if entry.kind is StepKind.FILES_SYNC)
    assert "/srv/app/public/fileadmin/" in step.detail
    assert "/srv/app/shared/" not in step.detail


def test_a_files_steps_remote_path_is_derived_from_a_deployer_targets_data_root() -> None:
    plan = plan_for(["files"], target_layout=Layout.DEPLOYER)
    step = next(entry for entry in plan.steps if entry.kind is StepKind.FILES_SYNC)
    assert "/srv/app/shared/public/fileadmin/" in step.detail


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
    assert plan.steps[-1].is_hook is True
    assert plan.steps[-2].is_hook is False


def test_a_pre_hook_lands_immediately_before_its_step() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(pre_db_dump={"commands": [{"run": "echo starting"}]})],
    )
    assert plan.steps[1].detail == "echo starting"
    assert plan.steps[1].is_hook is True
    assert plan.steps[2].detail.startswith("mysqldump")
    assert plan.steps[2].is_hook is False


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


def test_mode_replace_on_a_hook_discards_a_weaker_layers_commands() -> None:
    weak = hooks_layer(post_db_import={"mode": "extend", "commands": [{"run": "weak"}]})
    strong = LayerDoc(
        ".ddev/ddev-sync.local.yaml",
        {"hooks": {"post_db_import": {"mode": "replace", "commands": [{"run": "strong"}]}}},
    )
    plan = plan_for(["db"], user_layers=[weak, strong])
    hook_steps = [step.detail for step in plan.steps if step.is_hook]
    assert hook_steps == ["strong"]


def test_mode_extend_on_a_hook_keeps_both_layers_commands() -> None:
    weak = hooks_layer(post_db_import={"mode": "extend", "commands": [{"run": "weak"}]})
    strong = LayerDoc(
        ".ddev/ddev-sync.local.yaml",
        {"hooks": {"post_db_import": {"mode": "extend", "commands": [{"run": "strong"}]}}},
    )
    plan = plan_for(["db"], user_layers=[weak, strong])
    hook_steps = [step.detail for step in plan.steps if step.is_hook]
    assert hook_steps == ["weak", "strong"]


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


def test_a_bare_string_entry_is_normalised_to_a_named_operation() -> None:
    plan = plan_for(
        ["setup"],
        user_layers=[LayerDoc("bad", {"setup": {"commands": ["ddev-sync:flush-cache"]}})],
    )
    setup_steps = [step for step in plan.steps if step.kind is StepKind.SETUP]
    assert len(setup_steps) == 2
    assert setup_steps[0].detail == "composer install --no-interaction"
    assert setup_steps[1].detail == "ddev-sync:flush-cache"
    assert setup_steps[1].source == "bad"


def test_genuinely_malformed_entries_are_ignored_rather_than_crashing() -> None:
    plan = plan_for(
        ["setup"],
        user_layers=[LayerDoc("bad", {"setup": {"commands": [42, ["nested-list"]]}})],
    )
    setup_steps = [step for step in plan.steps if step.kind is StepKind.SETUP]
    assert len(setup_steps) == 1
    assert setup_steps[0].detail == "composer install --no-interaction"


def test_a_database_override_uses_the_top_level_database_key_not_a_sync_wrapper() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"database": {"target_database": "acme"}})
    plan = plan_for(["db"], user_layers=[extra])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.DB_IMPORT)
    assert "mysql -h ddev-acme-db acme <" in step.detail
    assert step.source == ".ddev/ddev-sync.yaml"


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


def test_an_ssh_alias_target_omits_the_port_flag_from_the_preflight_preview() -> None:
    aliased = ResolvedTarget(
        name="production",
        host="app-prod",
        via_ssh_alias=True,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
    )
    profile = ResolvedProfile(
        name="generic", label="generic", data={}, layers=(LayerDoc("profile:generic", {}),)
    )
    plan = build_plan(
        target=aliased,
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:generic", {})]),
        steps=steps_for_selection([]),
    )
    assert plan.steps[0].detail == (
        "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 app-prod"
    )


def test_a_direct_target_includes_the_port_flag_in_the_preflight_preview() -> None:
    plan = plan_for([])
    assert plan.steps[0].detail == (
        "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 deploy@web01.example"
    )


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


def test_preflight_params_flag_sanitize_as_selected_when_it_is() -> None:
    # operations/preflight.py's table probe (Part 3) must only run the extra
    # database round trip when SANITIZE was actually selected for this run -
    # PlannedStep.params is how the operation learns that, since it never
    # sees the whole Plan.
    plan = plan_for(["sanitize"])
    preflight = next(entry for entry in plan.steps if entry.kind is StepKind.PREFLIGHT)
    assert preflight.params == {"sanitize_selected": True, "db_selected": False}


def test_preflight_params_flag_sanitize_as_not_selected_otherwise() -> None:
    plan = plan_for(["files"])
    preflight = next(entry for entry in plan.steps if entry.kind is StepKind.PREFLIGHT)
    assert preflight.params == {"sanitize_selected": False, "db_selected": False}


def test_the_preflight_step_knows_whether_the_database_is_selected() -> None:
    # operations/preflight.py's execute() must refuse a run that reads the
    # database but cannot resolve credentials - gated on this flag so a
    # files-only run, which reads no database, is never refused over it.
    plan = plan_for(["db", "files"])
    preflight = next(entry for entry in plan.steps if entry.kind is StepKind.PREFLIGHT)
    assert preflight.params["db_selected"] is True


def test_a_files_only_run_marks_the_database_unselected() -> None:
    plan = plan_for(["files"])
    preflight = next(entry for entry in plan.steps if entry.kind is StepKind.PREFLIGHT)
    assert preflight.params["db_selected"] is False


def test_a_files_entry_missing_remote_names_the_entry_instead_of_crashing() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"files": {"paths": [{"local": "public/uploads/"}]}})
    with pytest.raises(ConfigError) as caught:
        plan_for(["files"], user_layers=[extra])
    assert "remote" in str(caught.value)


def test_a_files_entry_missing_local_names_the_entry_instead_of_crashing() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"files": {"paths": [{"remote": "x/"}]}})
    with pytest.raises(ConfigError) as caught:
        plan_for(["files"], user_layers=[extra])
    assert "local" in str(caught.value)


def test_a_hook_with_a_typoed_mode_and_no_commands_is_a_config_error() -> None:
    # `mode: replce` is a typo for `mode: replace`; without a recognised
    # payload key, `config/merge.py` cannot tell this apart from an ordinary
    # nested map and merges it as plain data (spec §4.5 is schema-agnostic on
    # purpose). Left unchecked, the hook silently never runs.
    with pytest.raises(ConfigError) as caught:
        plan_for(["db"], user_layers=[hooks_layer(post_db_import={"mode": "replce"})])
    assert "hooks.post_db_import" in str(caught.value)
    assert "mode" in str(caught.value)


def test_the_mode_typo_error_names_the_valid_modes() -> None:
    with pytest.raises(ConfigError) as caught:
        plan_for(["db"], user_layers=[hooks_layer(post_db_import={"mode": "replce"})])
    assert "extend" in str(caught.value)
    assert "prepend" in str(caught.value)
    assert "replace" in str(caught.value)


def test_a_setup_block_with_a_typoed_mode_and_no_commands_is_a_config_error() -> None:
    profile = ResolvedProfile(
        name="generic", label="generic", data={}, layers=(LayerDoc("profile:generic", {}),)
    )
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"setup": {"mode": "replce"}})
    with pytest.raises(ConfigError) as caught:
        build_plan(
            target=target(),
            project=project(),
            profile=profile,
            merged=merge_layers([LayerDoc("profile:generic", {}), extra]),
            steps=steps_for_selection(["setup"]),
        )
    assert "setup" in str(caught.value)
    assert "mode" in str(caught.value)


def test_a_hook_with_mode_and_commands_together_is_not_a_config_error() -> None:
    # The happy path for the same shape: `mode` alongside `commands` is a
    # legitimate list directive, not the typo case above.
    plan = plan_for(
        ["db"],
        user_layers=[
            hooks_layer(post_db_import={"mode": "replace", "commands": [{"run": "flush"}]})
        ],
    )
    assert plan.steps[-1].detail == "flush"


def test_pre_sync_runs_before_anything_else() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml", {"hooks": {"pre_sync": {"commands": [{"run": "maintenance-on"}]}}}
    )
    plan = plan_for(["db"], user_layers=[extra])
    assert plan.steps[0].detail == "maintenance-on"
    assert plan.steps[0].is_hook is True
    assert plan.steps[0].kind is StepKind.PREFLIGHT
    assert plan.steps[1].kind is StepKind.PREFLIGHT
    assert plan.steps[1].is_hook is False


def test_no_pre_sync_hooks_means_no_extra_step() -> None:
    plan = plan_for(["db"])
    assert plan.steps[0].kind is StepKind.PREFLIGHT
    assert plan.steps[0].is_hook is False


def test_every_planned_step_carries_params() -> None:
    plan = plan_for(["db", "files"])
    assert all(isinstance(step.params, dict) for step in plan.steps)


def test_a_files_sync_step_carries_the_raw_mapping_as_params() -> None:
    plan = plan_for(["files"])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.FILES_SYNC)
    assert step.params == {"remote": "public/fileadmin/", "local": "public/fileadmin/"}


def test_a_hook_step_carries_its_command_entry_as_params() -> None:
    plan = plan_for(
        ["db"],
        user_layers=[hooks_layer(post_db_import={"commands": [{"run": "typo3 cache:flush"}]})],
    )
    assert plan.steps[-1].params == {"run": "typo3 cache:flush"}


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
    assert "/srv/app/public/fileadmin/" in details
    assert "/srv/apppublic" not in details


def test_an_env_step_lands_between_db_import_and_files_sync() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"env": {"strategy": "patch_keys", "file": [".env"], "set": {"DB_HOST": "x"}}},
    )
    plan = plan_for(["db", "files"], user_layers=[extra])
    assert kinds(plan) == [
        "preflight",
        "db.dump",
        "db.download",
        "db.import",
        "env",
        "files.sync",
    ]


def test_an_env_step_names_its_strategy_candidates_and_key_count() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {
            "env": {
                "strategy": "patch_keys",
                "file": [".env.local", ".env"],
                "set": {"DB_HOST": "x", "DB_USER": "y"},
            }
        },
    )
    plan = plan_for(["db"], user_layers=[extra])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.ENV)
    assert "patch_keys" in step.detail
    assert "2 key(s)" in step.detail
    assert ".env.local, .env" in step.detail
    assert "DB_HOST" in step.detail
    assert "DB_USER" in step.detail
    assert step.source == ".ddev/ddev-sync.yaml"
    assert step.where == "local"


def test_an_env_strategy_of_none_emits_no_step() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"env": {"strategy": "none"}})
    plan = plan_for(["db"], user_layers=[extra])
    assert all(step.kind is not StepKind.ENV for step in plan.steps)


def test_an_env_block_with_nothing_to_set_emits_no_step() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"env": {"strategy": "patch_keys"}})
    plan = plan_for(["db"], user_layers=[extra])
    assert all(step.kind is not StepKind.ENV for step in plan.steps)


def test_selecting_files_alone_does_not_pull_in_env() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"env": {"strategy": "patch_keys", "set": {"DB_HOST": "x"}}},
    )
    plan = plan_for(["files"], user_layers=[extra])
    assert all(step.kind is not StepKind.ENV for step in plan.steps)


def test_an_s3_sync_step_renders_when_both_sides_are_configured() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {
            "s3": {
                "remote": {"bucket": "prod-media"},
                "local": {"bucket": "local-media"},
            }
        },
    )
    plan = plan_for(["s3"], user_layers=[extra])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.S3_SYNC)
    assert step.where == "rclone"
    assert "rclone copy prod-media -> local-media" in step.detail
    assert step.source == ".ddev/ddev-sync.yaml"
    assert step.params == extra.data["s3"]


def test_an_s3_sync_step_shows_sync_when_delete_is_set() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {
            "s3": {
                "remote": {"bucket": "prod-media"},
                "local": {"bucket": "local-media"},
                "delete": True,
            }
        },
    )
    plan = plan_for(["s3"], user_layers=[extra])
    step = next(entry for entry in plan.steps if entry.kind is StepKind.S3_SYNC)
    assert "rclone sync" in step.detail


@pytest.mark.parametrize(
    "s3_value",
    [
        "not-a-mapping",
        {"remote": {"bucket": "x"}},
        {"remote": {"bucket": "x"}, "local": "not-a-mapping"},
    ],
)
def test_an_s3_block_of_the_wrong_shape_yields_no_step(s3_value: Any) -> None:
    plan = plan_for(["s3"], user_layers=[LayerDoc("bad", {"s3": s3_value})])
    assert all(step.kind is not StepKind.S3_SYNC for step in plan.steps)


def test_an_escaping_local_path_is_rejected_while_building_the_plan() -> None:
    """`--dry-run` must refuse a `files.paths` entry whose `local:` escapes
    the project root, the same way the real run does - rendering it as
    though it were fine would be a dry run that lied.
    """
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml", {"files": {"paths": [{"remote": "x/", "local": "../../etc/"}]}}
    )
    with pytest.raises(PathEscapesProject):
        plan_for(["files"], user_layers=[extra])


def test_post_sync_hooks_appear_in_the_plans_post_sync_field_not_as_a_step() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml", {"hooks": {"post_sync": {"commands": [{"run": "cleanup"}]}}}
    )
    plan = plan_for(["db"], user_layers=[extra])
    assert all(step.detail != "cleanup" for step in plan.steps)
    assert [entry.detail for entry in plan.post_sync] == ["cleanup"]
    assert plan.post_sync[0].source == ".ddev/ddev-sync.yaml"


def test_post_sync_as_a_bare_list_is_resolved_too() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"hooks": {"post_sync": [{"run": "cleanup"}]}})
    plan = plan_for(["db"], user_layers=[extra])
    assert [entry.detail for entry in plan.post_sync] == ["cleanup"]


def test_post_sync_as_a_bare_string_operation_reference_is_resolved_too() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"hooks": {"post_sync": ["ddev-sync:flush-cache"]}})
    plan = plan_for(["db"], user_layers=[extra])
    assert [entry.detail for entry in plan.post_sync] == ["ddev-sync:flush-cache"]


def test_no_post_sync_hooks_means_an_empty_post_sync_field() -> None:
    assert plan_for(["db"]).post_sync == ()


@pytest.mark.parametrize(
    "hooks",
    ["not-a-mapping", {"post_sync": "not-a-mapping"}, {"post_sync": {"commands": "no"}}],
)
def test_a_malformed_post_sync_block_yields_no_entries(hooks: Any) -> None:
    plan = plan_for(["db"], user_layers=[LayerDoc("bad", {"hooks": hooks})])
    assert plan.post_sync == ()


def test_a_typoed_post_sync_mode_is_a_config_error() -> None:
    extra = LayerDoc(".ddev/ddev-sync.yaml", {"hooks": {"post_sync": {"mode": "replce"}}})
    with pytest.raises(ConfigError) as caught:
        plan_for(["db"], user_layers=[extra])
    assert "hooks.post_sync" in str(caught.value)


def test_a_setup_step_is_labelled_web_not_local() -> None:
    # `composer install` runs in the project's web container (see
    # transport/container.py), and a dry run that says "local" would
    # describe a place the command never reaches.
    plan = plan_for(["setup"])
    setup = [step for step in plan.steps if step.kind is StepKind.SETUP]
    assert setup
    assert {step.where for step in setup} == {"web"}


def test_a_hook_step_is_labelled_web_too() -> None:
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"hooks": {"post_db_import": [{"run": "php artisan migrate --force"}]}},
    )
    plan = plan_for(["db"], user_layers=[extra])
    hook = next(step for step in plan.steps if step.is_hook)
    assert hook.where == "web"


def test_the_env_step_stays_local() -> None:
    # The counter-case: the env rewrite is pure Python in this process and
    # must not be relabelled along with the commands.
    extra = LayerDoc(
        ".ddev/ddev-sync.yaml",
        {"env": {"strategy": "patch_keys", "set": {"DB_HOST": "x"}}},
    )
    plan = plan_for(["db"], user_layers=[extra])
    env = next(step for step in plan.steps if step.kind is StepKind.ENV)
    assert env.where == "local"


def available_for(
    data: dict[str, Any] = PROFILE_DATA, *, only_feature_setup: bool = False
) -> frozenset[str]:
    profile = ResolvedProfile(
        name="typo3",
        label="typo3@12",
        data=data,
        layers=(LayerDoc("profile:typo3@12", data),),
        notice=None,
    )
    return available_selection_groups(
        target=target(),
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:typo3@12", data)]),
        only_feature_setup=only_feature_setup,
    )


def test_a_group_is_available_when_it_would_produce_at_least_one_step() -> None:
    available = available_for()
    assert {"db", "files", "sanitize", "setup"} <= available


def test_a_group_that_would_produce_nothing_is_not_offered() -> None:
    # No shipped profile configures S3, so a project that never mentions it
    # must not be asked about it - which is the report this task came from.
    assert "s3" not in available_for()


def test_a_group_the_project_configures_becomes_available() -> None:
    data = {
        **PROFILE_DATA,
        "s3": {"remote": {"bucket": "prod"}, "local": {"bucket": "dev"}},
    }
    assert "s3" in available_for(data)


@pytest.mark.parametrize(
    ("group", "section"),
    [("files", "files"), ("sanitize", "sanitize"), ("setup", "setup")],
)
def test_a_group_whose_configuration_is_absent_is_not_offered(group: str, section: str) -> None:
    data = {key: value for key, value in PROFILE_DATA.items() if key != section}
    assert group not in available_for(data)


def test_db_is_available_even_when_the_profile_writes_no_env_file() -> None:
    # `db` names four kinds and TYPO3 produces no ENV entry at all, so the
    # test is that availability asks "any of these", not "all of these".
    data = {**PROFILE_DATA, "env": {"strategy": "none"}}
    plan = build_plan(
        target=target(),
        project=project(),
        profile=ResolvedProfile(
            name="typo3",
            label="typo3@12",
            data=data,
            layers=(LayerDoc("profile:typo3@12", data),),
            notice=None,
        ),
        merged=merge_layers([LayerDoc("profile:typo3@12", data)]),
        steps=steps_for_selection(["db"]),
    )
    assert StepKind.ENV not in {step.kind for step in plan.steps}
    assert "db" in available_for(data)


def test_a_hook_alone_makes_its_group_available() -> None:
    # The probe asks what a run would do, and a run of `s3` with only a
    # `pre_s3_sync` hook configured would run that hook. Pinning this keeps
    # the rule honest: availability follows the plan, hooks included.
    data = {**PROFILE_DATA, "hooks": {"pre_s3_sync": [{"run": "echo hi"}]}}
    assert "s3" in available_for(data)


def test_only_feature_setup_hides_setup_that_no_feature_contributed() -> None:
    # `ddev sync build` passes this flag, so the probe has to see it too:
    # otherwise it would call `setup` available on the strength of commands
    # the run is about to filter out.
    assert "setup" in available_for()
    assert "setup" not in available_for(only_feature_setup=True)


def test_only_feature_setup_keeps_setup_that_a_feature_contributed() -> None:
    # The other half of the test above: `build` on a project whose frontend
    # feature supplies commands must still find `setup` available, or it
    # could never run at all.
    data = {k: v for k, v in PROFILE_DATA.items() if k != "setup"}
    feature_layer = LayerDoc(
        "feature:node-build", {"setup": {"commands": [{"run": "npm run build"}]}}
    )
    profile = ResolvedProfile(
        name="typo3",
        label="typo3@12",
        data=data,
        layers=(LayerDoc("profile:typo3@12", data),),
        notice=None,
    )
    available = available_selection_groups(
        target=target(),
        project=project(),
        profile=profile,
        merged=merge_layers([LayerDoc("profile:typo3@12", data), feature_layer]),
        only_feature_setup=True,
    )
    assert "setup" in available


def test_a_group_with_broken_configuration_is_available_and_does_not_poison_the_others() -> None:
    # A `files.paths` entry with no `remote` makes planning `files` raise.
    # That must not stop the probe from answering for `db`, or `--steps db`
    # would fail over a typo in a group it never selected - and the broken
    # group is configured, only wrongly, so it still belongs in the menu with
    # the error left for the place that really plans it.
    data = {**PROFILE_DATA, "files": {"paths": [{"local": "public/fileadmin/"}]}}
    available = available_for(data)
    assert {"db", "files"} <= available


def test_a_group_whose_path_escapes_the_project_is_available_too() -> None:
    data = {**PROFILE_DATA, "files": {"paths": [{"remote": "a/", "local": "../outside/"}]}}
    assert "files" in available_for(data)


@pytest.mark.parametrize("bad", [None, 5, {"a": "b"}])
def test_a_malformed_env_file_is_a_config_error_naming_the_key(bad: object) -> None:
    data = {**PROFILE_DATA, "env": {"strategy": "patch_keys", "set": {"A": "1"}, "file": bad}}
    with pytest.raises(ConfigError, match=r"env\.file"):
        build_plan(
            target=target(),
            project=project(),
            profile=ResolvedProfile(
                name="typo3",
                label="typo3@12",
                data=data,
                layers=(LayerDoc("profile:typo3@12", data),),
                notice=None,
            ),
            merged=merge_layers([LayerDoc("profile:typo3@12", data)]),
            steps=steps_for_selection(["db"]),
        )
    # The probe must treat it like any other broken group: available, not fatal.
    assert "db" in available_for(data)


def test_every_name_it_returns_is_a_real_selection_group() -> None:
    assert available_for() <= set(SELECTION_GROUPS)
