"""That `up`, `down` and `service_exec` really pass `--profile` for a cell's services.

Unmarked on purpose, like `test_feature_registry.py`: nothing here starts a
container, and the regression it guards against is invisible at run time.

Measured on this branch: drop the argument list from `down` and the failure is
silent. The profiled container keeps running, the cell's network therefore
cannot be removed, compose prints "Resource is still in use" - and `down`
still exits 0. No unit test and no cell run goes red; the only symptom is
leftover containers nobody asserts about, and the next `up` meets a stale one.
A check whose absence looks like success is the one thing this matrix exists to
remove, so the argv is asserted rather than described.

`Stack.compose` is the single place both methods reach docker through, which is
what makes an argv assertion possible without a stack - the same seam
`test_stack_readers.py` uses for the service readers.
"""

import subprocess

import pytest

from tests.matrix.cells import CELLS, Cell
from tests.matrix.stack import Stack, up

CELL_IDS = [cell.name for cell in CELLS]

# `Stack.compose`'s return type, aliased so the recorder's signature fits a line.
Result = subprocess.CompletedProcess[str]


def _recorder(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[list[str]]:
    """Replace `Stack.compose` with a recorder and hand back its call log."""
    calls: list[list[str]] = []

    def fake(self: Stack, *args: str, stdin: str | None = None) -> Result:
        calls.append(list(args))
        return Result(args=list(args), returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(Stack, "compose", fake)
    return calls


def _profiles(cell: Cell) -> list[str]:
    return [argument for name in cell.services for argument in ("--profile", name)]


@pytest.mark.parametrize("cell", CELLS, ids=CELL_IDS)
def test_up_profiles_both_its_teardown_and_its_start(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `up` runs a pre-emptive `down` first, so both argv lists are asserted:
    # the teardown is the half whose regression would be silent.
    calls = _recorder(monkeypatch)
    up(cell, "unused")
    assert calls == [
        [*_profiles(cell), "down", "-v", "--remove-orphans"],
        [*_profiles(cell), "up", "-d", "--build", "--wait"],
    ]


@pytest.mark.parametrize("cell", CELLS, ids=CELL_IDS)
def test_down_profiles_every_service_the_cell_declares(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recorder(monkeypatch)
    Stack(cell=cell, image="unused").down()
    assert calls == [[*_profiles(cell), "down", "-v", "--remove-orphans"]]


@pytest.mark.parametrize("cell", CELLS, ids=CELL_IDS)
def test_a_stack_that_fails_to_come_up_is_torn_down_with_its_profiles(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The leakiest path: `up` failed, so the profiled container may well be the
    # one that failed, and this is the `down` that has to reach it.
    calls = _recorder(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError, match=f"{cell.name}: the stack did not come up"):
        up(cell, "unused")
    assert calls[-1] == [*_profiles(cell), "down", "-v", "--remove-orphans"]


@pytest.mark.parametrize("cell", CELLS, ids=CELL_IDS)
def test_service_exec_profiles_only_a_service_the_cell_declared(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The readers reach their service through here, and the unit tests in
    # `test_stack_readers.py` stub `compose` - so they exercise the readers'
    # parsing while saying nothing about this argv. Asserted for a service the
    # cell declares AND for one it does not: passing `--profile` for a service
    # outside the cell's list would name a profile its stack never enabled.
    for service in cell.services:
        calls = _recorder(monkeypatch)
        Stack(cell=cell, image="unused").service_exec(service, "true")
        assert calls == [["--profile", service, "exec", "-T", service, "true"]]

    calls = _recorder(monkeypatch)
    Stack(cell=cell, image="unused").service_exec("web", "true")
    assert calls == [["exec", "-T", "web", "true"]], (
        "`web` is in the compose file for every cell and behind no profile, so naming a "
        "profile for it would be wrong"
    )


def test_some_cell_declares_a_service_so_the_assertions_above_can_fail() -> None:
    # Against a registry where every `services` is empty, `_profiles` returns
    # `[]` and all three parametrizations above only restate the bare argv -
    # they would pass however badly the profile threading were broken. Same
    # minimum, for the same reason, as the loops in `test_feature_registry.py`.
    assert [cell.name for cell in CELLS if cell.services], (
        "no cell declares a service, so no `--profile` argument is asserted anywhere"
    )
