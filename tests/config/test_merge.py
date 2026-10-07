from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.errors import ConfigError


def layer(label: str, **data: Any) -> LayerDoc:
    return LayerDoc(label=label, data=data)


def test_a_single_layer_merges_to_itself() -> None:
    result = merge_layers([layer("profile", name="typo3", version=12)])
    assert result.data == {"name": "typo3", "version": 12}
    assert result.scalar_sources == {("name",): "profile", ("version",): "profile"}


def test_a_stronger_layer_overwrites_a_scalar_and_claims_it() -> None:
    result = merge_layers(
        [
            layer("profile", target_database="db"),
            layer("project", target_database="acme"),
        ]
    )
    assert result.data == {"target_database": "acme"}
    assert result.scalar_sources == {("target_database",): "project"}


def test_maps_merge_deeply_and_each_leaf_keeps_its_own_source() -> None:
    result = merge_layers(
        [
            layer(
                "profile", sync={"database": {"target_database": "db"}, "files": {"delete": False}}
            ),
            layer("project", sync={"database": {"target_database": "acme"}}),
        ]
    )
    assert result.data == {
        "sync": {"database": {"target_database": "acme"}, "files": {"delete": False}}
    }
    assert result.scalar_sources == {
        ("sync", "database", "target_database"): "project",
        ("sync", "files", "delete"): "profile",
    }


def test_an_empty_layer_changes_nothing() -> None:
    base = layer("profile", name="typo3")
    assert merge_layers([base, layer("project")]).data == merge_layers([base]).data


def test_a_bare_list_extends_by_default() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a", "b"]),
            layer("project", rules=["c"]),
        ]
    )
    assert result.data == {"rules": ["a", "b", "c"]}
    assert result.list_sources == {("rules",): ("profile", "profile", "project")}


def test_replace_discards_everything_below_it() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a", "b"]),
            layer("project", rules={"mode": "replace", "values": ["c"]}),
        ]
    )
    assert result.data == {"rules": ["c"]}
    assert result.list_sources == {("rules",): ("project",)}


def test_prepend_puts_the_stronger_layer_first_and_keeps_alignment() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a"]),
            layer("project", rules={"mode": "prepend", "values": ["x", "y"]}),
        ]
    )
    assert result.data == {"rules": ["x", "y", "a"]}
    assert result.list_sources == {("rules",): ("project", "project", "profile")}


def test_a_list_reappearing_after_a_scalar_override_ignores_stale_labels() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a", "b"]),
            layer("project", rules="not-a-list-anymore"),
            layer("override", rules=["c"]),
        ]
    )
    assert result.data == {"rules": ["c"]}
    assert result.list_sources == {("rules",): ("override",)}


def test_a_literal_dotted_key_does_not_collide_with_a_nested_path() -> None:
    result = merge_layers(
        [
            LayerDoc("L1", {"a.b": ["p", "q"]}),
            LayerDoc("L2", {"a": {"b": ["x", "y", "z"]}}),
        ]
    )
    assert result.data == {"a.b": ["p", "q"], "a": {"b": ["x", "y", "z"]}}
    assert result.list_sources[("a.b",)] == ("L1", "L1")
    assert result.list_sources[("a", "b")] == ("L2", "L2", "L2")


def test_replace_on_the_weakest_layer_starts_from_empty() -> None:
    result = merge_layers([layer("profile", rules={"mode": "replace", "values": ["a"]})])
    assert result.data == {"rules": ["a"]}
    assert result.list_sources == {("rules",): ("profile",)}


@pytest.mark.parametrize(
    ("directive", "expected"),
    [
        ({"mode": "smoosh", "values": []}, "unknown list mode 'smoosh'"),
        ({"values": "not-a-list"}, "'values' must be a list"),
        ({"values": [], "extra": 1}, "found extra"),
    ],
)
def test_malformed_list_directives_name_the_path_and_the_problem(
    directive: dict[str, Any], expected: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        merge_layers([layer("project", sync={"rules": directive})])
    assert "sync.rules" in str(caught.value)
    assert expected in str(caught.value)


def test_mode_and_commands_is_recognised_as_a_list_directive_that_replaces() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a", "b"]),
            layer("project", rules={"mode": "replace", "commands": ["c"]}),
        ]
    )
    assert result.data == {"rules": ["c"]}
    assert result.list_sources == {("rules",): ("project",)}


def test_mode_prepend_with_commands_keeps_alignment() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a"]),
            layer("project", rules={"mode": "prepend", "commands": ["x", "y"]}),
        ]
    )
    assert result.data == {"rules": ["x", "y", "a"]}
    assert result.list_sources == {("rules",): ("project", "project", "profile")}


def test_a_bare_mode_key_without_values_or_commands_is_not_a_directive() -> None:
    """Deviation from the letter of Ruling F3: F3 asked for this to raise
    ConfigError, but 'mode' is not a reserved word for a schema-agnostic
    merge function - a document may legitimately use it as an ordinary data
    key anywhere outside an actual list directive.
    `test_merge_properties.py`'s Hypothesis suite demonstrates the collision:
    it generates arbitrary documents and found one shaped exactly like
    `{"mode": 0}`, which a raise would incorrectly reject. So a lone 'mode'
    key falls through to the ordinary nested-map merge instead.
    """
    result = merge_layers([layer("project", sync={"rules": {"mode": "replace"}})])
    assert result.data == {"sync": {"rules": {"mode": "replace"}}}


def test_values_and_commands_together_is_rejected_as_an_unexpected_key() -> None:
    with pytest.raises(ConfigError) as caught:
        merge_layers([layer("project", sync={"rules": {"values": ["a"], "commands": ["b"]}})])
    assert "sync.rules" in str(caught.value)
    assert "found commands" in str(caught.value)


def test_a_bare_commands_key_without_mode_is_not_a_directive() -> None:
    """`setup: {commands: [...]}` must keep working: 'commands' alone, with no
    'mode' sibling, is the pre-existing nested-map shape, not a directive.
    """
    result = merge_layers([layer("project", setup={"commands": ["run-this"]})])
    assert result.data == {"setup": {"commands": ["run-this"]}}


# A key is whatever YAML read it as, and the path a message is built from
# carries it verbatim. `".".join` on a bool raised `TypeError: sequence item
# 0: expected str instance, bool found` - a traceback in place of the
# configuration error it was in the middle of writing. `config/targets.py`
# refuses a non-string *target* name before the merge; a key anywhere else
# reaches here, so the rendering has to hold on its own.
@pytest.mark.parametrize("key", [True, 2026, None])
def test_an_error_about_a_non_text_key_is_still_a_configuration_error(key: Any) -> None:
    layers = [LayerDoc(label="project", data={key: {"values": "notalist"}})]
    with pytest.raises(ConfigError) as caught:
        merge_layers(layers)
    assert str(key) in str(caught.value)


def test_a_non_text_key_deep_inside_a_target_is_named_in_the_error() -> None:
    """The case the target-name refusal does not cover, and never could."""
    layers = [
        LayerDoc(
            label="project",
            data={"targets": {"production": {"setup": {True: {"values": "notalist"}}}}},
        )
    ]
    with pytest.raises(ConfigError) as caught:
        merge_layers(layers)
    assert "targets.production.setup.True" in str(caught.value)
