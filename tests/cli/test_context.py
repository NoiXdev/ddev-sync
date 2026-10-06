import json
from pathlib import Path

import pytest

from ddev_sync.cli.context import build_context
from ddev_sync.errors import ConfigError, TargetError
from ddev_sync.transport.fake import FakeCommandRunner

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


def test_a_complete_project_assembles_a_context(project: Path, tmp_path: Path) -> None:
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.project.name == "acme"
    assert context.profile.name == "generic"
    assert context.target.name == "production"


def test_a_single_target_is_chosen_without_being_named(project: Path, tmp_path: Path) -> None:
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name=None,
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.target.name == "production"


def test_several_targets_with_none_named_is_an_error_listing_them(
    project: Path, tmp_path: Path
) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production: {host: a, user: u, path: /p/, stage: production}\n"
        "  staging: {host: b, user: u, path: /p/, stage: staging}\n",
        encoding="utf-8",
    )
    with pytest.raises(TargetError) as caught:
        build_context(
            project_root=project,
            home=tmp_path / "home",
            target_name=None,
            type_flag=None,
            framework_version_flag=None,
            profile_root=PROFILE_ROOT,
        )
    message = str(caught.value)
    assert "--target" in message
    assert "production, staging" in message


def test_no_targets_at_all_points_at_the_wizard(project: Path, tmp_path: Path) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(TargetError) as caught:
        build_context(
            project_root=project,
            home=tmp_path / "home",
            target_name=None,
            type_flag=None,
            framework_version_flag=None,
            profile_root=PROFILE_ROOT,
        )
    assert "ddev sync init" in str(caught.value)


def test_the_type_flag_overrides_the_ddev_type(project: Path, tmp_path: Path) -> None:
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="generic",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.profile.name == "generic"
    assert context.notices == ()


def test_the_global_layer_is_merged_in(project: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    global_config = home / ".ddev" / "ddev-sync" / "config.yaml"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        "version: 1\ntargets:\n  cluster:\n    host: shared.example\n    user: deploy\n",
        encoding="utf-8",
    )
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    extends: cluster\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
        encoding="utf-8",
    )
    context = build_context(
        project_root=project,
        home=home,
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.target.host == "shared.example"
    assert context.target.user == "deploy"


def test_a_real_project_block_configures_type_and_version(project: Path, tmp_path: Path) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "project:\n"
        "  type: generic\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
        encoding="utf-8",
    )
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.profile.name == "generic"


def test_an_empty_project_block_is_treated_as_no_overrides(project: Path, tmp_path: Path) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "project:\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
        encoding="utf-8",
    )
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.target.name == "production"


def test_a_non_mapping_project_block_is_rejected(project: Path, tmp_path: Path) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\nproject:\n  - oops\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        build_context(
            project_root=project,
            home=tmp_path / "home",
            target_name=None,
            type_flag=None,
            framework_version_flag=None,
            profile_root=PROFILE_ROOT,
        )
    assert "project" in str(caught.value)


def test_an_undetectable_type_is_reported_with_guidance(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text("name: acme\ntype: php\n", encoding="utf-8")
    (root / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\ntargets:\n  p: {host: a, user: u, path: /p/, stage: dev}\n",
        encoding="utf-8",
    )
    context = build_context(
        project_root=root,
        home=tmp_path / "home",
        target_name="p",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    # php maps to generic rather than failing; DetectionError is reserved for
    # the case where .ddev/config.yaml itself cannot supply a type.
    assert context.profile.name == "generic"


def test_a_detected_feature_contributes_a_layer(project: Path, tmp_path: Path) -> None:
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
    assert [a.feature.name for a in context.features.activated] == ["filament"]
    # The brief's own sketch of this assertion fell back on
    # `context.features.layers` because `filament`'s only contribution is a
    # `setup.commands` entry - a list, not a scalar - so `scalar_sources`
    # alone can never see it. Checking `list_sources` directly is the real
    # verification: it confirms the merge machinery actually carried
    # `feature:filament` through as a step's provenance label, not merely
    # that *some* feature activated.
    assert any("feature:filament" in labels for labels in context.merged.list_sources.values())


def test_no_features_disables_detection_for_the_run(project: Path, tmp_path: Path) -> None:
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
        no_features=True,
    )
    assert context.features.activated == ()
    # Must name the flag this call actually passed, not the config file - the
    # two kill switches disable detection the same way but are not the same
    # source, and the dry run's notice (cli/render.py) must say which one.
    assert context.features.disabled_reason == "--no-features"


def test_the_global_kill_switch_disables_detection(project: Path, tmp_path: Path) -> None:
    (project / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "filament/filament", "version": "v3.2.0"}]}),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    config = home / ".ddev" / "ddev-sync" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\ndefaults:\n  detect_features: false\n", encoding="utf-8")
    context = build_context(
        project_root=project,
        home=home,
        target_name="production",
        type_flag="laravel",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag=None,
        no_features=False,
    )
    assert context.features.activated == ()
    assert context.features.disabled_reason == "~/.ddev/ddev-sync/config.yaml"


