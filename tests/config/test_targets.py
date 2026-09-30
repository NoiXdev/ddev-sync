from collections.abc import Mapping
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.config.targets import (
    CollectedTarget,
    collect_targets,
    list_target_names,
    resolve_target,
)
from ddev_sync.errors import TargetError


def layer(label: str, targets: dict[str, Any]) -> LayerDoc:
    return LayerDoc(label=label, data={"targets": targets})


def test_targets_from_several_layers_are_collected_together() -> None:
    collected = collect_targets(
        [
            layer("global", {"cluster": {"host": "web01.example", "user": "deploy"}}),
            layer(
                "project",
                {"production": {"extends": "cluster", "path": "/srv/app/", "stage": "production"}},
            ),
        ]
    )
    assert sorted(collected) == ["cluster", "production"]
    assert collected["cluster"].source == "global"
    assert collected["production"].spec.extends == "cluster"


def test_a_stronger_layer_overrides_only_the_keys_it_sets() -> None:
    collected = collect_targets(
        [
            layer("global", {"production": {"host": "old.example", "user": "deploy", "port": 22}}),
            layer(
                "project",
                {"production": {"host": "new.example", "path": "/srv/app/", "stage": "staging"}},
            ),
        ]
    )
    spec = collected["production"].spec
    assert spec.host == "new.example"
    assert spec.user == "deploy"
    assert collected["production"].source == "project"


def test_no_targets_anywhere_is_an_empty_mapping() -> None:
    assert collect_targets([LayerDoc("project", {})]) == {}


def test_target_names_are_listed_sorted() -> None:
    collected = collect_targets([layer("project", {"staging": {}, "production": {}, "dev": {}})])
    assert list_target_names(collected) == ["dev", "production", "staging"]


def collected_from(targets: dict[str, Any]) -> Mapping[str, CollectedTarget]:
    return collect_targets([layer("project", targets)])


def test_a_complete_inline_target_resolves() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "production": {
                    "host": "web01.example",
                    "user": "deploy",
                    "port": 2222,
                    "path": "/srv/app/",
                    "stage": "production",
                }
            }
        ),
    )
    assert resolved == ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=2222,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
    )


def test_extends_pulls_connection_details_from_the_base() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {
                    "host": "web01.example",
                    "user": "deploy",
                    "jump_host": "bastion.example",
                    "jump_host_user": "tim",
                },
                "production": {"extends": "cluster", "path": "/srv/app/", "stage": "production"},
            }
        ),
    )
    assert resolved.host == "web01.example"
    assert resolved.user == "deploy"
    assert resolved.jump_host == "bastion.example"
    assert resolved.jump_host_user == "tim"
    assert resolved.path == "/srv/app/"


def test_the_child_wins_over_the_base_it_extends() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {"host": "old.example", "user": "deploy"},
                "production": {
                    "extends": "cluster",
                    "host": "new.example",
                    "path": "/srv/app/",
                    "stage": "production",
                },
            }
        ),
    )
    assert resolved.host == "new.example"


def test_a_child_default_does_not_overwrite_an_explicit_value_from_its_base() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {"host": "web01.example", "user": "deploy", "port": 2222},
                "production": {"extends": "cluster", "path": "/srv/app/", "stage": "production"},
            }
        ),
    )
    # 'production' never sets a port, so TargetSpec gives it the default 22.
    # Without exclude_defaults=True in _flatten, that 22 would overwrite the
    # base's explicit 2222 and this assertion is what catches it.
    assert resolved.port == 2222


def test_an_ssh_alias_target_needs_no_user_or_port() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {"production": {"ssh_alias": "app-prod", "path": "/srv/app/", "stage": "production"}}
        ),
    )
    assert resolved.via_ssh_alias is True
    assert resolved.host == "app-prod"
    assert resolved.user is None
    assert resolved.port is None


def test_an_unknown_target_lists_the_ones_that_exist() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target("prod", collected_from({"production": {}, "staging": {}}))
    message = str(caught.value)
    assert "prod" in message
    assert "production, staging" in message


def test_extending_a_target_that_does_not_exist_names_both() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target("production", collected_from({"production": {"extends": "cluster"}}))
    message = str(caught.value)
    assert "production" in message
    assert "cluster" in message


@pytest.mark.parametrize(
    "missing_key",
    ["path", "stage"],
)
def test_an_incomplete_target_names_the_missing_key(missing_key: str) -> None:
    definition = {"host": "web01.example", "user": "deploy", "path": "/srv/", "stage": "production"}
    del definition[missing_key]
    with pytest.raises(TargetError) as caught:
        resolve_target("production", collected_from({"production": definition}))
    assert missing_key in str(caught.value)


def test_a_target_with_neither_host_nor_ssh_alias_is_rejected() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target(
            "production",
            collected_from(
                {"production": {"user": "deploy", "path": "/srv/", "stage": "production"}}
            ),
        )
    assert "host" in str(caught.value)


def test_a_two_target_extends_cycle_is_reported_not_recursed() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target(
            "alpha",
            collected_from({"alpha": {"extends": "beta"}, "beta": {"extends": "alpha"}}),
        )
    message = str(caught.value)
    assert "circular extends chain" in message
    assert "alpha -> beta -> alpha" in message


def test_a_target_that_extends_itself_is_reported() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target("alpha", collected_from({"alpha": {"extends": "alpha"}}))
    assert "alpha -> alpha" in str(caught.value)


def test_a_non_mapping_target_definition_is_rejected() -> None:
    with pytest.raises(TargetError) as caught:
        collect_targets([layer("project", {"cluster": "not-a-mapping"})])
    assert "cluster" in str(caught.value)


def test_an_unknown_key_in_a_target_definition_is_rejected() -> None:
    with pytest.raises(TargetError) as caught:
        collect_targets([layer("project", {"cluster": {"host": "web01.example", "bogus": "x"}})])
    assert "cluster" in str(caught.value)


def test_a_target_missing_user_fails_resolved_target_validation() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target(
            "production",
            collected_from(
                {"production": {"host": "web01.example", "path": "/srv/", "stage": "production"}}
            ),
        )
    message = str(caught.value)
    assert "production" in message
    assert "user" in message


def test_an_extends_chain_deeper_than_the_backstop_is_rejected() -> None:
    targets = {f"t{index}": {"extends": f"t{index + 1}"} for index in range(19)}
    targets["t19"] = {}
    with pytest.raises(TargetError) as caught:
        resolve_target("t0", collected_from(targets))
    assert "deeper than 16 levels" in str(caught.value)
