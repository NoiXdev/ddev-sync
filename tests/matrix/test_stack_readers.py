"""The two service readers' failure and absence handling, checked without a stack.

`Stack.es_count` has to tell three cases apart that all look like "no
documents" from a distance: an index that does not exist yet (0), a service
that answered something else (an error), and a call that failed outright (an
error). Only the first may count as zero, and a cell run cannot reach the
other two without being broken in some other way first.

Each canned reply carries the shape the readers expect from curl: the body on
stdout with the HTTP status as its own last line, and stderr left for
whatever `docker compose exec` has to say - which the readers never parse.
"""

import subprocess
from dataclasses import dataclass

import pytest

from tests.matrix.cells import CELLS
from tests.matrix.stack import Stack


@dataclass
class _Reply:
    """One canned `compose exec` result."""

    returncode: int
    stdout: str
    stderr: str


def _stack(monkeypatch: pytest.MonkeyPatch, reply: _Reply) -> Stack:
    stack = Stack(cell=CELLS[0], image="unused")
    monkeypatch.setattr(
        Stack,
        "compose",
        lambda self, *args, stdin=None: subprocess.CompletedProcess(
            args=list(args), returncode=reply.returncode, stdout=reply.stdout, stderr=reply.stderr
        ),
    )
    return stack


def test_an_absent_index_counts_as_zero_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, '{"error":"index_not_found_exception"}\n404', ""))
    assert stack.es_count("app") == 0


def test_a_populated_index_returns_its_count(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, '{"count":7}\n200', ""))
    assert stack.es_count("app") == 7


def test_a_warning_on_stderr_does_not_break_a_count(monkeypatch: pytest.MonkeyPatch) -> None:
    # `docker compose exec` can warn on stderr; the body is on stdout, so a
    # healthy service must still count.
    stack = _stack(monkeypatch, _Reply(0, '{"count":7}\n200', "WARN something unrelated"))
    assert stack.es_count("app") == 7


def test_a_body_spanning_lines_is_kept_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    # The status is whatever follows the *last* newline, so a pretty-printed
    # body must not be mistaken for it.
    stack = _stack(monkeypatch, _Reply(0, '{\n  "count": 7\n}\n200', ""))
    assert stack.es_count("app") == 7


def test_an_unexpected_status_raises_rather_than_counting_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(monkeypatch, _Reply(0, "service unavailable\n503", ""))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        stack.es_count("app")


def test_a_failed_call_raises_rather_than_counting_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(monkeypatch, _Reply(1, "", "no such service: elasticsearch"))
    with pytest.raises(RuntimeError, match="counting app failed"):
        stack.es_count("app")


def test_a_failed_redis_call_raises_rather_than_returning_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(monkeypatch, _Reply(1, "", "no such service: redis"))
    with pytest.raises(RuntimeError, match="redis-cli LLEN k failed"):
        stack.redis("LLEN", "k")


def test_a_redis_reply_is_returned_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, "3\n", ""))
    assert stack.redis("LLEN", "k") == "3"


def test_deleting_an_absent_index_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # The delete exists to guarantee absence, so finding the index already
    # absent is the outcome it wanted, not a failure.
    stack = _stack(monkeypatch, _Reply(0, '{"error":"index_not_found_exception"}\n404', ""))
    stack.es_delete("app")


def test_deleting_a_present_index_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, '{"acknowledged":true}\n200', ""))
    stack.es_delete("app")


def test_a_refused_delete_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(0, "forbidden\n403", ""))
    with pytest.raises(RuntimeError, match="deleting app returned HTTP 403"):
        stack.es_delete("app")


def test_a_failed_delete_call_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack(monkeypatch, _Reply(1, "", "no such service: elasticsearch"))
    with pytest.raises(RuntimeError, match="deleting app failed"):
        stack.es_delete("app")
