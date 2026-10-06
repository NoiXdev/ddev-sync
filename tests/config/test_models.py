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
    message = str(caught.value)
    assert "user" in message
    # The advice to use `ssh_alias` has to say where it works: the sidecar is
    # given no ~/.ssh/config, so inside the add-on the suggestion cannot be
    # followed at all.
    assert "outside the add-on" in message


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


@pytest.mark.parametrize("given", ["/srv/app", "/srv/app/"])
@pytest.mark.parametrize(
    ("layout", "expected_code", "expected_data"),
    [
        (Layout.PLAIN, "/srv/app/", "/srv/app/"),
        (Layout.DEPLOYER, "/srv/app/current/", "/srv/app/shared/"),
    ],
)
def test_the_derived_roots_are_exact_for_both_path_forms(
    given: str, layout: Layout, expected_code: str, expected_data: str
) -> None:
    """Pinned by value, not just by `endswith("/")` - an unslashed `path` plus
    `deployer` is the one combination where two joins happen (normalise, then
    append the subdirectory), so it is the combination most likely to produce
    `/srv/appcurrent/` or `/srv/app/current` while still satisfying a looser,
    suffix-only assertion. This invariant has broken twice in this project,
    both times past an approximate check.
    """
    target = _target(given, layout)
    assert target.code_root == expected_code
    assert target.data_root == expected_data
