from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.plan import Plan, build_plan
from ddev_sync.engine.steps import StepKind, steps_for_selection
from ddev_sync.errors import ConfigError
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
    assert plan.steps[0].detail == "ssh app-prod"


def test_a_direct_target_includes_the_port_flag_in_the_preflight_preview() -> None:
    plan = plan_for([])
    assert plan.steps[0].detail == "ssh -p 22 deploy@web01.example"


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
