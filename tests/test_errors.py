import pytest

from ddev_sync.errors import (
    AmbiguousTargetError,
    ConfigError,
    DdevSyncError,
    DeployerError,
    DetectionError,
    FeatureError,
    ProfileError,
    TargetError,
)


@pytest.mark.parametrize(
    "subclass",
    [
        ConfigError,
        ProfileError,
        FeatureError,
        TargetError,
        AmbiguousTargetError,
        DetectionError,
        DeployerError,
    ],
)
def test_every_error_is_a_ddev_sync_error(subclass: type[DdevSyncError]) -> None:
    error = subclass("something went wrong")
    assert isinstance(error, DdevSyncError)
    assert str(error) == "something went wrong"


def test_an_ambiguous_target_is_still_a_target_error() -> None:
    # Load-bearing, not taxonomy: callers that handle any target failure -
    # `main`'s top-level handler among them - catch `TargetError`, and
    # `_build_run_context` catches only this subclass to retry the pick. If
    # the two ever stopped being related, the narrow catch would still work
    # and the broad one would let an unresolvable target escape as a
    # traceback.
    assert issubclass(AmbiguousTargetError, TargetError)
