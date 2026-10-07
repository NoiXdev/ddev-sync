from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

import pytest

from ddev_sync.config.deployer import DeployerConnection
from ddev_sync.config.merge import LayerDoc
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.config.targets import (
    _DEPLOYER_DETAIL_KEYS,
    CollectedTarget,
    collect_targets,
    list_target_names,
    resolve_target,
)
from ddev_sync.errors import ConfigError, TargetError


def layer(label: str, targets: dict[str, Any]) -> LayerDoc:
    return LayerDoc(label=label, data={"targets": targets})


def _targets(raw: dict[Any, Any]) -> dict[str, Any]:
    """Pass a `targets:` mapping whose keys are deliberately not strings.

    What YAML hands the loader for `yes:` or `2026:` is a bool or an int key,
    which is the whole point of the tests below - and not something
    `dict[str, Any]` can express. The cast is the test saying so out loud
    rather than `layer` loosening its type for every other caller.
    """
    return raw


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


def test_an_empty_targets_block_is_treated_as_no_targets_rather_than_crashing() -> None:
    assert collect_targets([LayerDoc("project", {"targets": None})]) == {}


def test_a_non_mapping_targets_block_is_rejected() -> None:
    with pytest.raises(ConfigError) as caught:
        collect_targets([LayerDoc("project", {"targets": ["oops"]})])
    assert "targets" in str(caught.value)


# YAML 1.1, which PyYAML implements, reads an unquoted `yes:` as the boolean
# true and `2026:` as a number. The key then is not the text the user wrote,
# so `--target yes` can never match it, `ddev sync targets` prints `True`,
# and - with one ordinary target beside it - `sorted()` raises a bare
# `TypeError` comparing a bool with a str, mid-command.
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (True, "boolean"),
        (2026, "number"),
        (1.5, "number"),
        (date(2026, 10, 7), "not text"),
    ],
)
def test_a_target_name_that_is_not_text_is_refused(name: Any, expected: str) -> None:
    with pytest.raises(ConfigError) as caught:
        collect_targets([layer("project", _targets({name: {"host": "web01.example"}}))])
    message = str(caught.value)
    assert expected in message
    assert "Quote it" in message


# A key with nothing before the colon has no spelling to quote, so telling
# the user to quote it would be advice they cannot follow.
def test_a_target_with_no_name_at_all_is_told_to_get_one() -> None:
    with pytest.raises(ConfigError) as caught:
        collect_targets([layer("project", _targets({None: {"host": "web01.example"}}))])
    message = str(caught.value)
    assert "Give the target a name" in message
    assert "Quote it" not in message


def test_the_refusal_shows_the_quoted_name_it_can_recover() -> None:
    with pytest.raises(ConfigError) as caught:
        collect_targets([layer("project", _targets({2026: {"host": "web01.example"}}))])
    assert "'2026'" in str(caught.value)


def test_a_boolean_name_beside_an_ordinary_one_no_longer_crashes() -> None:
    """The symptom that made this worth fixing: a `TypeError` from `sorted`."""
    with pytest.raises(ConfigError):
        collect_targets(
            [layer("project", _targets({"production": {"host": "a"}, True: {"host": "b"}}))]
        )


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
    # 'production' never mentions port at all, so with exclude_unset=True in
    # _flatten it contributes nothing for that field and the base's explicit
    # 2222 survives. Without exclude_unset=True (e.g. with exclude_defaults),
    # TargetSpec's default of 22 would be indistinguishable from an explicit
    # 22 and this assertion is what catches an implementation that conflates
    # the two.
    assert resolved.port == 2222


def test_an_explicitly_written_default_port_still_overrides_the_base() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {"host": "web01.example", "user": "deploy", "port": 2222},
                "production": {
                    "extends": "cluster",
                    "port": 22,
                    "path": "/srv/app/",
                    "stage": "production",
                },
            }
        ),
    )
    # 'production' explicitly writes port: 22, which happens to equal
    # TargetSpec's default. exclude_defaults cannot distinguish "wrote the
    # default" from "wrote nothing" and would leave the base's 2222 in place;
    # exclude_unset can, and spec §4.4 says inline keys win over inherited
    # ones regardless of what value they happen to carry.
    assert resolved.port == 22


def test_an_explicitly_written_default_jump_host_port_still_overrides_the_base() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {
                    "host": "web01.example",
                    "user": "deploy",
                    "jump_host": "bastion.example",
                    "jump_host_port": 2200,
                },
                "production": {
                    "extends": "cluster",
                    "jump_host_port": 22,
                    "path": "/srv/app/",
                    "stage": "production",
                },
            }
        ),
    )
    assert resolved.jump_host_port == 22


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


