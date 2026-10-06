import pytest

# The variables `ddev_sync.cli.colour` consults. Without this a developer who
# has `NO_COLOR` set in their shell gets different test results than CI does
# (and a CI runner that sets `FORCE_COLOR` for its log viewer gets different
# results than a laptop), and tests whose outcome depends on whose machine
# they run on are the most unpleasant kind to debug: nothing in the diff
# explains the failure. Clearing the variables for every test makes the
# result depend on the repository alone. A test that is about one of them
# sets it itself.
#
# The fixture is autouse and therefore global, on purpose: the variables are
# read on the way from `main` down to the renderers, so any test that runs the
# CLI could be affected, not only the colour tests. That reach is the price of
# the guarantee, and a deliberate one.
_COLOUR_VARIABLES = ("NO_COLOR", "FORCE_COLOR", "TERM")


@pytest.fixture(autouse=True)
def _clean_colour_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _COLOUR_VARIABLES:
        monkeypatch.delenv(name, raising=False)
