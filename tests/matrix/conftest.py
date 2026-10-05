"""Fixtures shared by every cell.

The image reference is an input rather than a constant: a nightly run builds
it from `main`, and a release run passes the candidate it just built. One
definition of a cell, two callers - a nightly run must never silently verify a
different artifact than a release run does.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.matrix.cells import Cell
from tests.matrix.paths import KEYS_DIR, WORK_ROOT, cell_dir, remote_dir, work_dir
from tests.matrix.stack import Stack, up

MATRIX_IMAGE = os.environ.get("DDEV_SYNC_MATRIX_IMAGE", "ddev-sync:dev")


@pytest.fixture(scope="session")
def ssh_key() -> Path:
    """A throwaway keypair the stand-in production host trusts."""
    KEYS_DIR.mkdir(exist_ok=True)
    private = KEYS_DIR / "id_ed25519"
    if not private.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(private), "-q"], check=True
        )
    private.chmod(0o600)
    return private


def _write_project_config(cell: Cell) -> None:
    """Write the two files the engine reads to know what it is syncing.

    Written **after** the install: `composer create-project .` refuses a
    directory that already holds anything, and `.ddev/` would be exactly that.
    """
    ddev = work_dir(cell.name) / ".ddev"
    ddev.mkdir(parents=True, exist_ok=True)
    # The docroot is quoted, as DDEV writes it (`docroot: ""` for a project
    # whose root is its web root). Unquoted, an empty value is YAML null and
    # the engine reads it as the string "None", a directory that does not exist.
    (ddev / "config.yaml").write_text(
        f'name: {cell.name}\ntype: {cell.project_type}\ndocroot: "{cell.docroot}"\n',
        encoding="utf-8",
    )
    shutil.copy(cell_dir(cell.name) / "ddev-sync.yaml", ddev / "ddev-sync.yaml")


def _install(stack: Stack, cell: Cell) -> None:
    """Run the cell's install recipe inside its web container."""
    recipe = (cell_dir(cell.name) / "install.sh").read_text(encoding="utf-8")
    result = stack.web(["sh", "-euc", recipe])
    if result.returncode != 0:
        raise RuntimeError(
            f"{cell.name}: install failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )
    # The recipe's last lines print the framework version; keep them visible
    # under `pytest -s` so the version a cell verified is on record.
    print(f"\n[{cell.name}] installed:\n{result.stdout[-600:]}")


def _dump(stack: Stack, cell: Cell, *options: str) -> str:
    """Dump the local database the framework's installer populated."""
    dump = stack.web(
        [
            "mariadb-dump",
            "--skip-comments",
            f"--host=ddev-{cell.name}-db",
            "--user=db",
            "--password=db",
            *options,
        ]
    )
    if dump.returncode != 0:
        raise RuntimeError(f"{cell.name}: dumping the local database failed:\n{dump.stderr}")
    return dump.stdout


def _seed(stack: Stack, cell: Cell) -> None:
    """Give the stand-in remote this installation's schema and fake data.

    The schema is dumped from the local database the framework's own
    installer just populated, rather than written by hand. That is what keeps
    a `requires_table` assertion honest: a hand-written schema would prove
    only that the guess matches itself.

    A cell may also list, in `keep-data.txt`, tables whose **rows** are
    framework state rather than content (Laravel's `migrations`). A real
    production database carries them, and without them the setup step's
    migrate command would run against a schema it believes is empty and
    fail - a failure `allow_failure` then hides, which is how a harness
    deviating from production could make the profile look verified.
    """
    parts = [_dump(stack, cell, "--no-data", "db")]
    keep = cell_dir(cell.name) / "keep-data.txt"
    if keep.is_file():
        tables = keep.read_text(encoding="utf-8").split()
        parts.append(_dump(stack, cell, "--no-create-info", "db", *tables))
    parts.append((cell_dir(cell.name) / "seed.sql").read_text(encoding="utf-8"))
    loaded = stack.remote_script("\n".join(parts))
    if loaded.returncode != 0:
        raise RuntimeError(f"{cell.name}: seeding the remote failed:\n{loaded.stderr}")


def _seed_remote_files(cell: Cell) -> None:
    """Give the stand-in remote the file tree a real production host has.

    A profile's `files.paths` name directories that exist on any real
    deployment, and the files step fails outright (rsync exit 23) when one is
    missing. The tree comes from the cell's `remote/` directory, so it is
    hand-written: it states what production has, and it is **not** evidence
    that the framework creates those directories. That evidence is the local
    side - the installed project must have the directory the profile maps
    onto, or the pull has nowhere to land.

    A cell may also have a `remote-only/` directory: files that exist on the
    production host but belong to no mapped path (WordPress's wp-config.php),
    so a sync never pulls them and no test expects it to.
    """
    for name in ("remote", "remote-only"):
        tree = cell_dir(cell.name) / name
        if tree.is_dir():
            shutil.copytree(tree, remote_dir(cell.name), dirs_exist_ok=True)


class Stacks:
    """The cells' stacks, each brought up the first time a test asks for it.

    Lazy because a CI job runs one cell (`-k <cell>`) and must not pay for the
    other frameworks' installs: an eager loop over `CELLS` installed all of
    them whichever tests were selected. Whatever was started is kept so the
    session's teardown closes it, and a cell whose setup failed keeps failing
    with the same error instead of being installed again for every test.
    """

    def __init__(self) -> None:
        self._running: dict[str, Stack] = {}
        self._failed: dict[str, Exception] = {}

    def get(self, cell: Cell) -> Stack:
        """Install, seed and start `cell` on first use, then reuse it."""
        if cell.name in self._failed:
            raise self._failed[cell.name]
        if cell.name not in self._running:
            try:
                self._start(cell)
            except Exception as error:
                self._failed[cell.name] = error
                raise
        return self._running[cell.name]

    def _start(self, cell: Cell) -> None:
        project = work_dir(cell.name)
        if project.exists():
            shutil.rmtree(project)
        project.mkdir(parents=True)
        remote_dir(cell.name).mkdir(parents=True, exist_ok=True)
        stack = up(cell, MATRIX_IMAGE)
        # Registered before the install, so a failing install is still torn down.
        self._running[cell.name] = stack
        _install(stack, cell)
        _write_project_config(cell)
        _seed(stack, cell)
        _seed_remote_files(cell)

    def close(self) -> None:
        for stack in self._running.values():
            stack.down()
        # The installed project is a few hundred megabytes of vendor code, and
        # a stale one would be wiped at the next start anyway. Kept on request
        # so a failing cell can be inspected after the run.
        if not os.environ.get("DDEV_SYNC_MATRIX_KEEP"):
            for name in self._running:
                shutil.rmtree(work_dir(name), ignore_errors=True)
                shutil.rmtree(remote_dir(name), ignore_errors=True)


@pytest.fixture(scope="session")
def stacks(ssh_key: Path) -> Iterator[Stacks]:
    """The session's stacks. Session-scoped on purpose: `composer
    create-project` dominates the runtime, so a per-test install would make the
    matrix unaffordable."""
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    registry = Stacks()
    try:
        yield registry
    finally:
        registry.close()


@pytest.fixture
def stack(cell: Cell, stacks: Stacks) -> Stack:
    """The running stack for the cell this test is parameterised over."""
    return stacks.get(cell)
