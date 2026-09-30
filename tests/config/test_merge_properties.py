from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from ddev_sync.config.merge import LayerDoc, merge_layers

# Scalars and nested maps only: list directives deliberately break
# associativity (replace is not associative), so they are excluded here
# and covered by the explicit table tests instead.
scalars = st.one_of(st.integers(), st.booleans(), st.text(max_size=8))
documents = st.recursive(
    st.dictionaries(st.text(min_size=1, max_size=4), scalars, max_size=4),
    lambda children: st.dictionaries(st.text(min_size=1, max_size=4), children, max_size=3),
    max_leaves=6,
)


@given(documents)
def test_merging_one_layer_returns_that_layer(data: dict[str, Any]) -> None:
    assert merge_layers([LayerDoc("only", data)]).data == data


@given(documents)
def test_an_empty_layer_on_top_is_the_identity(data: dict[str, Any]) -> None:
    base = [LayerDoc("base", data)]
    assert merge_layers([*base, LayerDoc("empty", {})]).data == merge_layers(base).data


@given(documents, documents, documents)
def test_merging_is_associative(a: dict[str, Any], b: dict[str, Any], c: dict[str, Any]) -> None:
    flat = merge_layers([LayerDoc("a", a), LayerDoc("b", b), LayerDoc("c", c)])
    staged = merge_layers(
        [LayerDoc("ab", merge_layers([LayerDoc("a", a), LayerDoc("b", b)]).data), LayerDoc("c", c)]
    )
    assert flat.data == staged.data


@given(documents, documents)
def test_the_strongest_layer_always_wins_for_keys_it_sets(
    weak: dict[str, Any], strong: dict[str, Any]
) -> None:
    merged = merge_layers([LayerDoc("weak", weak), LayerDoc("strong", strong)]).data
    for key, value in strong.items():
        if not isinstance(value, dict):
            assert merged[key] == value