def test_a_stronger_explicit_host_overrides_a_weaker_ssh_alias() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {"ssh_alias": "myalias", "path": "/a/", "stage": "dev"},
                "production": {
                    "extends": "cluster",
                    "host": "real.example",
                    "user": "u",
                    "port": 2200,
                },
            }
        ),
    )
    assert resolved.host == "real.example"
    assert resolved.via_ssh_alias is False
    assert resolved.user == "u"
    assert resolved.port == 2200


def test_a_stronger_ssh_alias_overrides_a_weaker_explicit_host() -> None:
    resolved = resolve_target(
        "production",
        collected_from(
            {
                "cluster": {
                    "host": "old.example",
                    "user": "deploy",
                    "port": 22,
                    "path": "/a/",
                    "stage": "dev",
                },
                "production": {"extends": "cluster", "ssh_alias": "myalias"},
            }
        ),
    )
    assert resolved.host == "myalias"
    assert resolved.via_ssh_alias is True
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
    message = str(caught.value)
    assert missing_key in message
    # collected_from() wraps everything in a single layer labelled "project";
    # spec §4.4 requires the message to name the file the target came from.
    assert "project" in message


def test_an_incomplete_target_names_the_strongest_layer_it_came_from() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target(
            "production",
            collect_targets(
                [
                    layer("global", {"production": {"host": "web01.example", "user": "deploy"}}),
                    layer("project", {"production": {"port": 2222}}),
                ]
            ),
        )
    message = str(caught.value)
    assert "path" in message
    assert "stage" in message
    assert "project" in message


def test_a_target_with_neither_host_nor_ssh_alias_is_rejected() -> None:
    with pytest.raises(TargetError) as caught:
        resolve_target(
            "production",
            collected_from(
                {"production": {"user": "deploy", "path": "/srv/", "stage": "production"}}
            ),
        )
    message = str(caught.value)
    assert "host" in message
    assert "project" in message


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
    assert "project" in message


def test_an_extends_chain_deeper_than_the_backstop_is_rejected() -> None:
    targets = {f"t{index}": {"extends": f"t{index + 1}"} for index in range(19)}
    targets["t19"] = {}
    with pytest.raises(TargetError) as caught:
        resolve_target("t0", collected_from(targets))
    assert "deeper than 16 levels" in str(caught.value)


def test_a_deployer_block_parses_with_an_alias() -> None:
    collected = collected_from({"prod": {"deployer": {"host": "web-1"}, "stage": "production"}})
    assert collected["prod"].spec.deployer is not None
    assert collected["prod"].spec.deployer.host == "web-1"


@pytest.mark.parametrize("raw", [None, {}], ids=["null", "mapping"])
def test_a_deployer_block_may_be_empty_as_null_or_as_a_mapping(raw: dict[str, str] | None) -> None:
    # `deployer:` with nothing after it parses as None, `deployer: {}` as a
    # mapping. Both mean "the single host, whatever it is called", so both
    # have to survive into a DeployerRef with no alias.
    collected = collected_from({"prod": {"deployer": raw, "stage": "production"}})
    spec = collected["prod"].spec
    assert spec.deployer is not None
    assert spec.deployer.host is None


def test_a_target_without_a_deployer_block_has_none() -> None:
    collected = collected_from({"prod": {"host": "web-1", "stage": "production"}})
    assert collected["prod"].spec.deployer is None


# These assert on the validator's own sentence, never on a bare key name:
# `_parse_spec` wraps pydantic's error, whose text echoes the whole input dict
# (`input_value={...}`), so every key the test wrote is in the message whatever
# the validator says. Only "so <keys> must not be set beside it" can come from
# the rejection itself.
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("host", "web-1"),
        ("ssh_alias", "prod"),
        ("user", "deploy"),
        ("port", 2222),
        ("path", "/srv/"),
        ("layout", "plain"),
    ],
)
def test_deployer_beside_a_connection_key_is_rejected_by_name(key: str, value: object) -> None:
    with pytest.raises(TargetError, match=rf"so {key} must not be set beside it"):
        collected_from({"prod": {"deployer": {}, key: value, "stage": "production"}})


def test_deployer_beside_several_connection_keys_names_all_of_them() -> None:
    with pytest.raises(TargetError, match=r"so host, user, path must not be set beside it"):
        collected_from(
            {
                "prod": {
                    "deployer": {},
                    "path": "/srv/",
                    "user": "deploy",
                    "host": "web-1",
                    "stage": "production",
                }
            }
        )


