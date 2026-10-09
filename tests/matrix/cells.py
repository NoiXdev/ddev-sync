"""The variants this matrix verifies, and what "verified" means for each.

One registry, so the workflow, the tests and `VERIFIED.md` cannot disagree
about what is covered. Everything a cell asserts is declared here rather than
written into a test body: adding a cell is then a registry entry plus three
recipe files, not a new test.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FlushMarker:
    """A cache row a cell plants so its `flush_cache` rule can be seen working.

    The engine discards the result of a `flush_cache` command on purpose, so a
    command that does not exist never fails a sync and no exit status says it
    ran. The effect does: `insert` puts a row into a cache table that no other
    sanitize rule touches, `where` selects exactly that row in `table`, and the
    rule must have removed it once the sync is over.
    """

    table: str
    insert: str
    where: str


@dataclass(frozen=True)
class FeatureCheck:
    """What a cell proves about one feature.

    `feature` is the name `features/<name>.yaml` declares, and naming it is
    itself an assertion: the run must report that feature as active. A check
    with nothing else set therefore still proves something, which is exactly
    `inertia`'s case - it declares no commands at all.

    "Active", not "detected": `typo3-processedfiles` has no detect block by
    design (see the comment at the top of its file), so it is activated by a
    `features:` key instead. `cli/render.py::render_features` prints both
    kinds in one block, which is what the assertion reads.

    `appears` are paths that must exist after the sync and must have been
    removed by the install recipe before it - otherwise the assertion passes
    against a path the install left behind and proves nothing. `disappears`
    are paths that must be gone after the sync. `row_marker` reuses
    `FlushMarker`: a row the cell plants which the sync must have removed.
    """

    feature: str
    appears: tuple[str, ...] = ()
    disappears: tuple[str, ...] = ()
    row_marker: FlushMarker | None = None


@dataclass(frozen=True)
class Cell:
    """One profile variant, installed for real and synced end to end.

    `required_tables` lists only the tables the profile's **non-tolerated**
    `requires_table` rules declare. A rule carrying `tolerate_missing_table`
    is deliberately absent: asserting it would turn an intended tolerance into
    a false failure.

    `truncated_tables` lists the tables a profile's truncate rules empty,
    **including** the ones marked `tolerate_missing_table`: the tolerance only
    decides what happens when the table is absent, and a cell whose install
    creates the table must still see it emptied. Defaulted so a profile with
    no such rules states nothing.

    `password_column` is the (table, column) a `reset_passwords` rule
    rewrites, `admin_user` the login a `create_admin` rule creates, and
    `production_url` the URL a sanitize rule must remove. `None` means the
    profile has no such rule - which is itself part of what the cell states.

    `identity_column` and `seeded_users` say which rows of the password
    table the cell's seed put there: the values of a column no sanitize rule
    rewrites (a login name, not an email address). They exist because
    `create_admin` runs after `reset_passwords` and inserts a row whose hash
    is genuinely not the reset value, so "every row was reset" cannot be
    asserted - "every *seeded* row was reset" can, and still fails when the
    reset never ran. The admin is found through the same column.

    `flush_marker` is the row the cell's `flush_cache` rule must remove. `None`
    means the profile has no such rule, or that nothing observable lives in the
    database for it to clear.

    `remote_packages` is what the stand-in production host installs at start
    (`|`-separated, the format of the sshd image's `INSTALL_PACKAGES`). The
    default is what every sync needs - a mysqldump client and rsync. A profile
    whose commands run on production itself needs more: WordPress's `wp_cli`
    credentials source runs `wp` over ssh, so its cell adds PHP. It is a field,
    not a shared default, because the packages are installed from the network
    at every start and the other cells should not pay for them.

    `features` is what this cell proves about the *features* the sync
    activated, as opposed to the profile - see `FeatureCheck`. Defaulted, so
    the six profile cells state nothing about features and keep asserting
    exactly what they asserted before this field existed.
    """

    # `mx` + letters and digits only, for three independent reasons:
    # - `name` becomes the DDEV project name in `.ddev/config.yaml`, from which
    #   the engine derives `ddev-<name>-web`; a plain `laravel` would claim
    #   the container name of a developer's real project, and the engine's
    #   `docker exec` could then run `composer`/`artisan` in *their* project.
    #   The prefix keeps the cell out of that namespace.
    # - no hyphen: `pytest -k typo3-13` selects nothing, because `-k` parses
    #   its argument as an expression and the hyphen is not a substring match.
    # - no underscore: the name ends up in the network alias `ddev-<name>-db`,
    #   which the engine passes to the mysql client as a hostname.
    #
    # One further constraint, on the set of names rather than on the charset:
    # no name may contain another cell's name. `.github/workflows/matrix.yaml`
    # runs one job per cell with `-k "$CELL"`, and `-k` matches a substring of
    # the test id - `-k mxtypo312` collects one cell's 25 tests while
    # `-k mxtypo31` collects both TYPO3 cells' 50. A feature cell called
    # `mxlaravelfeat` would therefore have been swept into the `mxlaravel`
    # job, which emits the stock-skeleton `laravel` row; hence `mxfeat*`.
    name: str
    profile: str
    project_type: str
    docroot: str
    php_image: str
    required_tables: tuple[str, ...]
    admin_user: str | None
    password_column: tuple[str, str] | None
    production_url: str | None
    truncated_tables: tuple[str, ...] = ()
    identity_column: str | None = None
    seeded_users: tuple[str, ...] = ()
    flush_marker: FlushMarker | None = None
    remote_packages: str = "mariadb-client|rsync"
    features: tuple[FeatureCheck, ...] = ()


# Both TYPO3 cells keep the same cache tables, so they plant the same row.
TYPO3_FLUSH_MARKER = FlushMarker(
    table="cache_hash",
    # `expires` far in the future, so an expiry sweep cannot be what removes the
    # row; only `cache:flush` does.
    insert=(
        "INSERT INTO cache_hash (identifier, expires, content) "
        "VALUES ('matrix_marker', 2000000000, 'present')"
    ),
    where="identifier = 'matrix_marker'",
)

# `name` is both the directory under `cells/` and the pytest parameter id, so
# a failure names the variant directly.
CELLS: tuple[Cell, ...] = (
    Cell(
        name="mxlaravel",
        profile="laravel",
        project_type="laravel",
        docroot="public",
        # PHP 8.3.35 was printed by `php -v` in this image, and the newest
        # laravel/laravel release (v13.10.1) declares `php ^8.3`.
        php_image="php:8.3-cli",
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        # Not `email`: the profile's sanitize rules rewrite it.
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
    ),
    Cell(
        name="mxfeatlaravel",
        # A second laravel cell, not a change to `mxlaravel`: that cell's row
        # says "against a fresh laravel/laravel skeleton, profile unchanged",
        # and installing Filament and Livewire into it would make the sentence
        # false to save three minutes of runtime.
        profile="laravel",
        project_type="laravel",
        docroot="public",
        php_image="php:8.3-cli",
        # The profile-shaped fields are `mxlaravel`'s, verbatim: `Cell`'s
        # docstring defines `None`/`()` as "the profile has no such rule", so
        # emptying them here would make that meaning false and print a lying
        # skip reason. The duplicated profile evidence costs nothing (the same
        # sync runs either way) and is not what this cell exists to record:
        # the rows it is here to fill are VERIFIED.md's *feature* rows. Note
        # that `tools/apply_verified_rows.py` still keys every emitted row by
        # `profile`, so `--emit mxfeatlaravel` yields a `laravel` row today -
        # applying one would overwrite `mxlaravel`'s evidence with this cell's.
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
        features=(
            FeatureCheck(
                feature="node-build",
                # The directory, not a manifest path: where inside it the
                # manifest lands is the toolchain's choice, so a pinned
                # filename would break on a version bump without telling
                # anyone anything true. This build wrote
                # `public/build/manifest.json` and no `public/build/.vite/`
                # at all (vite 8.3.4, laravel-vite-plugin 3.2.0).
                appears=("public/build",),
            ),
            FeatureCheck(
                feature="filament",
                # `filament:optimize`'s two steps, "Caching components" and
                # "Caching Blade icons", and the two paths that appear -
                # measured 2026-10-08 against filament/filament v5.10.1.
                # `filament:upgrade` is deliberately absent: it reported
                # success and left no new path, but the probe had not removed
                # its likely targets under `public/`, so their absence proves
                # nothing. See the VERIFIED.md row.
                appears=(
                    "bootstrap/cache/filament/panels",
                    "bootstrap/cache/blade-icons.php",
                ),
            ),
            FeatureCheck(
                feature="livewire",
                # `livewire:publish --assets` copies
                # vendor/livewire/livewire/dist into here - eleven files,
                # the whole of that directory, measured against
                # livewire/livewire v4.4.7.
                appears=("public/vendor/livewire",),
            ),
            # Detection only. The feature declares no commands at all
            # ("Detected for reporting; the build itself is node-build's
            # job"), so that it is reported is the whole of its claim.
            FeatureCheck(feature="inertia"),
        ),
    ),
    Cell(
        name="mxtypo313",
        profile="typo3@13",
        project_type="typo3",
        docroot="public",
        php_image="php:8.3-cli",
        # The three non-tolerated requires_table rules in profiles/typo3.yaml.
        # `tx_scheduler_task` and the fe_* tables are deliberately absent:
        # they carry tolerate_missing_table because EXT:scheduler and
        # EXT:frontend are optional, and asserting them here would turn an
        # intended tolerance into a false failure.
        required_tables=("be_sessions", "sys_log", "be_users"),
        admin_user="ddev",
        password_column=("be_users", "password"),
        production_url=None,
        # `tx_scheduler_task` is not listed: the base distribution does not
        # install EXT:scheduler, so the table does not exist here. `fe_sessions`
        # does (EXT:frontend ships in the distribution) and must be emptied.
        truncated_tables=("be_sessions", "sys_log", "fe_sessions"),
        identity_column="username",
        seeded_users=("editor.hofer", "editor.kalb"),
        flush_marker=TYPO3_FLUSH_MARKER,
    ),
    Cell(
        name="mxtypo312",
        profile="typo3@12",
        project_type="typo3",
        docroot="public",
        php_image="php:8.2-cli",
        required_tables=("be_sessions", "sys_log", "be_users"),
        admin_user="ddev",
        password_column=("be_users", "password"),
        production_url=None,
        truncated_tables=("be_sessions", "sys_log", "fe_sessions"),
        identity_column="username",
        seeded_users=("editor.hofer", "editor.kalb"),
        flush_marker=TYPO3_FLUSH_MARKER,
    ),
    Cell(
        name="mxsymfony",
        profile="symfony",
        project_type="symfony",
        docroot="public",
        php_image="php:8.3-cli",
        # The resolved name, not `${SYMFONY_USER_TABLE}`: the cell's config
        # sets `database.symfony_user_table` to the table the install really
        # creates, and the assertion proves resolution agreed with it.
        required_tables=("app_user",),
        admin_user=None,
        password_column=("app_user", "password"),
        production_url=None,
        # No `truncated_tables`: the profile's only truncate rule empties
        # `messenger_messages`, which a stock `symfony/orm-pack` installation
        # does not have - Doctrine Messenger creates it lazily on first use,
        # and Messenger is not even among the pack's packages. So the rule's
        # `tolerate_missing_table` is what this cell exercises, and the table
        # cannot be asserted empty.
        # Not `email`: the profile's sanitize rules rewrite it. `id` is
        # preserved by the import, and a MakerBundle-shaped user table has no
        # login column to use instead.
        identity_column="id",
        seeded_users=("1", "2"),
    ),
    Cell(
        name="mxwordpress",
        profile="wordpress",
        project_type="wordpress",
        # DDEV writes `docroot: ""` for WordPress: the project root is the web
        # root, so wp-cli runs there.
        docroot="",
        php_image="php:8.3-cli",
        # The resolved name, not `${WP_TABLE_PREFIX}`. The install chooses
        # `mxwp_` rather than WordPress's default `wp_`; the engine reads that
        # prefix from production's wp-config.php (cells/mxwordpress/remote-only/)
        # over ssh, because the cell's config keeps the profile's `wp_cli`
        # credentials source and sets no `database.wp_table_prefix`.
        required_tables=("mxwp_users",),
        admin_user="ddev",
        password_column=("mxwp_users", "user_pass"),
        production_url="https://acme-production.example",
        # No `truncated_tables`: the profile has no truncate rule.
        # Not `user_email`: the profile's sanitize rules rewrite it.
        identity_column="user_login",
        seeded_users=("alice", "bruno"),
        # `wp config get` runs on production itself, so that host needs PHP; the
        # init script in `remote-init/` adds wp-cli on top of it.
        remote_packages="mariadb-client|rsync|php83|php83-phar|php83-mbstring|php83-openssl|php83-iconv|php83-ctype",
    ),
    Cell(
        name="mxdrupal",
        profile="drupal",
        project_type="drupal",
        docroot="web",
        php_image="php:8.3-cli",
        required_tables=("users_field_data",),
        admin_user="ddev",
        password_column=("users_field_data", "pass"),
        production_url=None,
        # No `truncated_tables`, although the profile has two truncate rules:
        # `sessions` does not exist after a stock Drupal 11.4.8 install (observed
        # with SHOW TABLES), so `clear-sessions` runs on its
        # `tolerate_missing_table`; and `watchdog` cannot be asserted empty,
        # because a setup command, which runs after the sanitize step, writes a
        # log row of its own ("Maintenance mode disabled."). Its rule is checked by
        # `test_drupal_clear_watchdog_removed_the_production_log` instead.
        # Not `mail`: the profile's sanitize rules rewrite it.
        identity_column="name",
        seeded_users=("alice", "bruno"),
        flush_marker=FlushMarker(
            table="cache_default",
            # `expire` -1 is Drupal's CACHE_PERMANENT, so only a flush removes it.
            insert=(
                "INSERT INTO cache_default "
                "(cid, data, expire, created, serialized, tags, checksum) "
                "VALUES ('matrix_marker', 'present', -1, 0, 0, '', 0)"
            ),
            where="cid = 'matrix_marker'",
        ),
    ),
)
