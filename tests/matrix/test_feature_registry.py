"""What is worth knowing about the cell registry before any cell runs.

Unmarked on purpose, so these run in the ordinary unit suite: a mistyped
feature name would otherwise cost a three-minute cell run to discover, or
worse, silently assert about a feature that does not exist, and a name
breaking the rules stated above `Cell.name` would cost a CI run that
quietly covered two cells in one job. The `matrix` marker is applied by
`pytestmark` in `test_matrix.py` alone and `tests/matrix/conftest.py`
declares no autouse fixtures, so nothing here brings a stack up.
"""

import itertools
import re

from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT, FeatureRegistry
from tests.matrix.cells import CELLS
from tests.matrix.paths import HERE

# The charset stated above `Cell.name`: the `mx` prefix, then letters and
# digits, which is what excludes the hyphen `-k` cannot match and the
# underscore a network alias cannot carry.
CELL_NAME = re.compile(r"mx[A-Za-z0-9]+")


def test_every_feature_check_names_a_feature_that_exists() -> None:
    known = set(FeatureRegistry(DEFAULT_FEATURE_ROOT).available())
    # The registry read must not be the thing that is empty: an empty `known`
    # would make the loop below assert nothing at all.
    assert "node-build" in known, f"the feature directory was not read: {sorted(known)}"
    for cell in CELLS:
        for check in cell.features:
            assert check.feature in known, (
                f"{cell.name} declares a check for feature {check.feature!r}, "
                f"which is not one of {sorted(known)}"
            )


# Both loops below assert nothing against a registry too small to iterate, so
# each states the size it needs first - the same reason the feature check
# above refuses an empty `known`.
def test_every_cell_name_is_mx_followed_by_letters_and_digits_only() -> None:
    assert CELLS, "the cell registry is empty, so the loop below checks nothing"
    for cell in CELLS:
        assert CELL_NAME.fullmatch(cell.name), (
            f"cell name {cell.name!r} is not the `mx` prefix followed by "
            "letters and digits; see the charset rule above `Cell.name` for "
            "what a bare name, a hyphen and an underscore each break"
        )


def test_no_cell_name_contains_another_so_a_k_filter_selects_one_cell() -> None:
    assert len(CELLS) > 1, "fewer than two cells, so there is no pair to compare"
    for cell, other in itertools.permutations(CELLS, 2):
        assert other.name not in cell.name, (
            f"cell name {cell.name!r} contains cell name {other.name!r}, so the "
            f"`-k {other.name}` job in .github/workflows/matrix.yaml would "
            f"run {cell.name}'s tests too and still emit only {other.name}'s row"
        )


def test_every_declared_service_is_a_compose_profile() -> None:
    """A mistyped service name must fail this test, not a cell run.

    `compose --profile nosuch up` starts the four default services and says
    nothing about the name it did not recognise, so the cell would come up
    without the service it asked for and fail somewhere downstream.

    This checks that the name appears as a list item in the file, not that it
    sits under the service of that name.
    """
    compose = (HERE / "docker-compose.yaml").read_text(encoding="utf-8")
    declared = {name for cell in CELLS for name in cell.services}
    for name in sorted(declared):
        assert f"- {name}\n" in compose, (
            f"cell service {name!r} is not a compose profile in docker-compose.yaml; "
            "each service's `profiles:` entry carries the service's own name"
        )
