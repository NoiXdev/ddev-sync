"""One whole sync per cell, against a real framework installation.

Each test states one claim about the profile, so a failure names the claim
rather than "the sync broke". The registry supplies what differs between
cells; the bodies are identical for every variant, which is what makes adding
a cell three recipe files rather than a new test.
"""

import re

import pytest

from tests.matrix.cells import CELLS, Cell
from tests.matrix.paths import cell_dir, work_dir
from tests.matrix.stack import Stack

pytestmark = pytest.mark.matrix

# The seeds use `@acme-production.example`, which reads as production data
# rather than as a placeholder: a sanitize rule that rewrote nothing leaves
# these behind, and that is exactly what must not survive.
PRODUCTION_ADDRESS = re.compile(r"@acme-production\.example")
# The local parts of the seeded addresses. A rule that rewrote only the domain
# (`alice.hofer@example.invalid`) still leaks who the customer is.
PRODUCTION_NAMES = re.compile(r"alice\.hofer|bruno\.kalb")


@pytest.fixture(params=CELLS, ids=lambda cell: cell.name)
def cell(request: pytest.FixtureRequest) -> Cell:
    """The variant under test."""
    parameter: Cell = request.param
    return parameter


def test_the_required_tables_exist_where_preflight_looks(stack: Stack, cell: Cell) -> None:
    # The claim: each non-tolerated `requires_table` names a table a real
    # installation of this framework actually has. Asserted against the
    # **remote** database, because that is where the preflight probe looks -
    # and the remote's schema was dumped from the installation the framework
    # created, so a table present here is one the framework really makes. A
    # missing one aborts the sync before the dump: the engine working
    # correctly and the profile being wrong.
    for table in cell.required_tables:
        probe = stack.remote_sql(f"SHOW TABLES LIKE '{table}'")
        assert probe.returncode == 0, probe.stderr
        assert table in probe.stdout, (
            f"{cell.profile} declares requires_table {table!r}, "
            "which this installation does not create"
        )


def test_a_whole_sync_succeeds(stack: Stack, cell: Cell) -> None:
    # The headline claim: every command this profile declares exists and
    # exits 0 against a real installation - the setup commands included,
    # which run in the web container via `docker exec`.
    result = stack.engine("--target", "production", "--steps", "db,files,sanitize,setup", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"


def test_the_files_step_pulled_the_remote_tree(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `files.paths` map production's directories onto
    # real local ones. Every file the cell put on the remote must have arrived
    # at the same relative path - which holds for every profile whose remote
    # and local paths coincide, and a cell where they differ states its own
    # expectation here.
    tree = cell_dir(cell.name) / "remote"
    for source in sorted(path for path in tree.rglob("*") if path.is_file()):
        relative = source.relative_to(tree)
        pulled = work_dir(cell.name) / relative
        assert pulled.is_file(), f"{relative} was not pulled from the remote"
        assert pulled.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_no_production_address_survives(stack: Stack, cell: Cell) -> None:
    if cell.password_column is None:
        pytest.skip(f"{cell.profile} declares no user table to anonymise")
    table, _column = cell.password_column
    rows = stack.sql(f"SELECT * FROM `{table}`")
    # An empty table satisfies the check below trivially - which is what the
    # local table is before the import, so a sync that imported nothing would
    # otherwise pass.
    assert rows, f"{table} is empty - the dump or the import did not carry the seeded rows"
    assert not PRODUCTION_ADDRESS.search(rows), (
        f"a production address survived in {table}: {rows[:400]}"
    )
    assert not PRODUCTION_NAMES.search(rows), (
        f"the local part of a production address survived in {table}: {rows[:400]}"
    )


def test_every_password_was_reset_to_the_profiles_value(stack: Stack, cell: Cell) -> None:
    if cell.password_column is None:
        pytest.skip(f"{cell.profile} declares no reset_passwords rule")
    table, column = cell.password_column
    hashes = stack.sql(f"SELECT DISTINCT `{column}` FROM `{table}`").splitlines()
    assert hashes, f"{table} is empty - the dump or the import did not carry the seeded rows"
    assert len(set(hashes)) == 1, f"not every password was reset: {hashes}"
    assert "disabled" in hashes[0], f"the reset value is not the profile's: {hashes[0]!r}"


def test_the_local_admin_was_created(stack: Stack, cell: Cell) -> None:
    if cell.admin_user is None:
        pytest.skip(f"{cell.profile} declares no create_admin rule")
    # What this settles: the driver's argv is the real CLI's. A wrong
    # subcommand name fails here and nowhere else in the suite.
    result = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert cell.admin_user in result.stdout


def test_every_truncate_rule_emptied_its_table(stack: Stack, cell: Cell) -> None:
    # The claim: each table a truncate rule names - the tolerated ones too -
    # is empty after the sync. `tolerate_missing_table` only decides what
    # happens when the table is absent; Laravel's migrations create all three,
    # so a rule that never ran must show up as a row left behind. The remote
    # is checked first, so removing a row from the seed cannot make this pass
    # vacuously.
    if not cell.truncated_tables:
        pytest.skip(f"{cell.profile} declares no truncate rules")
    for table in cell.truncated_tables:
        seeded = stack.remote_sql(f"SELECT COUNT(*) FROM `{table}`")
        assert seeded.returncode == 0, seeded.stderr
        assert seeded.stdout.strip() != "0", f"the seed put no row into {table}"
        assert stack.sql(f"SELECT COUNT(*) FROM `{table}`") == "0", (
            f"{table} still has rows: its truncate rule did not take effect"
        )


def test_storage_link_created_the_public_symlink(stack: Stack, cell: Cell) -> None:
    # The claim: `php artisan storage:link` - which the profile allows to
    # fail, and which the engine then reports as OK - really created
    # `public/storage`. Asserted on its effect, because the exit status of an
    # allow_failure command is invisible to every other test.
    if cell.profile != "laravel":
        pytest.skip("storage:link is Laravel's")
    link = work_dir(cell.name) / "public" / "storage"
    assert link.is_symlink(), "public/storage is not a symlink: storage:link did not run"
    assert link.resolve() == (work_dir(cell.name) / "storage" / "app" / "public").resolve()


def test_artisan_migrate_applied_the_pending_migration(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `php artisan migrate --force` runs against the
    # real project and applies what production lacks. The install leaves one
    # migration unapplied (see cells/mxlaravel/install.sh) and the remote dump
    # does not contain its table, so after the sync the table exists only if
    # migrate ran. The profile allows that command to fail, so neither the
    # sync's exit status nor `migrate:status` alone could say.
    if cell.profile != "laravel":
        pytest.skip("artisan is Laravel's")
    assert stack.sql("SHOW TABLES LIKE 'matrix_marker'") == "matrix_marker", (
        "the pending migration was not applied: `migrate --force` did not take effect"
    )
    status = stack.web(["php", "artisan", "migrate:status"])
    assert status.returncode == 0, f"stdout:\n{status.stdout}\n\nstderr:\n{status.stderr}"
    assert "Pending" not in status.stdout, status.stdout
