from pathlib import Path

import pytest

from ddev_sync.errors import FeatureError
from ddev_sync.features.registry import (
    DEFAULT_FEATURE_ROOT,
    DEV_CHECKOUT_FEATURE_ROOT,
    INSTALLED_FEATURE_ROOT,
    FeatureRegistry,
)


def write(root: Path, filename: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(body, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    features = tmp_path / "features"
    write(
        features,
        "node-build.yaml",
        "name: node-build\n"
        "description: Build the frontend.\n"
        "detect:\n  file_exists: package.json\n"
        "settings:\n  script:\n    default: build\n    description: the script\n"
        "setup:\n  commands:\n    - run: npm run build\n",
    )
    write(
        features,
        "filament.yaml",
        "name: filament\napplies_to: [laravel]\n"
        "detect:\n  composer_package: filament/filament\n"
        "setup:\n  commands:\n    - run: php artisan filament:upgrade\n",
    )
    return features


def test_available_lists_every_feature_sorted(root: Path) -> None:
    assert FeatureRegistry(root).available() == ["filament", "node-build"]


def test_a_feature_loads_with_its_metadata_split_from_its_contribution(
    root: Path,
) -> None:
    feature = FeatureRegistry(root).load("node-build")
    assert feature.name == "node-build"
    assert feature.description == "Build the frontend."
    assert feature.applies_to == ()
    assert feature.settings["script"].default == "build"
    assert feature.data == {"setup": {"commands": [{"run": "npm run build"}]}}
    assert "detect" not in feature.data
    assert "settings" not in feature.data


def test_applies_to_is_carried_as_a_tuple(root: Path) -> None:
    assert FeatureRegistry(root).load("filament").applies_to == ("laravel",)


def test_an_unknown_feature_lists_the_ones_that_exist(root: Path) -> None:
    with pytest.raises(FeatureError) as caught:
        FeatureRegistry(root).load("horizon")
    message = str(caught.value)
    assert "horizon" in message
    assert "filament, node-build" in message


def test_an_empty_feature_directory_reports_none_available(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(FeatureError) as caught:
        FeatureRegistry(empty).load("node-build")
    assert "available features: none" in str(caught.value)


def test_a_name_not_matching_the_filename_is_rejected(tmp_path: Path) -> None:
    features = tmp_path / "features"
    write(features, "node-build.yaml", "name: nodebuild\n")
    with pytest.raises(FeatureError) as caught:
        FeatureRegistry(features).load("node-build")
    message = str(caught.value)
    assert "node-build" in message
    assert "nodebuild" in message


def test_unparseable_yaml_names_the_file(tmp_path: Path) -> None:
    features = tmp_path / "features"
    write(features, "broken.yaml", "name: [unclosed\n")
    with pytest.raises(FeatureError) as caught:
        FeatureRegistry(features).load("broken")
    assert "broken.yaml" in str(caught.value)


def test_a_non_mapping_feature_is_rejected(tmp_path: Path) -> None:
    features = tmp_path / "features"
    write(features, "listy.yaml", "- one\n")
    with pytest.raises(FeatureError) as caught:
        FeatureRegistry(features).load("listy")
    assert "mapping" in str(caught.value)


def test_an_empty_feature_file_is_rejected_because_it_has_no_name(
    tmp_path: Path,
) -> None:
    features = tmp_path / "features"
    write(features, "empty.yaml", "")
    with pytest.raises(FeatureError) as caught:
        FeatureRegistry(features).load("empty")
    assert "name" in str(caught.value)


# The enforcement that a feature only activates within its applies_to profiles
# lands in Task 5 with the resolver, so the registry only carries the data
# needed for that check without enforcing it.
def test_a_scoped_feature_carries_the_profiles_it_applies_to(root: Path) -> None:
    assert FeatureRegistry(root).load("filament").applies_to == ("laravel",)


def test_an_unscoped_feature_applies_anywhere(root: Path) -> None:
    assert FeatureRegistry(root).load("node-build").applies_to == ()


# --- DEFAULT_FEATURE_ROOT's dev-checkout fallback ---------------------------


def test_the_dev_checkout_root_resolves_to_this_repositorys_own_features_directory() -> None:
    assert DEV_CHECKOUT_FEATURE_ROOT.is_dir()
    assert (DEV_CHECKOUT_FEATURE_ROOT / "horizon.yaml").is_file()


def test_the_installed_root_is_the_opt_path() -> None:
    assert Path("/opt/ddev-sync/features") == INSTALLED_FEATURE_ROOT


def test_the_default_root_is_one_of_the_two_known_candidates() -> None:
    assert DEFAULT_FEATURE_ROOT in {INSTALLED_FEATURE_ROOT, DEV_CHECKOUT_FEATURE_ROOT}
