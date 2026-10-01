import pytest
from pydantic import ValidationError

from ddev_sync.config.models import Layout, ResolvedTarget, Stage, TargetSpec


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
    assert spec.layout is None


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


@pytest.mark.parametrize(
    ("given", "expected"),
    [("/srv/app", "/srv/app/"), ("/srv/app/", "/srv/app/"), ("/", "/")],
)
def test_the_target_path_always_ends_with_exactly_one_separator(given: str, expected: str) -> None:
    target = ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path=given,
        stage=Stage.PRODUCTION,
    )
    assert target.path == expected


def _target(path: str, layout: Layout | None = None) -> ResolvedTarget:
    kwargs: dict[str, object] = {
        "name": "production",
        "host": "web01.example",
        "user": "deploy",
        "port": 22,
        "path": path,
        "stage": Stage.PRODUCTION,
    }
    if layout is not None:
        kwargs["layout"] = layout
    return ResolvedTarget(**kwargs)  # type: ignore[arg-type]


def test_layout_defaults_to_plain_when_not_set() -> None:
    assert _target("/srv/app/").layout is Layout.PLAIN


def test_layout_values_match_the_spec() -> None:
    assert [layout.value for layout in Layout] == ["plain", "deployer"]


def test_a_plain_target_derives_both_roots_from_path_unchanged() -> None:
    target = _target("/srv/app/", Layout.PLAIN)
    assert target.code_root == "/srv/app/"
    assert target.data_root == "/srv/app/"


def test_a_deployer_target_derives_code_root_under_current() -> None:
    target = _target("/srv/app/", Layout.DEPLOYER)
    assert target.code_root == "/srv/app/current/"


def test_a_deployer_target_derives_data_root_under_shared() -> None:
    target = _target("/srv/app/", Layout.DEPLOYER)
    assert target.data_root == "/srv/app/shared/"


@pytest.mark.parametrize("layout", [Layout.PLAIN, Layout.DEPLOYER])
@pytest.mark.parametrize("given", ["/srv/app", "/srv/app/"])
def test_code_root_and_data_root_end_with_exactly_one_separator_regardless_of_input(
    given: str, layout: Layout
) -> None:
    """The C14 invariant `path` already guarantees must hold for both derived
    roots too - that is the whole point of deriving rather than writing them.
    """
    target = _target(given, layout)
    assert target.code_root.endswith("/")
    assert not target.code_root.endswith("//")
    assert target.data_root.endswith("/")
    assert not target.data_root.endswith("//")
