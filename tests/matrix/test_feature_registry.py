"""The one thing about a feature cell that is worth knowing before it runs.

Unmarked on purpose, so it runs in the ordinary unit suite: a mistyped
feature name would otherwise cost a three-minute cell run to discover, or
worse, silently assert about a feature that does not exist. The `matrix`
marker is applied by `pytestmark` in `test_matrix.py` alone and
`tests/matrix/conftest.py` declares no autouse fixtures, so nothing here
brings a stack up.
"""

from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT, FeatureRegistry
from tests.matrix.cells import CELLS


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