def test_deployer_beside_an_explicit_default_port_is_still_rejected() -> None:
    # `port` defaults to 22, so "is it set" cannot be answered by looking at
    # the value: a user who writes `port: 22` beside a block has contradicted
    # it just as much as one who writes 2222.
    with pytest.raises(TargetError, match=r"so port must not be set beside it"):
        collected_from({"prod": {"deployer": {}, "port": 22, "stage": "production"}})


def test_a_deployer_block_in_one_layer_and_literals_in_another_is_rejected() -> None:
    # Layers are merged key by key *before* a target is validated, so the same
    # target name defined with literals in one layer and a `deployer:` block in
    # another ends up with both on one spec. That is rejected on purpose: two
    # mechanisms for one target must not be silently blended. To override a
    # target's connection, define a new target that `extends` it - there the
    # stronger link wins (see the chain tests below).
    with pytest.raises(TargetError, match=r"so host, user, path must not be set beside it"):
        collect_targets(
            [
                layer(
                    "global",
                    {"prod": {"host": "old.example", "user": "deploy", "path": "/srv/"}},
                ),
                layer("project", {"prod": {"deployer": {}, "stage": "production"}}),
            ]
        )


@pytest.mark.parametrize("alias", ["", "   ", "\t"])
def test_a_blank_deployer_alias_is_rejected_where_it_was_written(alias: str) -> None:
    # Accepting it costs twice: the listing renders `from deployer: ` with a
    # dangling separator, and resolution then reports `Deployer has no host
    # ''`, which names neither the file nor the mistake. The assertion is on
    # the validator's own sentence, since pydantic echoes the input dict.
    with pytest.raises(TargetError, match=r"must name a host in the project's Deployer"):
        collected_from({"prod": {"deployer": {"host": alias}, "stage": "production"}})


def test_a_deployer_block_with_no_alias_at_all_is_still_fine() -> None:
    # The counterpart to the test above: rejecting a blank alias must not
    # reject an omitted one, which is the documented way to say "there is
    # only one host".
    spec = collected_from({"prod": {"deployer": {}, "stage": "production"}})["prod"].spec
    assert spec.deployer is not None
    assert spec.deployer.host is None


def test_deployer_beside_a_jump_host_is_allowed() -> None:
    # Deployer carries no canonical field for a bastion, so there is nothing
    # for the block to supply and no conflict to have. Rejecting it would put
    # this feature out of reach for every jump-host target.
    collected = collected_from(
        {
            "prod": {
                "deployer": {},
                "jump_host": "bastion.example",
                "jump_host_user": "jump",
                "stage": "production",
            }
        }
    )
    assert collected["prod"].spec.jump_host == "bastion.example"


def fake_resolver(**overrides: object) -> Callable[[str | None], DeployerConnection]:
    """A resolver that answers without running anything.

    The real one shells into the web container; every test about *targets* is
    about what happens to the answer, not about getting it.
    """

    def resolve(alias: str | None) -> DeployerConnection:
        values: dict[str, object] = {
            "host": "resolved.example",
            "user": "deploy",
            "port": None,
            "path": "/srv/app/",
            "layout": "deployer",
        }
        values.update(overrides)
        return DeployerConnection(**values)  # type: ignore[arg-type]

    return resolve


def test_a_deployer_target_takes_its_connection_from_the_resolver() -> None:
    resolved = resolve_target(
        "prod",
        collected_from({"prod": {"deployer": {}, "stage": "production"}}),
        resolve_deployer=fake_resolver(),
    )
    assert resolved == ResolvedTarget(
        name="prod",
        host="resolved.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        layout=Layout.DEPLOYER,
    )


def test_the_alias_from_the_block_is_what_the_resolver_is_asked_for() -> None:
    asked: list[str | None] = []

    def resolve(alias: str | None) -> DeployerConnection:
        asked.append(alias)
        return fake_resolver()(alias)

    resolve_target(
        "prod",
        collected_from({"prod": {"deployer": {"host": "web-1"}, "stage": "production"}}),
        resolve_deployer=resolve,
    )
    assert asked == ["web-1"]


def test_a_resolved_port_is_used_and_an_absent_one_leaves_the_default() -> None:
    collected = collected_from({"prod": {"deployer": {}, "stage": "production"}})
    assert resolve_target("prod", collected, resolve_deployer=fake_resolver(port=2222)).port == 2222
    assert resolve_target("prod", collected, resolve_deployer=fake_resolver()).port == 22


