"""One whole sync per cell, against a real framework installation.

Each test states one claim about the profile, so a failure names the claim
rather than "the sync broke". The registry supplies what differs between
cells; the bodies are identical for every variant, which is what makes adding
a cell three recipe files rather than a new test.
"""

import re

import pytest

from ddev_sync.operations.catalog import LOCAL_ADMIN_PASSWORD
from tests.matrix.cells import CELLS, Cell
from tests.matrix.paths import cell_dir, remote_dir, work_dir
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


def test_every_seeded_password_was_reset_to_the_profiles_value(stack: Stack, cell: Cell) -> None:
    if cell.password_column is None:
        pytest.skip(f"{cell.profile} declares no reset_passwords rule")
    table, column = cell.password_column
    assert cell.identity_column is not None and cell.seeded_users, (
        f"{cell.name} has a password column but does not say which rows it seeded"
    )
    # Only the seeded rows: `create_admin` runs after the reset and adds a row
    # whose hash is rightly not the reset value, and a framework CLI may add
    # system users of its own (TYPO3's `_cli_`). What the cell can state is
    # that every row *it put there* was reset.
    logins = ", ".join(f"'{user}'" for user in cell.seeded_users)
    hashes = stack.sql(
        f"SELECT `{column}` FROM `{table}` WHERE `{cell.identity_column}` IN ({logins})"
    ).splitlines()
    # Every seeded user must be present, or a sync that dropped the rows (or an
    # identity column that matches nothing) would pass with an empty list.
    assert len(hashes) == len(cell.seeded_users), (
        f"expected {len(cell.seeded_users)} seeded rows in {table}, found {len(hashes)}"
    )
    assert len(set(hashes)) == 1, f"not every seeded password was reset: {hashes}"
    assert "disabled" in hashes[0], f"the reset value is not the profile's: {hashes[0]!r}"


def test_the_local_admin_was_created(stack: Stack, cell: Cell) -> None:
    if cell.admin_user is None:
        pytest.skip(f"{cell.profile} declares no create_admin rule")
    # What this settles: the driver's argv is the real CLI's. A wrong
    # subcommand name fails here and nowhere else in the suite.
    result = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert cell.admin_user in result.stdout
    # A second run finds the account already there. The engine tolerates that
    # only by recognising the real CLI's refusal message, which is observed
    # here rather than assumed: a marker that does not match what the CLI
    # prints would fail every second sync of a developer's project.
    again = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert again.returncode == 0, f"stdout:\n{again.stdout}\n\nstderr:\n{again.stderr}"
    assert "already exists" in again.stdout, again.stdout
    # The output names the user whether the account was created or merely found
    # to exist, so the row itself is what shows the CLI really created it.
    assert cell.password_column is not None and cell.identity_column is not None
    table, _column = cell.password_column
    count = stack.sql(
        f"SELECT COUNT(*) FROM `{table}` WHERE `{cell.identity_column}` = '{cell.admin_user}'"
    )
    assert count == "1", f"{cell.admin_user!r} is not exactly one row of {table}: {count}"


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


