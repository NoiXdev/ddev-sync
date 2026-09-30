import pytest
from pydantic import ValidationError

from ddev_sync.config.models import ResolvedTarget, Stage, TargetSpec


def test_stage_values_match_the_spec() -> None:
    assert [stage.value for stage in Stage] == ["production", "staging", "testing", "dev"]


def test_target_spec_defaults_are_all_empty_so_layers_can_complete_each_other() -> None:
    spec = TargetSpec()
    assert spec.host is None
    assert spec.user is None
    assert spec.path is None
    assert spec.stage is None
    assert spec.extends is None
    assert spec.port == 22
    assert spec.jump_host_port == 22


def test_target_spec_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError) as caught:
        TargetSpec(hsot="typo.example")  # type: ignore[call-arg]
    assert "hsot" in str(caught.value)


def test_resolved_target_is_frozen() -> None:
    target = ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/var/www/app/",
        stage=Stage.PRODUCTION,
    )
    with pytest.raises(ValidationError):
        target.host = "other.example"


def test_resolved_target_requires_user_and_port_without_an_ssh_alias() -> None:
    with pytest.raises(ValidationError) as caught:
        ResolvedTarget(
            name="production",
            host="web01.example",
            path="/var/www/app/",
            stage=Stage.PRODUCTION,
        )
    assert "user" in str(caught.value)


def test_resolved_target_via_ssh_alias_needs_neither_user_nor_port() -> None:
    target = ResolvedTarget(
        name="production",
        host="app-prod",
        via_ssh_alias=True,
        path="/var/www/app/",
        stage=Stage.PRODUCTION,
    )
    assert target.user is None
    assert target.port is None