def test_a_stronger_deployer_block_beats_an_inherited_literal_host() -> None:
    # `host`, `ssh_alias` and `deployer` are three ways to say the same thing.
    # targets.py already settles host-versus-alias by letting the stronger
    # link win, and this follows that rule rather than inventing a second one.
    resolved = resolve_target(
        "prod",
        collected_from(
            {
                "base": {
                    "host": "old.example",
                    "user": "old-user",
                    "path": "/old/",
                    "stage": "production",
                },
                "prod": {"extends": "base", "deployer": {"host": "web-1"}},
            }
        ),
        resolve_deployer=fake_resolver(),
    )
    assert resolved.host == "resolved.example"
    assert resolved.user == "deploy"
    assert resolved.path == "/srv/app/"


def test_a_stronger_deployer_block_discards_an_inherited_port() -> None:
    # Strict means no fallback to a literal. If the base's `port: 2222` were
    # kept, a Deployer host that states no port would silently connect on a
    # port nobody asked for in this target.
    resolved = resolve_target(
        "prod",
        collected_from(
            {
                "base": {
                    "host": "old.example",
                    "user": "u",
                    "port": 2222,
                    "path": "/old/",
                    "stage": "production",
                },
                "prod": {"extends": "base", "deployer": {}},
            }
        ),
        resolve_deployer=fake_resolver(),
    )
    assert resolved.port == 22


def test_a_stronger_deployer_block_beats_an_inherited_ssh_alias() -> None:
    resolved = resolve_target(
        "prod",
        collected_from(
            {
                "base": {"ssh_alias": "myalias", "path": "/old/", "stage": "production"},
                "prod": {"extends": "base", "deployer": {}},
            }
        ),
        resolve_deployer=fake_resolver(),
    )
    assert resolved.host == "resolved.example"
    assert resolved.via_ssh_alias is False


def test_an_inherited_deployer_block_loses_to_a_stronger_literal_host() -> None:
    resolved = resolve_target(
        "prod",
        collected_from(
            {
                "base": {"deployer": {"host": "web-1"}, "stage": "production"},
                "prod": {"extends": "base", "host": "new.example", "user": "u", "path": "/srv/"},
            }
        ),
    )
    assert resolved.host == "new.example"


def test_an_inherited_deployer_block_loses_to_a_stronger_ssh_alias() -> None:
    resolved = resolve_target(
        "prod",
        collected_from(
            {
                "base": {"deployer": {}, "stage": "production"},
                "prod": {"extends": "base", "ssh_alias": "myalias", "path": "/srv/"},
            }
        ),
    )
    assert resolved.host == "myalias"
    assert resolved.via_ssh_alias is True


def test_a_literal_beside_an_inherited_deployer_block_is_rejected_by_name() -> None:
    # The block is the stronger-or-equal source for the connection, so a
    # `path` written further down the chain contradicts it just as a `path`
    # beside it would. Overwriting it silently would shadow an explicit
    # choice; so would ignoring the block.
    collected = collected_from(
        {
            "base": {"deployer": {}, "stage": "production"},
            "prod": {"extends": "base", "path": "/srv/", "user": "u"},
        }
    )
    with pytest.raises(TargetError) as error:
        resolve_target("prod", collected, resolve_deployer=fake_resolver())
    message = str(error.value)
    assert "target 'prod'" in message
    assert "user, path cannot be set" in message
    assert "'deployer:' block" in message


# One literal per key a block owns besides the host. Written as a mapping, not
# a parametrize list, so that a key added to `DEPLOYER_OWNED_KEYS` - from which
# `_DEPLOYER_DETAIL_KEYS` is derived - fails this test with a `KeyError` naming
# it, rather than quietly going unchecked. The silent-discard bug this guards
# against is exactly what an unchecked key produces.
_A_LITERAL_FOR = {"user": "u", "port": 2222, "path": "/srv/", "layout": "plain"}


@pytest.mark.parametrize("key", _DEPLOYER_DETAIL_KEYS)
def test_every_detail_a_block_owns_is_rejected_when_a_stronger_link_sets_it(key: str) -> None:
    collected = collected_from(
        {
            "base": {"deployer": {}, "stage": "production"},
            "prod": {"extends": "base", key: _A_LITERAL_FOR[key]},
        }
    )
    with pytest.raises(TargetError, match=rf"{key} cannot be set on a target that extends"):
        resolve_target("prod", collected, resolve_deployer=fake_resolver())


def test_a_deployer_target_without_a_resolver_says_so() -> None:
    collected = collected_from({"prod": {"deployer": {}, "stage": "production"}})
    with pytest.raises(TargetError) as error:
        resolve_target("prod", collected)
    assert "deployer" in str(error.value).lower()
