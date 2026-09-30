from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.errors import ConfigError


def layer(label: str, **data: Any) -> LayerDoc:
    return LayerDoc(label=label, data=data)


def test_a_single_layer_merges_to_itself() -> None:
    result = merge_layers([layer("profile", name="typo3", version=12)])
    assert result.data == {"name": "typo3", "version": 12}
    assert result.scalar_sources == {"name": "profile", "version": "profile"}


def test_a_stronger_layer_overwrites_a_scalar_and_claims_it() -> None:
    result = merge_layers(
        [
            layer("profile", target_database="db"),
            layer("project", target_database="acme"),
        ]
    )
    assert result.data == {"target_database": "acme"}
    assert result.scalar_sources == {"target_database": "project"}


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
        "sync.database.target_database": "project",
        "sync.files.delete": "profile",
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
    assert result.list_sources == {"rules": ("profile", "profile", "project")}


def test_replace_discards_everything_below_it() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a", "b"]),
            layer("project", rules={"mode": "replace", "values": ["c"]}),
        ]
    )
    assert result.data == {"rules": ["c"]}
    assert result.list_sources == {"rules": ("project",)}


def test_prepend_puts_the_stronger_layer_first_and_keeps_alignment() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a"]),
            layer("project", rules={"mode": "prepend", "values": ["x", "y"]}),
        ]
    )
    assert result.data == {"rules": ["x", "y", "a"]}
    assert result.list_sources == {"rules": ("project", "project", "profile")}


def test_a_list_reappearing_after_a_scalar_override_ignores_stale_labels() -> None:
    result = merge_layers(
        [
            layer("profile", rules=["a", "b"]),
            layer("project", rules="not-a-list-anymore"),
            layer("override", rules=["c"]),
        ]
    )
    assert result.data == {"rules": ["c"]}
    assert result.list_sources == {"rules": ("override",)}


def test_replace_on_the_weakest_layer_starts_from_empty() -> None:
    result = merge_layers([layer("profile", rules={"mode": "replace", "values": ["a"]})])
    assert result.data == {"rules": ["a"]}
    assert result.list_sources == {"rules": ("profile",)}


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
