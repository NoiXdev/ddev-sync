"""Where the matrix's cells and their working directories live.

A module of its own, like `tests/integration/paths.py`, because the compose
file needs these paths as environment variables before any test runs: a bind
mount has to be known when the stack comes up, not when a test asks for it.
"""

from pathlib import Path

HERE = Path(__file__).parent
CELLS_ROOT = HERE / "cells"
# One working directory per cell, holding the installed framework. Outside
# `tmp_path` for the same reason the integration suite's project is: the web
# and engine containers bind-mount it at this exact absolute path, because
# `transport/container.py::web_command` passes a host path straight through
# as `docker exec -w`.
WORK_ROOT = (HERE / ".work").resolve()
KEYS_DIR = (HERE / ".keys").resolve()


def cell_dir(name: str) -> Path:
    """The three recipes for `name`."""
    return CELLS_ROOT / name


def work_dir(name: str) -> Path:
    """The installed project for `name`."""
    return WORK_ROOT / name


def remote_dir(name: str) -> Path:
    """The files the stand-in production host serves for `name`."""
    return WORK_ROOT / f"{name}-remote"