def test_a_project_features_key_overrides_detection(project: Path, tmp_path: Path) -> None:
    (project / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "filament/filament", "version": "v3.2.0"}]}),
        encoding="utf-8",
    )
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "features:\n"
        "  - manual-only\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
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
    # An explicit `features:` key replaces detection entirely (spec §2): only
    # the named feature is active, even though filament would otherwise have
    # been detected from the composer.lock written above.
    assert [a.feature.name for a in context.features.activated] == ["manual-only"]


def test_the_features_flag_overrides_the_project_features_key(
    project: Path, tmp_path: Path
) -> None:
    (project / "package.json").write_text("{}", encoding="utf-8")
    (project / "package-lock.json").write_text("{}", encoding="utf-8")
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "features:\n"
        "  - manual-only\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
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
        # A trailing empty entry (from a trailing comma) must be dropped
        # rather than looked up as a feature named "".
        features_flag="node-build,",
        no_features=False,
    )
    assert [a.feature.name for a in context.features.activated] == ["node-build"]
    assert context.features.activated[0].reason == "from --features"


def test_the_features_flag_strips_whitespace_around_an_interior_name(
    project: Path, tmp_path: Path
) -> None:
    """`--features="node-build, filament"` must look up `filament`, not `" filament"`.

    `_feature_requests` strips each entry twice: once in the filter
    (`if name.strip()`, which only decides whether to keep an entry) and
    once in the value actually used to build the `Request`
    (`Request(name.strip(), ...)`). Only the second strip is exercised here:
    without it, a name with leading whitespace from `", "`-separated input
    would be looked up verbatim and fail with "unknown feature ' filament'"
    even though the entry itself is not empty and so survives the filter.
    """
    (project / "package.json").write_text("{}", encoding="utf-8")
    (project / "package-lock.json").write_text("{}", encoding="utf-8")
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="laravel",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag="node-build, filament",
        no_features=False,
    )
    assert [a.feature.name for a in context.features.activated] == ["node-build", "filament"]


def test_the_features_flag_drops_an_interior_empty_element(project: Path, tmp_path: Path) -> None:
    """`--features=node-build,,filament` drops the middle empty entry.

    Distinct from the already-covered trailing-comma case
    (`test_the_features_flag_overrides_the_project_features_key`): a
    *middle* empty element must not be looked up as a feature named `""`
    either.
    """
    (project / "package.json").write_text("{}", encoding="utf-8")
    (project / "package-lock.json").write_text("{}", encoding="utf-8")
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="laravel",
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        feature_root=FEATURE_ROOT,
        features_flag="node-build,,filament",
        no_features=False,
    )
    assert [a.feature.name for a in context.features.activated] == ["node-build", "filament"]


def test_a_features_mapping_with_settings_is_parsed(project: Path, tmp_path: Path) -> None:
    (project / "package.json").write_text("{}", encoding="utf-8")
    (project / "package-lock.json").write_text("{}", encoding="utf-8")
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "features:\n"
        "  node-build:\n"
        "    install: npm\n"
        "targets:\n"
        "  production:\n"
        "    host: web01.example\n"
        "    user: deploy\n"
        "    path: /srv/app/\n"
        "    stage: production\n",
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
    assert [a.feature.name for a in context.features.activated] == ["node-build"]
    assert context.features.activated[0].settings["install"] == "npm"
    assert context.features.activated[0].reason == "from .ddev/ddev-sync.yaml"


DEP_CONFIG = json.dumps(
    {
        "web-1": {
            "hostname": "web-1.example",
            "remote_user": "deploy",
            "port": 2222,
            "deploy_path": "/srv/app",
            "current_path": "/srv/app/current",
        }
    }
)


def _write_deployer_target(project: Path) -> None:
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\ntargets:\n  production:\n    deployer: {host: web-1}\n    stage: production\n",
        encoding="utf-8",
    )


def test_build_context_resolves_a_deployer_target_through_the_runner(
    project: Path, tmp_path: Path
) -> None:
    _write_deployer_target(project)
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=DEP_CONFIG)

    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        runner=runner,
    )

    assert context.target.host == "web-1.example"
    assert context.target.user == "deploy"
    assert context.target.port == 2222
    assert context.target.path == "/srv/app/"
    assert len(runner.calls) == 1
    # It ran in this project's own web container.
    assert "ddev-acme-web" in runner.calls[0]


def test_a_target_without_a_deployer_block_never_runs_dep(project: Path, tmp_path: Path) -> None:
    # An unscripted command raises, so reaching the runner at all would fail
    # this - which is the point: a project that does not use Deployer must not
    # pay for it, or break because of it.
    runner = FakeCommandRunner()

    build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
        runner=runner,
    )

    assert runner.calls == []


def test_a_deployer_target_without_a_runner_says_so(project: Path, tmp_path: Path) -> None:
    _write_deployer_target(project)

    with pytest.raises(TargetError) as caught:
        build_context(
            project_root=project,
            home=tmp_path / "home",
            target_name="production",
            type_flag=None,
            framework_version_flag=None,
            profile_root=PROFILE_ROOT,
        )

    assert "'deployer:' block" in str(caught.value)