def test_the_typo3_schema_step_restored_the_missing_column(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `vendor/bin/typo3 extension:setup` really brings
    # the imported schema in line with the code. The install drops
    # `tt_content.header_link` (see the install.sh of each TYPO3 cell), the
    # remote dump therefore lacks it, and the column exists after the sync
    # only if the step re-added it. The step used to be `database:updateschema`
    # under `allow_failure`, which the engine printed as OK while doing
    # nothing: the effect is what would have shown that, so it stays asserted.
    if cell.project_type != "typo3":
        pytest.skip("extension:setup is TYPO3's")
    assert stack.sql("SHOW COLUMNS FROM tt_content LIKE 'header_link'").startswith("header_link"), (
        "the missing column was not restored: the schema step did not take effect"
    )


def test_a_placeholder_table_resolved_to_the_real_one(stack: Stack, cell: Cell) -> None:
    if cell.name != "mxsymfony":
        pytest.skip("only the symfony profile declares a placeholder requires_table")
    # `${SYMFONY_USER_TABLE}` resolves at parse time from
    # `database.symfony_user_table`. Had resolution fallen back to the default
    # `user`, the preflight probe would abort on a table this installation does
    # not have. A real run rather than `--dry-run`: a dry run renders the plan
    # and never reaches the probe, so it passes whichever table the placeholder
    # resolved to - verified by deleting the override from the cell's config.
    result = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    # The two SHOW TABLES lines make the claim specific: the default table does
    # not exist here, so only `app_user` can have been the one the rules used.
    assert stack.sql("SHOW TABLES LIKE 'user'") == ""
    assert stack.sql("SHOW TABLES LIKE 'app_user'") == "app_user"
    # The effect, not just the exit status: `anonymize-users` rewrote the rows
    # of the resolved table.
    emails = stack.sql("SELECT email FROM app_user ORDER BY id").splitlines()
    assert emails == ["user1@example.invalid", "user2@example.invalid"], emails


def test_doctrine_migrate_applied_the_pending_migration(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `php bin/console doctrine:migrations:migrate`
    # runs against the real project and applies what production lacks. The
    # install leaves one migration unapplied (see cells/mxsymfony/install.sh)
    # and the remote dump does not contain its table, so after the sync the
    # table exists only if the command ran. The profile allows that command to
    # fail, so neither the sync's exit status nor the engine's "OK" line could
    # say - and Symfony's console runs an unambiguous prefix of a command name
    # (`doctrine:migrations:migrat` still migrates), so only a real
    # misspelling, not an abbreviation, makes this fail.
    if cell.profile != "symfony":
        pytest.skip("doctrine migrations are Symfony's")
    assert stack.remote_sql("SHOW TABLES LIKE 'matrix_marker'").stdout.strip() == "", (
        "the remote already has the marker table: the seed carried the migration in"
    )
    assert stack.sql("SHOW TABLES LIKE 'matrix_marker'") == "matrix_marker", (
        "the pending migration was not applied: `doctrine:migrations:migrate` did not take effect"
    )
    # Doctrine's own bookkeeping agrees: the version was recorded as executed.
    recorded = stack.sql(
        "SELECT COUNT(*) FROM doctrine_migration_versions "
        "WHERE version LIKE '%Version20260102000000'"
    )
    assert recorded == "1", f"the migration is not recorded as executed: {recorded}"


def test_the_production_url_is_gone(stack: Stack, cell: Cell) -> None:
    if cell.production_url is None:
        pytest.skip(f"{cell.profile} rewrites no URL")
    assert cell.password_column is not None
    # The options table shares the users table's prefix, which the cell
    # chose; deriving it keeps the test from hardcoding `wp_`.
    options = cell.password_column[0].removesuffix("users") + "options"
    query = (
        f"SELECT option_name, option_value FROM `{options}` "
        "WHERE option_name IN ('siteurl', 'home')"
    )
    # The precondition: production really carries the URL. Without it the
    # assertions below would pass for a seed that never planted it.
    seeded = stack.remote_sql(query)
    assert seeded.returncode == 0, seeded.stderr
    assert seeded.stdout.count(cell.production_url) == 2, seeded.stdout
    # Asserted against `siteurl` and `home` rather than the whole database,
    # because they are the two values WordPress actually reads - a rewrite
    # that missed them would leave the local site pointed at production,
    # which is the failure the rule exists to prevent.
    local = stack.sql(query).splitlines()
    # DDEV serves a project at `<name>.ddev.site`, which is where the rewrite
    # has to point.
    local_url = f"https://{cell.name}.ddev.site"
    assert sorted(local) == [f"home\t{local_url}", f"siteurl\t{local_url}"], local


def test_the_wordpress_prefix_read_from_production_reached_the_rules(
    stack: Stack, cell: Cell
) -> None:
    if cell.profile != "wordpress":
        pytest.skip("`${WP_TABLE_PREFIX}` is WordPress's")
    assert cell.password_column is not None and cell.identity_column is not None
    table, _column = cell.password_column
    # This installation chose a prefix other than WordPress's default, so the
    # default table does not exist and only the prefix production's wp-config.php
    # states can have been named by the rules - which is what makes the next
    # query meaningful.
    assert stack.sql("SHOW TABLES LIKE 'wp\\_users'") == ""
    logins = ", ".join(f"'{user}'" for user in cell.seeded_users)
    where = f"`{cell.identity_column}` IN ({logins})"
    # The effect of `anonymize-users` on the resolved table: every seeded row
    # carries the rewritten address, and the rows are really there to be
    # counted.
    rewritten = stack.sql(
        f"SELECT COUNT(*) FROM `{table}` "
        f"WHERE {where} AND user_email = CONCAT('user', ID, '@example.invalid')"
    )
    assert rewritten == str(len(cell.seeded_users)), rewritten


def test_the_wordpress_admin_is_an_administrator_with_the_documented_password(
    stack: Stack, cell: Cell
) -> None:
    if cell.profile != "wordpress":
        pytest.skip("`wp user create` is WordPress's")
    assert cell.admin_user is not None
    # A fresh import first, so the account is the one this very sync created.
    # Re-running `sanitize` alone would not do: `reset-user-passwords` rewrites
    # every row of the users table, the admin created by an earlier run
    # included, and `create_admin` then finds the account already there and
    # leaves its hash alone.
    # The object cache is flushed first because this site has a persistent one
    # and the engine flushes it only in `setup`, *after* `create_admin`: with an
    # entry for `ddev` left over from an earlier run, `wp user create` believes
    # the account exists, reports "already registered" - which the engine
    # tolerates - and the freshly imported database has no such user. Reported
    # with the cell; this line keeps the assertions below about the argv.
    flushed = stack.web(["wp", "cache", "flush"])
    assert flushed.returncode == 0, flushed.stderr
    result = stack.engine("--target", "production", "--steps", "db,sanitize", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    # `--role` and `--user_pass` are two of the tokens `CreateAdmin` builds for
    # the wp_cli driver; the row existing (asserted elsewhere) shows neither.
    roles = stack.web(["wp", "user", "get", cell.admin_user, "--field=roles"])
    assert roles.returncode == 0, roles.stderr
    assert roles.stdout.strip() == "administrator", roles.stdout
    accepted = stack.web(["wp", "user", "check-password", cell.admin_user, LOCAL_ADMIN_PASSWORD])
    assert accepted.returncode == 0, f"the documented password does not log in: {accepted.stderr}"
    # `check-password` must be able to say no, or the line above proves nothing.
    refused = stack.web(["wp", "user", "check-password", cell.admin_user, "not-the-password"])
    assert refused.returncode != 0


def test_wp_cache_flush_emptied_the_object_cache(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `wp cache flush` really flushes. It is the
    # profile's only setup command and it is `allow_failure`, so the engine
    # prints OK whatever the command did; the effect is the only evidence.
    # The install gives the site a persistent object cache (see
    # cells/mxwordpress/install.sh) - without one the command has nothing to
    # act on outside its own process.
    if cell.profile != "wordpress":
        pytest.skip("`wp cache flush` is WordPress's")
    stored = stack.web(["wp", "cache", "set", "matrix_marker", "present", "matrix"])
    assert stored.returncode == 0, stored.stderr
    present = stack.web(["wp", "cache", "get", "matrix_marker", "matrix"])
    assert present.stdout.strip() == "present", "the entry did not persist before the flush"
    result = stack.engine("--target", "production", "--steps", "setup", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    gone = stack.web(["wp", "cache", "get", "matrix_marker", "matrix"])
    assert gone.returncode != 0, f"the entry survived `wp cache flush`: {gone.stdout}"


def test_the_wordpress_prefix_comes_from_production_not_from_a_default(
    stack: Stack, cell: Cell
) -> None:
    if cell.profile != "wordpress":
        pytest.skip("`${WP_TABLE_PREFIX}` is WordPress's")
    # The control for the test above, which passes whichever way the prefix
    # reached the rules as long as it is `mxwp_`. Here production's wp-config.php
    # is changed to a prefix no table has, and the sync must follow it: the
    # preflight probe then names `zz_users`. A resolver that ignored the remote
    # (a default, the local wp-config.php) would still find `mxwp_users` and
    # let the sync through. The remote directory is a bind mount, so the edit
    # is seen at once; it is put back whatever happens.
    config = remote_dir(cell.name) / "wp-config.php"
    original = config.read_text(encoding="utf-8")
    assert "$table_prefix = 'mxwp_';" in original
    try:
        config.write_text(original.replace("'mxwp_'", "'zz_'"), encoding="utf-8")
        result = stack.engine("--target", "production", "--steps", "db,sanitize", "--yes")
    finally:
        config.write_text(original, encoding="utf-8")
    assert result.returncode != 0, result.stdout
    assert "requires table 'zz_users'" in result.stdout, result.stdout
    assert "Nothing was downloaded or imported" in result.stdout, result.stdout


def test_drupal_rules_rewrote_users_field_data_not_users(stack: Stack, cell: Cell) -> None:
    if cell.profile != "drupal":
        pytest.skip("the `users` / `users_field_data` split is Drupal's")
    # The premise: `users` holds only the uuid and langcode, so the mail and
    # pass columns the rules rewrite can only be in `users_field_data`. Checked
    # on this installation rather than assumed, because a rule aimed at `users`
    # would fail with an unknown column instead of quietly doing nothing.
    columns = stack.sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'db' AND table_name = 'users' ORDER BY ordinal_position"
    )
    assert columns.splitlines() == ["uid", "uuid", "langcode"], columns
    # The seed put production's data into both tables, so the rewritten values
    # below cannot be leftovers of the installer.
    seeded = stack.remote_sql("SELECT uid, mail FROM users_field_data WHERE uid IN (11, 12)")
    assert seeded.returncode == 0, seeded.stderr
    assert "alice.hofer@acme-production.example" in seeded.stdout, seeded.stdout
    # The effect of `anonymize-users`: `CONCAT('user', uid, '@example.invalid')`
    # on the rows the cell seeded, and of `reset-user-passwords` on the column
    # that table holds.
    rewritten = stack.sql(
        "SELECT uid, mail, pass FROM users_field_data WHERE uid IN (11, 12) ORDER BY uid"
    ).splitlines()
    assert rewritten == [
        "11\tuser11@example.invalid\t$argon2i$disabled",
        "12\tuser12@example.invalid\t$argon2i$disabled",
    ], rewritten
    # `users` is where the import left it: the rules never touched it.
    uuids = stack.sql("SELECT uid, uuid, langcode FROM users WHERE uid IN (11, 12) ORDER BY uid")
    assert uuids.splitlines() == [
        "11\taaaaaaaa-1111-4111-8111-aaaaaaaaaaaa\ten",
        "12\tbbbbbbbb-2222-4222-8222-bbbbbbbbbbbb\ten",
    ], uuids


def test_drush_updatedb_applied_the_pending_update(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `vendor/bin/drush updatedb --yes` runs against
    # the real project and applies what production lacks. The install leaves
    # one update unapplied (see cells/mxdrupal/install.sh): production's
    # recorded schema version for `matrix_marker` is 10000 while the code
    # carries an update 10001, which creates a table. The profile allows the
    # command to fail and the engine then prints OK, so the table is the only
    # evidence it ran.
    if cell.profile != "drupal":
        pytest.skip("drush updatedb is Drupal's")
    schema_version = (
        "SELECT value FROM key_value WHERE collection = 'system.schema' AND name = 'matrix_marker'"
    )
    # The preconditions: the seed carries neither the table nor the new version.
    assert stack.remote_sql("SHOW TABLES LIKE 'matrix_marker'").stdout.strip() == "", (
        "the remote already has the marker table: the seed carried the update in"
    )
    assert stack.remote_sql(schema_version).stdout.strip() == "i:10000;"
    assert stack.sql("SHOW TABLES LIKE 'matrix_marker'") == "matrix_marker", (
        "the pending update was not applied: `drush updatedb` did not take effect"
    )
    # Drupal's own bookkeeping agrees: the update is recorded as run.
    assert stack.sql(schema_version) == "i:10001;"
    status = stack.web(["vendor/bin/drush", "updatedb:status"])
    assert status.returncode == 0, f"stdout:\n{status.stdout}\n\nstderr:\n{status.stderr}"
    assert "No database updates required" in status.stdout + status.stderr, status.stdout


def test_the_flush_cache_rule_removed_the_planted_cache_row(stack: Stack, cell: Cell) -> None:
    # The claim: the profile's `flush-cache` rule really clears the cache. The
    # engine ignores that rule's exit status on purpose (`FlushCache` tolerates
    # failure), so a command that does not exist could never fail the sync, and
    # the effect is the only evidence. A row is planted in a cache table that no
    # other sanitize rule touches (see `FlushMarker`); only the flush can
    # remove it.
    marker = cell.flush_marker
    if marker is None:
        pytest.skip(f"{cell.profile} has no observable flush_cache effect in the database")
    count = f"SELECT COUNT(*) FROM `{marker.table}` WHERE {marker.where}"
    stack.sql(marker.insert)
    assert stack.sql(count) == "1", "the cache row was not planted"
    result = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert stack.sql(count) == "0", "the cache row survived: `flush-cache` did not take effect"


def test_drupal_clear_watchdog_removed_the_production_log(stack: Stack, cell: Cell) -> None:
    # The claim: the `clear-watchdog` rule empties `watchdog` - which, unlike
    # the other cells' truncated tables, is not empty afterwards: the setup step
    # runs after the sanitize step and one of its commands logs a row of its
    # own ("Maintenance mode disabled."). So the check is that the row the seed
    # put there is gone, not that the table has no rows.
    if cell.profile != "drupal":
        pytest.skip("`watchdog` is Drupal's")
    seeded = stack.remote_sql("SELECT COUNT(*) FROM watchdog WHERE type = 'matrix'")
    assert seeded.returncode == 0, seeded.stderr
    assert seeded.stdout.strip() == "1", f"the seed put no row into watchdog: {seeded.stdout}"
    assert stack.sql("SELECT COUNT(*) FROM watchdog WHERE type = 'matrix'") == "0", (
        "the production log row survived: `clear-watchdog` did not take effect"
    )


def test_the_drupal_admin_holds_the_administrator_role(stack: Stack, cell: Cell) -> None:
    # The claim: the `local-admin` rule produces an administrator, not just a
    # login. `drush user:create` has no role option, so the engine follows it
    # with `user:role:add`; the evidence is the row Drupal keeps for a user's
    # roles (`user__roles`: `entity_id` is the uid, `roles_target_id` the role's
    # machine name - both read off this installation), because a command that
    # ran proves nothing about the account.
    if cell.profile != "drupal":
        pytest.skip("`user:role:add` is drush's")
    assert cell.admin_user is not None
    roles = (
        "SELECT r.roles_target_id FROM user__roles r "
        "JOIN users_field_data u ON u.uid = r.entity_id "
        f"WHERE u.name = '{cell.admin_user}'"
    )
    first = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert first.returncode == 0, f"stdout:\n{first.stdout}\n\nstderr:\n{first.stderr}"
    assert stack.sql(roles) == "administrator", "the account was created without the role"
    # The path every later sync takes: the account exists, `user:create` is
    # refused, and the role must still be (re)applied. Taking it away first, by
    # drush rather than SQL so Drupal's entity caches agree, is what makes the
    # row below evidence of this run and not a leftover of the first.
    removed = stack.web(["vendor/bin/drush", "user:role:remove", "administrator", cell.admin_user])
    assert removed.returncode == 0, f"stdout:\n{removed.stdout}\n\nstderr:\n{removed.stderr}"
    assert stack.sql(roles) == "", "the role could not be taken away"
    again = stack.engine("--target", "production", "--steps", "sanitize", "--yes")
    assert again.returncode == 0, f"stdout:\n{again.stdout}\n\nstderr:\n{again.stderr}"
    assert "already exists" in again.stdout, again.stdout
    assert stack.sql(roles) == "administrator", "an existing account was not given the role again"
