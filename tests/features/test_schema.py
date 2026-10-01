from typing import Any

import pytest

from ddev_sync.errors import FeatureError
from ddev_sync.features.schema import validate_feature

MINIMAL: dict[str, Any] = {"name": "node-build"}


def test_a_minimal_feature_is_valid() -> None:
    validate_feature(MINIMAL)


def test_an_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_feature({**MINIMAL, "aplies_to": ["laravel"]})
    message = str(caught.value)
    assert "aplies_to" in message
    assert "node-build" in message


def test_a_feature_without_a_name_is_rejected() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_feature({"description": "no name here"})
    assert "name" in str(caught.value)


@pytest.mark.parametrize("bad", ["laravel", [1, 2], {"laravel": True}])
def test_applies_to_must_be_a_list_of_strings(bad: Any) -> None:
    with pytest.raises(FeatureError) as caught:
        validate_feature({**MINIMAL, "applies_to": bad})
    assert "applies_to" in str(caught.value)


def test_an_empty_applies_to_is_valid_and_means_any_profile() -> None:
    validate_feature({**MINIMAL, "applies_to": []})


@pytest.mark.parametrize(
    "bad",
    ["not-a-mapping", {"script": "build"}, {"script": {"description": "no default"}}],
)
def test_a_settings_entry_must_be_a_mapping_carrying_a_default(bad: Any) -> None:
    with pytest.raises(FeatureError) as caught:
        validate_feature({**MINIMAL, "settings": bad})
    assert "setting" in str(caught.value)


def test_a_well_formed_settings_block_passes() -> None:
    validate_feature(
        {
            **MINIMAL,
            "settings": {"script": {"default": "build", "description": "the script to run"}},
        }
    )


def test_a_contribution_is_validated_by_the_profile_rules() -> None:
    with pytest.raises(FeatureError) as caught:
        validate_feature({**MINIMAL, "files": {"paths": [{"remote": "a/"}]}})
    assert "local" in str(caught.value)
