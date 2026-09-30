import pytest

from ddev_sync.config.detect import (
    GENERIC_PROFILE,
    PROFILE_FOR_DDEV_TYPE,
    Resolution,
    resolve_profile_name,
)


def test_the_flag_beats_everything() -> None:
    assert resolve_profile_name("laravel", "typo3", "wordpress") == Resolution(
        value="laravel", source="--type flag"
    )


def test_configuration_beats_the_ddev_type() -> None:
    assert resolve_profile_name(None, "typo3", "wordpress") == Resolution(
        value="typo3", source=".ddev/ddev-sync.yaml"
    )


@pytest.mark.parametrize(
    ("ddev_type", "expected"),
    [
        ("typo3", "typo3"),
        ("laravel", "laravel"),
        ("wordpress", "wordpress"),
        ("symfony", "symfony"),
        ("shopware6", "shopware6"),
        ("drupal", "drupal"),
    ],
)
def test_every_supported_ddev_type_maps_to_its_profile(ddev_type: str, expected: str) -> None:
    assert resolve_profile_name(None, None, ddev_type) == Resolution(
        value=expected, source=".ddev/config.yaml"
    )


@pytest.mark.parametrize("ddev_type", ["php", "python", "magento2", "backdrop", ""])
def test_an_unmapped_ddev_type_falls_back_to_generic_and_says_so(ddev_type: str) -> None:
    resolution = resolve_profile_name(None, None, ddev_type)
    assert resolution is not None
    assert resolution.value == GENERIC_PROFILE
    assert "generic" in resolution.source


def test_nothing_at_all_returns_none_so_the_cli_can_ask() -> None:
    assert resolve_profile_name(None, None, None) is None


def test_the_mapping_covers_exactly_the_v1_profiles() -> None:
    assert sorted(PROFILE_FOR_DDEV_TYPE.values()) == [
        "drupal",
        "laravel",
        "shopware6",
        "symfony",
        "typo3",
        "wordpress",
    ]
