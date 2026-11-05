"""`steps:`: what the picker starts from, across the configuration layers."""

from typing import Any

import pytest

from ddev_sync.cli.step_defaults import StepDefaults, read_step_defaults
from ddev_sync.config.loader import GLOBAL_LABEL, LOCAL_LABEL, PROJECT_LABEL
from ddev_sync.config.merge import LayerDoc
from ddev_sync.errors import ConfigError


def _layer(steps: Any, label: str = PROJECT_LABEL) -> LayerDoc:
    return LayerDoc(label, {"version": 1, "steps": steps})


def test_no_layers_mean_the_built_in_default() -> None:
    assert read_step_defaults([]) == StepDefaults()
    assert read_step_defaults([LayerDoc(PROJECT_LABEL, {"version": 1})]).for_target("x") is None
    assert read_step_defaults([_layer(None)]).ask_to_save is True


def test_a_targets_own_list_wins_over_the_projects() -> None:
    stored = read_step_defaults(
        [_layer({"default": ["db", "setup"], "by_target": {"production": ["db"]}})]
    )
    assert stored.for_target("production") == ["db"]
    assert stored.for_target("staging") == ["db", "setup"]


def test_an_empty_list_is_a_list() -> None:
    assert read_step_defaults([_layer({"default": []})]).for_target("x") == []


def test_the_stronger_layer_replaces_and_does_not_extend() -> None:
    stored = read_step_defaults(
        [
            _layer({"default": ["db", "files"], "by_target": {"a": ["db"]}}, GLOBAL_LABEL),
            _layer({"default": ["setup"], "by_target": {"b": ["files"]}}, PROJECT_LABEL),
            _layer({"by_target": {"a": ["s3"]}, "ask_to_save": False}, LOCAL_LABEL),
        ]
    )
    assert stored.project == ["setup"]
    assert stored.by_target == {"a": ["s3"], "b": ["files"]}
    assert stored.ask_to_save is False
    assert stored.in_project_file == {"b": ["files"]}


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ("db", "steps in .* must be a mapping"),
        ({"default": "db"}, "steps.default in .* must be a list"),
        ({"default": ["nope"]}, "unknown steps: nope"),
        ({"by_target": ["db"]}, "steps.by_target in .* must map"),
        ({"by_target": {"a": "db"}}, "steps.by_target.a in .* must be a list"),
        ({"ask_to_save": "no"}, "steps.ask_to_save in .* must be true or false"),
    ],
)
def test_what_is_malformed_is_refused(steps: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        read_step_defaults([_layer(steps)])
