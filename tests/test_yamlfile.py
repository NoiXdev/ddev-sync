import pytest
import yaml

from ddev_sync.yamlfile import load_yaml


def test_an_ordinary_document_loads_as_safe_load_would() -> None:
    assert load_yaml("a: 1\nb: [2, 3]\n") == {"a": 1, "b": [2, 3]}


def test_an_empty_document_is_none_as_before() -> None:
    assert load_yaml("") is None


def test_a_key_named_twice_at_the_top_level_is_refused() -> None:
    # `safe_load` returns {"a": 2} here and says nothing, which is the whole
    # reason this module exists.
    with pytest.raises(yaml.YAMLError, match="'a' more than once"):
        load_yaml("a: 1\na: 2\n")


def test_the_refusal_names_the_line_the_key_recurs_on() -> None:
    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml("version: 1\ntargets: {x: 1}\ntargets:\n  y: 2\n")
    assert "line 3" in str(caught.value), "the second use is what the reader has to find"


def test_a_key_named_twice_inside_a_nested_mapping_is_refused() -> None:
    with pytest.raises(yaml.YAMLError, match="'k' more than once"):
        load_yaml("outer:\n  k: 1\n  k: 2\n")


def test_a_key_named_twice_inside_a_sequence_item_is_refused() -> None:
    with pytest.raises(yaml.YAMLError, match="'a' more than once"):
        load_yaml("list:\n  - a: 1\n    a: 2\n")


def test_keys_that_only_look_alike_are_both_kept() -> None:
    # `1` and `'1'` are different keys, and the tag is what tells them apart.
    assert load_yaml("1: a\n'1': b\n") == {1: "a", "1": "b"}


def test_an_unhashable_key_is_left_to_the_base_loader() -> None:
    # Comparing it here would raise `TypeError` from the set lookup instead of
    # PyYAML's own message, so the pass stops and lets the base refuse it.
    with pytest.raises(yaml.YAMLError, match="unhashable"):
        load_yaml("? [a, b]\n: 1\n")


def test_a_merge_key_is_not_treated_as_a_key() -> None:
    # `SafeLoader` registers no constructor for the merge tag - `flatten_mapping`
    # expands it afterwards - so constructing it would raise "could not
    # determine a constructor" on a file that is perfectly valid YAML. Sharing
    # fields through an anchor is an ordinary way to write a target.
    document = (
        "base: &b\n  user: deploy\n  port: 22\n"
        "targets:\n  production:\n    <<: *b\n    host: web01\n    stage: production\n"
    )
    assert load_yaml(document)["targets"]["production"] == {
        "user": "deploy",
        "port": 22,
        "host": "web01",
        "stage": "production",
    }


def test_a_key_after_a_merge_still_overrides_it() -> None:
    assert load_yaml("a: &a\n  x: 1\nb:\n  <<: *a\n  x: 2\n")["b"] == {"x": 2}


def test_several_merge_keys_in_one_mapping_are_allowed() -> None:
    # Two `<<` in one mapping is legal YAML, so counting them as a repeat
    # would refuse a valid document.
    document = "a: &a\n  x: 1\nb: &b\n  y: 2\nc:\n  <<: *a\n  <<: *b\n"
    assert load_yaml(document)["c"] == {"x": 1, "y": 2}


def test_a_merged_list_is_allowed() -> None:
    document = "a: &a\n  x: 1\nb: &b\n  y: 2\nc:\n  <<: [*a, *b]\n"
    assert load_yaml(document)["c"] == {"x": 1, "y": 2}


def test_a_duplicate_beside_a_merge_key_is_still_refused() -> None:
    # Skipping the merge key must not make the mapping unchecked.
    with pytest.raises(yaml.YAMLError, match="'x' more than once"):
        load_yaml("a: &a\n  y: 1\nb:\n  <<: *a\n  x: 1\n  x: 2\n")
