import pytest

from ddev_sync.errors import (
    ConfigError,
    DdevSyncError,
    DetectionError,
    ProfileError,
    TargetError,
)


@pytest.mark.parametrize(
    "subclass",
    [ConfigError, ProfileError, TargetError, DetectionError],
)
def test_every_error_is_a_ddev_sync_error(subclass: type[DdevSyncError]) -> None:
    error = subclass("something went wrong")
    assert isinstance(error, DdevSyncError)
    assert str(error) == "something went wrong"
