"""The parser the feature assertions stand on, checked without a stack.

`_active_features` reads the Features block of a printed plan. The matrix
bodies that use it are deselected by the `matrix` marker in an ordinary run,
and when that marker is selected they skip until a cell declares a feature -
so on their own they would never exercise it in an ordinary run. Unmarked on
purpose, like `test_feature_registry.py`: the `matrix` marker is applied by
`pytestmark` in `test_matrix.py` alone, and importing that module only defines
functions, so nothing here brings a stack up.

The first seven tests take their input from `render_features`/`render_plan`,
so the block's own shapes come from the renderer rather than from an idea of
the format. The last four feed hand-built strings, pinning edges of the parser
instead: where the block ends, and how a name is read out of a line. Which
shapes the renderer can or cannot print is deliberately not claimed here -
that is a statement about `cli/render.py`, and it does not survive being
maintained in prose over here.
"""

from ddev_sync.cli.render import render_features, render_plan
from ddev_sync.engine.plan import Plan
from tests.cli.test_render import activated, small_plan
from tests.matrix.test_matrix import FEATURES_LABEL, _active_features


def plan_with(*features: tuple[str, str], notices: tuple[str, ...] = ()) -> Plan:
    base = small_plan(notices)
    return Plan(
        target=base.target,
        profile_label="laravel",
        project_name="acme",
        steps=base.steps,
        notices=notices,
        features=tuple(activated(name, reason) for name, reason in features),
    )


def test_a_single_feature_with_no_continuation_line_is_read() -> None:
    block = render_features(plan_with(("node-build", "detected: package.json")))
    assert block == "  Features  node-build  detected: package.json"
    assert _active_features(block) == {"node-build"}


def test_the_label_constant_is_the_one_the_renderer_prints() -> None:
    # `FEATURES_LABEL` is copied from `cli/render.py::_FEATURES_LABEL` (a private
    # name, so not imported); this is what notices the copy drifting.
    block = render_features(plan_with(("node-build", "detected: package.json")))
    assert block.startswith(FEATURES_LABEL)
    assert block[len(FEATURES_LABEL) :].startswith("node-build")


def test_two_features_whose_names_are_padded_to_one_width_are_both_read() -> None:
    # `filament` is padded to `node-build`'s width, so a name followed by
    # spaces and then the reason has to come back without the padding.
    block = render_features(
        plan_with(
            ("node-build", "detected: package.json"), ("filament", "detected: filament/filament")
        )
    )
    assert block.splitlines()[1].startswith(" " * 12 + "filament    detected")
    assert _active_features(block) == {"node-build", "filament"}


def test_a_reason_containing_a_double_space_does_not_leak_into_the_name() -> None:
    block = render_features(plan_with(("node-build", "named by  features: in config")))
    assert _active_features(block) == {"node-build"}


def test_the_disabled_notice_comes_back_as_one_unmatchable_name_carrying_the_reason() -> None:
    # The assertion in the matrix body compares against feature names, so this
    # string matches none of them and the failure message shows why.
    plan = small_plan()
    plan = Plan(
        target=plan.target,
        profile_label=plan.profile_label,
        project_name=plan.project_name,
        steps=plan.steps,
        features_detection_enabled=False,
        features_disabled_reason="--no-features",
    )
    assert _active_features(render_plan(plan)) == {"(detection disabled by --no-features)"}


def test_a_plan_with_no_features_block_yields_no_names() -> None:
    assert _active_features(render_plan(small_plan())) == set()
    assert _active_features("") == set()


def test_a_whole_plan_yields_only_the_features_not_the_header_notices_or_steps() -> None:
    plan = plan_with(
        ("node-build", "detected: package.json"),
        ("filament", "detected: filament/filament"),
        notices=("a notice that mentions Features",),
    )
    assert _active_features(render_plan(plan)) == {"node-build", "filament"}


def test_the_block_ends_at_the_blank_line_so_later_deep_indentation_is_not_a_name() -> None:
    """Deeply indented text below the block is not hypothetical. A hook's
    `run:` string reaches the rendered plan unescaped
    (`engine/plan.py::_hook_detail`), so a multi-line `run:` in a project's
    config carries its own later lines down there; rendering a plan from one
    such hook put a 14-space line below the block. The fixture is trimmed
    rather than copied from that plan, because what the parser has to get
    right is just this: the blank line ended the block, so a deep indentation
    after it is not a name.
    """
    plan = "\n".join(
        [
            "  Features  node-build  detected: package.json",
            "",
            " " * 12 + "a wrapped line further down that is not a feature",
        ]
    )
    assert _active_features(plan) == {"node-build"}


def test_a_line_indented_less_than_the_continuation_column_ends_the_block() -> None:
    plan = "\n".join(
        [
            "  Features  node-build  detected: package.json",
            "   1  preflight",
            " " * 12 + "not a continuation of anything",
        ]
    )
    assert _active_features(plan) == {"node-build"}


def test_a_whitespace_only_continuation_line_adds_no_empty_name() -> None:
    plan = "\n".join(["  Features  node-build  detected: package.json", " " * 12, ""])
    assert _active_features(plan) == {"node-build"}


def test_a_name_indented_a_column_past_the_label_is_still_read_without_the_space() -> None:
    plan = "\n".join(
        ["  Features  node-build  detected: package.json", " " * 13 + "filament  detected: x"]
    )
    assert _active_features(plan) == {"node-build", "filament"}
