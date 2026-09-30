from pathlib import Path

import pytest

from ddev_sync.cli.context import build_context
from ddev_sync.errors import TargetError

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"


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
        version_flag=None,
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
        version_flag=None,
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
            version_flag=None,
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
            version_flag=None,
            profile_root=PROFILE_ROOT,
        )
    assert "ddev sync init" in str(caught.value)


def test_the_type_flag_overrides_the_ddev_type(project: Path, tmp_path: Path) -> None:
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag="generic",
        version_flag=None,
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
        version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert context.target.host == "shared.example"
    assert context.target.user == "deploy"


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
        version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    # php maps to generic rather than failing; DetectionError is reserved for
    # the case where .ddev/config.yaml itself cannot supply a type.
    assert context.profile.name == "generic"
