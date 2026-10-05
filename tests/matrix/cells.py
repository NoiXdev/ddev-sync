"""The variants this matrix verifies, and what "verified" means for each.

One registry, so the workflow, the tests and `VERIFIED.md` cannot disagree
about what is covered. Everything a cell asserts is declared here rather than
written into a test body: adding a cell is then a registry entry plus three
recipe files, not a new test.
"""

from dataclasses import dataclass


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
        # `mxwp_` rather than WordPress's default `wp_`, and the cell's config
        # sets `database.wp_table_prefix` to match - see that file for why the
        # prefix is configured rather than read from production's wp-config.php.
        required_tables=("mxwp_users",),
        admin_user="ddev",
        password_column=("mxwp_users", "user_pass"),
        production_url="https://acme-production.example",
        # No `truncated_tables`: the profile has no truncate rule.
        # Not `user_email`: the profile's sanitize rules rewrite it.
        identity_column="user_login",
        seeded_users=("alice", "bruno"),
    ),
)
