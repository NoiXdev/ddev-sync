"""Fixtures shared by every cell.

The image reference is an input rather than a constant: a nightly run builds
it from `main`, and a release run passes the candidate it just built. One
definition of a cell, two callers - a nightly run must never silently verify a
different artifact than a release run does.
"""

import contextlib
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

from tests.matrix.cells import Cell
from tests.matrix.paths import KEYS_DIR, WORK_ROOT, cell_dir, remote_dir, work_dir
from tests.matrix.stack import Stack, up

DEFAULT_MATRIX_IMAGE = "ddev-sync:dev"
# The repository root, which is also the build context: this file is
# <root>/tests/matrix/conftest.py.
REPO_ROOT = Path(__file__).resolve().parents[2]


def image_under_test(environ: Mapping[str, str]) -> tuple[str, bool]:
    """The image to run, and whether this harness has to build it first.

    Split out as a pure function so the decision is testable without Docker:
    the fixture below is session-scoped and runs a build, which is exactly the
    shape a test cannot reach.

    An empty value counts as unset. `DDEV_SYNC_MATRIX_IMAGE=` in a shell, or
    an `env:` block whose expression resolved to nothing, leaves the name
    present and empty; honouring that literally would run `docker build -t ""`
    and fail a long way from the cause.
    """
    configured = environ.get("DDEV_SYNC_MATRIX_IMAGE")
    if configured:
        return configured, False
    return DEFAULT_MATRIX_IMAGE, True


MATRIX_IMAGE, _MUST_BUILD_IMAGE = image_under_test(os.environ)


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


def _make_writable_and_retry(
    function: Callable[..., object], path: str, _error: BaseException
) -> None:
    """Let `rmtree` delete what an installer deliberately made read-only.

    Drupal's installer takes the write bit off `web/sites/default` and its
    `settings.php` as soon as it finishes, so a plain `rmtree` of the project
    fails with "permission denied" - at teardown (silently, under
    `ignore_errors`) and then again at the next run's start, which wipes the
    previous tree first. Deleting a file needs write permission on its
    *directory*, so the parent is opened up before the failed call is retried.
    """
    parent = Path(path).parent
    parent.chmod(parent.stat().st_mode | stat.S_IWUSR)
    function(path)


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, onexc=_make_writable_and_retry)


def _install(stack: Stack, cell: Cell) -> None:
    """Run the cell's install recipe inside its web container."""
    recipe = (cell_dir(cell.name) / "install.sh").read_text(encoding="utf-8")
    # A cell's `app/` directory is application files its recipe installs -
    # FOSElasticaBundle's configuration and the entity it indexes, for
    # `mxfeatsymfony`. They are files rather than heredocs inside `install.sh`
    # because a YAML block and a PHP class with attributes and namespaces are
    # reviewable as files and unreadable as shell quoting.
    #
    # Copied into the container at /tmp, not into the bind-mounted project:
    # a recipe whose first line is `composer create-project .` refuses to run
    # at all if the project directory holds anything, dot entries included,
    # because `Composer\Util\Filesystem::isDirEmpty` builds its Finder with
    # `ignoreVCS(false)->ignoreDotFiles(false)`. Most of this matrix's recipes
    # start that way (`mxwordpress`'s `wp core download` is the exception), so
    # the project directory is the one place staged recipe inputs cannot live.
    # Measured against the composer this image pins: a `.matrix-app/` in the
    # project fails the very first line of the recipe with
    # `Project directory "/var/www/html/." is not empty.`
    staged = cell_dir(cell.name) / "app"
    if staged.is_dir():
        copied = stack.compose("cp", str(staged), "web:/tmp/matrix-app")
        if copied.returncode != 0:
            raise RuntimeError(
                f"{cell.name}: staging app/ into the web container failed:\n{copied.stderr}"
            )
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
            # `local-db` runs with no certificate (see the compose file), and
            # the client in the web image requires a verified TLS connection by
            # default.
            "--skip-ssl",
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
    framework state rather than content: Laravel's `migrations`, Symfony's
    `doctrine_migration_versions`, Drupal's `config` and `key_value`,
    WordPress's `mxwp_options`. A real production database carries them, and
    without them the setup step's migrate command would run against a schema
    it believes is empty and fail - a failure `allow_failure` then hides,
    which is how a harness deviating from production could make the profile
    look verified. Some are also load-bearing for a test's own premise:
    Drupal's `key_value` holds the recorded schema version that
    `test_drush_updatedb_applied_the_pending_update` needs to be 10000, and
    WordPress's `mxwp_options` holds the production URL that
    `test_the_production_url_is_gone` asserts is rewritten.
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

    def __init__(self, image: str) -> None:
        # Taken rather than read from the module global, so that the fixture
        # which may have just *built* this image is the thing that names it.
        # Read globally, the build would be a side effect a reader has to go
        # looking for.
        self._image = image
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
            _remove_tree(project)
        project.mkdir(parents=True)
        remote_dir(cell.name).mkdir(parents=True, exist_ok=True)
        stack = up(cell, self._image)
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
                # Best effort, as before: the stand-in remote's bind mount may
                # hold files this user cannot remove, and that must not turn a
                # finished run into an error.
                for tree in (work_dir(name), remote_dir(name)):
                    with contextlib.suppress(OSError):
                        _remove_tree(tree)


@pytest.fixture(scope="session")
def matrix_image() -> str:
    """Build the image under test from this working tree, unless one was given.

    The matrix always runs the *image*, never the checkout. An image older than
    the tree therefore verifies code nobody is changing - and reports green
    while doing it, which is the failure mode worth spending a build on: a
    stale local `ddev-sync:dev` once made a profile change look like a product
    defect, because the image still carried the profile from before it.

    Building rather than detecting staleness is deliberate. A check could
    compare `profiles/` and `features/`, which the image bundles as plain
    files, but `src/` arrives as a built wheel and is not comparable that way -
    so the half that a check would miss is the half most changes live in.
    Layer caching makes the common case a few seconds.

    An explicitly set `DDEV_SYNC_MATRIX_IMAGE` is trusted and never rebuilt:
    that is CI's path, where `.github/workflows/matrix.yaml` builds the
    candidate itself and hands it over, and where rebuilding would verify a
    different artifact than the release pipeline is about to publish.
    """
    if _MUST_BUILD_IMAGE:
        subprocess.run(
            ["docker", "build", "--tag", MATRIX_IMAGE, str(REPO_ROOT)],
            check=True,
            capture_output=True,
            text=True,
        )
    return MATRIX_IMAGE


@pytest.fixture(scope="session")
def stacks(ssh_key: Path, matrix_image: str) -> Iterator[Stacks]:
    """The session's stacks. Session-scoped on purpose: `composer
    create-project` dominates the runtime, so a per-test install would make the
    matrix unaffordable."""
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    registry = Stacks(matrix_image)
    try:
        yield registry
    finally:
        registry.close()


@pytest.fixture
def stack(cell: Cell, stacks: Stacks) -> Stack:
    """The running stack for the cell this test is parameterised over."""
    return stacks.get(cell)
