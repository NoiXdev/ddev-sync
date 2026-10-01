from typing import Any

import pytest

from ddev_sync.errors import FeatureError
from ddev_sync.features.activation import Request, parse_feature_key, resolve_settings
from ddev_sync.features.registry import Feature, SettingSpec

SOURCE = ".ddev/ddev-sync.yaml"


def test_an_absent_key_is_none_not_an_empty_list() -> None:
    assert parse_feature_key(None, SOURCE) is None


def test_an_empty_list_means_explicitly_none() -> None:
    assert parse_feature_key([], SOURCE) == []


def test_a_list_becomes_requests_with_no_settings() -> None:
    assert parse_feature_key(["node-build", "filament"], SOURCE) == [
        Request("node-build", {}, SOURCE),
        Request("filament", {}, SOURCE),
    ]


def test_a_list_preserves_the_written_order() -> None:
    parsed = parse_feature_key(["filament", "node-build"], SOURCE)
    assert parsed is not None
    assert [r.name for r in parsed] == ["filament", "node-build"]


def test_a_mapping_carries_settings() -> None:
    parsed = parse_feature_key({"node-build": {"script": "build:prod"}}, SOURCE)
    assert parsed == [Request("node-build", {"script": "build:prod"}, SOURCE)]


def test_a_mapping_entry_with_no_settings_is_allowed() -> None:
    assert parse_feature_key({"filament": {}}, SOURCE) == [Request("filament", {}, SOURCE)]


def test_a_mapping_entry_with_a_null_value_is_allowed() -> None:
    # `filament:` with nothing under it is natural YAML for "just on".
    assert parse_feature_key({"filament": None}, SOURCE) == [Request("filament", {}, SOURCE)]


@pytest.mark.parametrize("bad", ["node-build", 5, [1, 2], {"a": "not-a-mapping"}])
def test_a_malformed_features_key_names_the_source(bad: Any) -> None:
    with pytest.raises(FeatureError) as caught:
        parse_feature_key(bad, SOURCE)
    assert SOURCE in str(caught.value)


def node_build() -> Feature:
    return Feature(
        name="node-build",
        settings={
            "install": SettingSpec(default="ci", description="install subcommand"),
            "script": SettingSpec(default="build", description="script to run"),
        },
    )


def test_defaults_apply_when_nothing_is_overridden() -> None:
    resolved = resolve_settings(node_build(), Request("node-build", {}, SOURCE))
    assert resolved == {"install": "ci", "script": "build"}


def test_an_override_replaces_one_default_and_leaves_the_rest() -> None:
    resolved = resolve_settings(
        node_build(), Request("node-build", {"script": "build:prod"}, SOURCE)
    )
    assert resolved == {"install": "ci", "script": "build:prod"}


def test_a_feature_with_no_settings_resolves_to_empty() -> None:
    assert resolve_settings(Feature(name="filament"), Request("filament", {}, SOURCE)) == {}


def test_absent_and_empty_are_distinguishable_by_the_caller() -> None:
    absent = parse_feature_key(None, SOURCE)
    empty = parse_feature_key([], SOURCE)
    assert absent is None
    assert empty == []
    # The distinction only helps if a caller can act on it, so it must survive
    # the obvious wrong test: `if not parsed` is true for both.
    assert (absent is None) is not (empty is None)


def test_a_typod_setting_key_is_rejected_and_lists_the_valid_ones() -> None:
    with pytest.raises(FeatureError) as caught:
        resolve_settings(node_build(), Request("node-build", {"scrip": "build"}, SOURCE))
    message = str(caught.value)
    assert "scrip" in message
    assert "install, script" in message
    assert SOURCE in message


def test_a_setting_on_a_feature_that_accepts_none_says_so() -> None:
    with pytest.raises(FeatureError) as caught:
        resolve_settings(Feature(name="filament"), Request("filament", {"x": "1"}, SOURCE))
    assert "accepts: none" in str(caught.value)


def test_a_list_as_settings_is_rejected_and_names_the_feature() -> None:
    with pytest.raises(FeatureError) as caught:
        parse_feature_key({"node-build": ["a", "b"]}, SOURCE)
    message = str(caught.value)
    assert "node-build" in message
    assert "mapping" in message
    assert SOURCE in message


def test_boolean_true_renders_as_lowercase_true() -> None:
    parsed = parse_feature_key({"node-build": {"enabled": True}}, SOURCE)
    assert parsed == [Request("node-build", {"enabled": "true"}, SOURCE)]


def test_boolean_false_renders_as_lowercase_false() -> None:
    parsed = parse_feature_key({"node-build": {"enabled": False}}, SOURCE)
    assert parsed == [Request("node-build", {"enabled": "false"}, SOURCE)]


def test_numeric_settings_render_as_digits() -> None:
    parsed = parse_feature_key({"node-build": {"timeout": 30, "retries": 3}}, SOURCE)
    assert parsed == [Request("node-build", {"timeout": "30", "retries": "3"}, SOURCE)]
