import json
from pathlib import Path

import pytest

from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import FeatureError
from ddev_sync.features.activation import Request
from ddev_sync.features.registry import FeatureRegistry
from ddev_sync.features.resolver import resolve_features

SOURCE = ".ddev/ddev-sync.yaml"


def write(root: Path, filename: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(body, encoding="utf-8")


@pytest.fixture
def registry(tmp_path: Path) -> FeatureRegistry:
    features = tmp_path / "features"
    write(
        features,
        "node-build.yaml",
        "name: node-build\n"
        "detect:\n  file_exists: package.json\n"
        "settings:\n"
        "  install:\n    default: ci\n"
        "  script:\n    default: build\n"
        "setup:\n  commands:\n"
        "    - run: ${PM} ${install}\n"
        "    - run: ${PM_RUN} ${script}\n",
    )
    write(
        features,
        "filament.yaml",
        "name: filament\napplies_to: [laravel]\n"
        "detect:\n  composer_package: filament/filament\n"
        "setup:\n  commands:\n    - run: php artisan filament:upgrade\n",
    )
    write(
        features,
        "manual-only.yaml",
        "name: manual-only\nsetup:\n  commands:\n    - run: echo manual\n",
    )
    return FeatureRegistry(features)


def project_at(root: Path, type_: str = "laravel") -> DdevProject:
    return DdevProject(name="acme", type=type_, docroot="public", root=root)


def with_laravel_project(root: Path, *, filament: bool = False, node: bool = False) -> Path:
    packages = [{"name": "laravel/framework", "version": "v11.30.0"}]
    if filament:
        packages.append({"name": "filament/filament", "version": "v3.2.0"})
    (root / "composer.lock").write_text(json.dumps({"packages": packages}), encoding="utf-8")
    if node:
        (root / "package.json").write_text("{}", encoding="utf-8")
        (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    return root


def test_detection_activates_what_the_project_actually_uses(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True, node=True)
    resolution = resolve_features(registry, project_at(root), "laravel", None, True)
    assert [a.feature.name for a in resolution.activated] == ["filament", "node-build"]


def test_detection_reports_why_each_feature_is_active(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True)
    resolution = resolve_features(registry, project_at(root), "laravel", None, True)
    assert resolution.activated[0].reason == "detected: filament/filament"


def test_a_feature_with_no_detect_block_is_never_auto_activated(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, node=True)
    resolution = resolve_features(registry, project_at(root), "laravel", None, True)
    assert "manual-only" not in [a.feature.name for a in resolution.activated]


def test_an_explicit_request_activates_a_feature_detection_would_not(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path)
    resolution = resolve_features(
        registry, project_at(root), "laravel", [Request("manual-only", {}, SOURCE)], True
    )
    assert [a.feature.name for a in resolution.activated] == ["manual-only"]
    assert resolution.activated[0].reason == f"from {SOURCE}"


def test_an_explicit_list_replaces_detection_entirely(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True, node=True)
    resolution = resolve_features(
        registry, project_at(root), "laravel", [Request("manual-only", {}, SOURCE)], True
    )
    assert [a.feature.name for a in resolution.activated] == ["manual-only"]


def test_an_explicitly_empty_list_activates_nothing(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True, node=True)
    resolution = resolve_features(registry, project_at(root), "laravel", [], True)
    assert resolution.activated == ()


def test_detection_disabled_activates_nothing_when_nothing_is_declared(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True, node=True)
    resolution = resolve_features(registry, project_at(root), "laravel", None, False)
    assert resolution.activated == ()
    assert resolution.detection_enabled is False


def test_detection_disabled_still_honours_an_explicit_request(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path)
    resolution = resolve_features(
        registry, project_at(root), "laravel", [Request("filament", {}, SOURCE)], False
    )
    assert [a.feature.name for a in resolution.activated] == ["filament"]


def test_a_layer_is_produced_per_feature_labelled_for_provenance(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True, node=True)
    resolution = resolve_features(registry, project_at(root), "laravel", None, True)
    assert [layer.label for layer in resolution.layers] == [
        "feature:filament",
        "feature:node-build",
    ]


def test_settings_and_the_package_manager_are_expanded_into_the_layer(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, node=True)  # pnpm-lock.yaml
    resolution = resolve_features(registry, project_at(root), "laravel", None, True)
    commands = resolution.layers[0].data["setup"]["commands"]
    assert commands == [{"run": "pnpm ci"}, {"run": "pnpm run build"}]


def test_an_overridden_setting_reaches_the_expanded_command(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, node=True)
    resolution = resolve_features(
        registry,
        project_at(root),
        "laravel",
        [Request("node-build", {"script": "build:prod", "install": "install"}, SOURCE)],
        True,
    )
    commands = resolution.layers[0].data["setup"]["commands"]
    assert commands == [{"run": "pnpm install"}, {"run": "pnpm run build:prod"}]


def test_an_unknown_placeholder_survives_for_the_later_expansion_pass(
    tmp_path: Path,
) -> None:
    features = tmp_path / "features"
    write(
        features,
        "sitename.yaml",
        "name: sitename\n"
        "sanitize:\n  rules:\n"
        "    - name: url\n      operation: search_replace\n"
        "      table: t\n      column: c\n"
        '      to: "https://${DDEV_SITENAME}.ddev.site"\n',
    )
    root = with_laravel_project(tmp_path)
    resolution = resolve_features(
        FeatureRegistry(features),
        project_at(root),
        "laravel",
        [Request("sitename", {}, SOURCE)],
        True,
    )
    rule = resolution.layers[0].data["sanitize"]["rules"][0]
    assert rule["to"] == "https://${DDEV_SITENAME}.ddev.site"


def test_a_feature_using_PM_without_a_lockfile_names_the_lockfiles(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path)  # no package.json, no lockfile
    with pytest.raises(FeatureError) as caught:
        resolve_features(
            registry,
            project_at(root),
            "laravel",
            [Request("node-build", {}, SOURCE)],
            True,
        )
    message = str(caught.value)
    assert "package-lock.json" in message
    assert "yarn.lock" in message
    assert "pnpm-lock.yaml" in message


def test_explicit_features_keep_their_written_order(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, node=True)
    requests = [Request("manual-only", {}, SOURCE), Request("node-build", {}, SOURCE)]
    resolution = resolve_features(registry, project_at(root), "laravel", requests, True)
    assert [a.feature.name for a in resolution.activated] == ["manual-only", "node-build"]


def test_detected_features_are_alphabetical_so_the_plan_is_stable(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True, node=True)
    first = resolve_features(registry, project_at(root), "laravel", None, True)
    second = resolve_features(registry, project_at(root), "laravel", None, True)
    names = [a.feature.name for a in first.activated]
    assert names == [a.feature.name for a in second.activated]
    assert names == sorted(names)


def test_an_out_of_scope_explicit_request_names_the_feature_and_the_profile(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path)
    with pytest.raises(FeatureError) as caught:
        resolve_features(
            registry,
            project_at(root, "typo3"),
            "typo3",
            [Request("filament", {}, SOURCE)],
            True,
        )
    message = str(caught.value)
    assert "filament" in message
    assert "typo3" in message
    assert "laravel" in message


def test_an_out_of_scope_feature_is_simply_not_detected(
    tmp_path: Path, registry: FeatureRegistry
) -> None:
    root = with_laravel_project(tmp_path, filament=True)
    resolution = resolve_features(registry, project_at(root, "typo3"), "typo3", None, True)
    assert resolution.activated == ()


def test_detection_describes_a_combined_predicate_by_its_combinator(
    tmp_path: Path,
) -> None:
    features = tmp_path / "features"
    write(
        features,
        "combined.yaml",
        "name: combined\n"
        "detect:\n  any_of:\n"
        "    - file_exists: package.json\n"
        "    - file_exists: composer.json\n"
        "setup:\n  commands:\n    - run: echo combined\n",
    )
    root = with_laravel_project(tmp_path, node=True)
    resolution = resolve_features(
        FeatureRegistry(features), project_at(root), "laravel", None, True
    )
    assert resolution.activated[0].reason == "detected: any_of"


def test_a_non_string_leaf_in_a_feature_contribution_passes_through_unexpanded(
    tmp_path: Path,
) -> None:
    features = tmp_path / "features"
    write(
        features,
        "numeric.yaml",
        "name: numeric\n"
        "sanitize:\n  rules:\n"
        "    - name: url\n      operation: search_replace\n"
        "      table: t\n      column: c\n"
        "      to: replaced\n"
        "      priority: 7\n",
    )
    root = with_laravel_project(tmp_path)
    resolution = resolve_features(
        FeatureRegistry(features),
        project_at(root),
        "laravel",
        [Request("numeric", {}, SOURCE)],
        True,
    )
    rule = resolution.layers[0].data["sanitize"]["rules"][0]
    assert rule["priority"] == 7
