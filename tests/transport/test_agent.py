"""Which of DDEV's agent sockets this process talks to."""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ddev_sync.transport.agent import answers, use_an_answering_agent


@contextmanager
def listening(path: Path) -> Iterator[None]:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    try:
        yield
    finally:
        server.close()


def dead(path: Path) -> Path:
    """A socket file nothing listens on any more, as a restarted agent leaves behind."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.close()
    return path


@pytest.fixture
def directory() -> Iterator[Path]:
    # Short: a unix socket path is limited to about a hundred bytes, which a
    # pytest tmp_path on macOS already comes close to.
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp") as name:
        yield Path(name)


def test_a_listening_socket_answers_and_a_dead_one_does_not(directory: Path) -> None:
    with listening(directory / "socket"):
        assert answers(str(directory / "socket")) is True
    assert answers(str(dead(directory / "proxy-socket"))) is False
    assert answers(str(directory / "missing")) is False


def test_a_configured_socket_that_answers_is_kept(directory: Path) -> None:
    environ = {"SSH_AUTH_SOCK": str(directory / "proxy-socket")}
    with listening(directory / "proxy-socket"), listening(directory / "socket"):
        use_an_answering_agent(environ)
    assert environ["SSH_AUTH_SOCK"] == str(directory / "proxy-socket")


def test_a_dead_proxy_socket_gives_way_to_the_socket_beside_it(directory: Path) -> None:
    environ = {"SSH_AUTH_SOCK": str(dead(directory / "proxy-socket"))}
    with listening(directory / "socket"):
        use_an_answering_agent(environ)
    assert environ["SSH_AUTH_SOCK"] == str(directory / "socket")


def test_nothing_answering_leaves_the_configured_socket_for_doctor(directory: Path) -> None:
    configured = str(dead(directory / "proxy-socket"))
    environ = {"SSH_AUTH_SOCK": configured}
    dead(directory / "socket")
    use_an_answering_agent(environ)
    assert environ["SSH_AUTH_SOCK"] == configured


def test_no_configured_socket_is_left_alone() -> None:
    environ: dict[str, str] = {}
    use_an_answering_agent(environ)
    assert environ == {}
