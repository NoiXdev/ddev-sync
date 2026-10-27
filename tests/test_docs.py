import re

import pytest

from ddev_sync.docs import AREAS, ENTRIES, TOPICS, doc_link, doc_url
from ddev_sync.errors import DdevSyncError, SyncInterrupted, TargetError
from ddev_sync.operations.base import OperationError


def test_doc_url_points_at_the_troubleshooting_page() -> None:
    assert (
        doc_url("connection")
        == "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#connection"
    )


def test_an_unknown_slug_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown docs topic 'nope'"):
        doc_url("nope")
    with pytest.raises(ValueError, match="unknown docs topic 'nope'"):
        DdevSyncError("x", doc="nope")


def test_an_explicit_slug_wins() -> None:
    link = doc_link(OperationError("x", doc="dump-is-incomplete"))
    assert link is not None
    assert link.endswith("#dump-is-incomplete")


def test_extra_positional_arguments_still_reach_the_base() -> None:
    assert DdevSyncError("a", "b").args == ("a", "b")


def _raise_from(module: str, error: Exception) -> Exception:
    # A function whose globals claim to be `module`, so the traceback frame
    # looks like it belongs there.
    namespace: dict[str, object] = {"__name__": module, "error": error}
    exec("def boom():\n    raise error\n", namespace)
    boom = namespace["boom"]
    assert callable(boom)
    try:
        boom()
    except Exception as caught:
        return caught
    raise AssertionError("unreachable")


def test_the_raising_module_decides_the_area() -> None:
    caught = _raise_from("ddev_sync.operations.database", OperationError("x"))
    assert (doc_link(caught) or "").endswith("#database")


def _call_through(inner_module: str, outer_module: str) -> Exception:
    # `outer` (claiming `outer_module`) calls `inner` (claiming `inner_module`),
    # which raises: the traceback runs outer -> inner.
    inner_ns: dict[str, object] = {"__name__": inner_module}
    inner_ns["DdevSyncError"] = DdevSyncError
    exec("def inner():\n    raise DdevSyncError('x')\n", inner_ns)
    outer_ns: dict[str, object] = {"__name__": outer_module, "inner": inner_ns["inner"]}
    exec("def outer():\n    inner()\n", outer_ns)
    outer = outer_ns["outer"]
    assert callable(outer)
    try:
        outer()
    except Exception as caught:
        return caught
    raise AssertionError("unreachable")


def test_an_unmapped_helper_takes_its_callers_area() -> None:
    caught = _call_through("ddev_sync.operations.base", "ddev_sync.operations.database")
    assert (doc_link(caught) or "").endswith("#database")


def test_an_unmapped_helper_called_from_the_cli_gets_command_line() -> None:
    caught = _call_through("ddev_sync.operations.base", "ddev_sync.cli.commands")
    assert (doc_link(caught) or "").endswith("#command-line")


def test_the_longest_module_prefix_wins() -> None:
    caught = _raise_from("ddev_sync.config.targets", DdevSyncError("x"))
    assert (doc_link(caught) or "").endswith("#targets")
    caught = _raise_from("ddev_sync.config.models", DdevSyncError("x"))
    assert (doc_link(caught) or "").endswith("#configuration")


def test_the_class_is_the_last_resort() -> None:
    with pytest.raises(TargetError) as targets:
        raise TargetError("x")
    assert (doc_link(targets.value) or "").endswith("#targets")
    with pytest.raises(DdevSyncError) as bare:
        raise DdevSyncError("x")
    assert doc_link(bare.value) is None


def test_an_error_that_was_never_raised_has_no_traceback_to_read() -> None:
    assert doc_link(DdevSyncError("x")) is None
    assert (doc_link(TargetError("x")) or "").endswith("#targets")


def test_an_interrupt_carries_no_link() -> None:
    assert doc_link(SyncInterrupted("x")) is None


def test_every_slug_obeys_the_slug_rule() -> None:
    rule = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
    assert all(rule.match(slug) for slug in TOPICS)
    assert len(set(AREAS)) == len(AREAS)
    assert len(set(ENTRIES)) == len(ENTRIES)
    assert not set(AREAS) & set(ENTRIES)
