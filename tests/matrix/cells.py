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
    """

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


# `name` is both the directory under `cells/` and the pytest parameter id, so
# a failure names the variant directly.
CELLS: tuple[Cell, ...] = (
    Cell(
        name="laravel",
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
    ),
)
