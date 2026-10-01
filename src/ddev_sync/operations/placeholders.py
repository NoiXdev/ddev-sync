"""Expand the `${...}` placeholders a profile may use in configured values.

`env.py` re-exports `expand_placeholders` for backward compatibility, but the
substitution logic itself lives here exactly once, so a sixth place that
reads a profile value cannot forget to expand it. This module is no longer
"neutral" in the sense of depending on nothing but `ExecutionContext`:
`${WP_TABLE_PREFIX}` needs `operations/database.py`'s `read_remote_credentials`
(see below), which is a one-directional dependency - `database.py` depends
on neither this module nor `sanitize.py`, so no cycle results.

Every placeholder is a thunk, not a precomputed string, and resolving one
only calls its thunk when a value being expanded actually contains the
literal substring. `${DDEV_SITENAME}` and `${SYNC_TARGET_DATABASE}` are
unaffected: reading `context.project.name` or a config default is cheap
enough that wrapping it in a thunk changes nothing observable, but keeps
every placeholder in `_substitutions` the same shape.
"""

import re
from collections.abc import Callable, Mapping
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import read_remote_credentials

WP_TABLE_PREFIX_CONFIG_KEY = "database.wp_table_prefix"
SYMFONY_USER_TABLE_CONFIG_KEY = "database.symfony_user_table"

# `${WP_TABLE_PREFIX}` and `${SYMFONY_USER_TABLE}` are spliced directly into
# *unquoted* raw SQL (see profiles/wordpress.yaml's and profiles/symfony.yaml's
# `sql:` rules, e.g. `"UPDATE ${WP_TABLE_PREFIX}users SET ..."`) - there are no
# surrounding backticks here for `operations/catalog.py::_identifier`'s style
# of escaping to apply to. Validation, not escaping, is the right guard: a
# table prefix or user-table name containing a backtick, quote, space or
# semicolon is not a value anyone intends to use - it is a mistake, in config
# or (for the WordPress prefix) in production's wp-config.php - so it is
# rejected once, here, at the single place both placeholders resolve, rather
# than risked as a baffling SQL error or worse at the point the statement
# finally runs.
_SAFE_SQL_IDENTIFIER = re.compile(r"[A-Za-z0-9_]*")


def _validated_identifier(value: str, *, source: str) -> str:
    """Reject `value` unless it is safe to splice into unquoted SQL."""
    if _SAFE_SQL_IDENTIFIER.fullmatch(value) is None:
        raise OperationError(
            f"{source} resolved to {value!r}, which is not a safe SQL identifier "
            "(only letters, digits and underscores are allowed); refusing to "
            "use it in a sanitize rule's SQL"
        )
    return value


def _validated_docroot(docroot: str) -> str:
    """Reject a docroot that could escape the project root once spliced into
    a shell command.

    `DdevProject.docroot` is read verbatim from `.ddev/config.yaml` and never
    normalised (`ddev/project.py::read_ddev_project`). ${DDEV_DOCROOT} ends
    up directly in a `run:` string (features/typo3-processedfiles.yaml's
    `rm -rf`) - unlike `files.paths`, nothing downstream runs a `run:` string
    through `context.local()`'s containment check, so a docroot of e.g.
    "../../etc" would otherwise turn that command into
    `rm -rf ../../etc/fileadmin/_processed_`, run with cwd at the project
    root. Rejecting here, where the placeholder resolves, is the one place
    that can still catch it.
    """
    if docroot.startswith("/") or ".." in docroot:
        raise OperationError(
            f"docroot {docroot!r} (from .ddev/config.yaml) is not safe to use "
            "in a shell command - it must be a relative path with no '..' segment"
        )
    return docroot


def _cached(context: ExecutionContext, key: str, resolve: Callable[[], str]) -> str:
    """Run `resolve` at most once per `context`, keyed by `key`.

    `context.cache` lives as long as `context` does - one whole sync run -
    so this is what turns "called once per rule" into "run once per run",
    however many rules or expansions ask for the same placeholder.
    """
    cache = context.cache
    if key not in cache:
        cache[key] = resolve()
    return str(cache[key])


def _resolve_wp_table_prefix(context: ExecutionContext) -> str:
    """Resolve WordPress's table prefix against *production*, not the local
    checkout.

    The local wp-config.php is the wrong source even when one exists: a
    WordPress repo conventionally gitignores wp-config.php, and DDEV
    regenerates one with `$table_prefix = 'wp_'` - reading it locally would
    silently report the DDEV default instead of production's real prefix on
    exactly the hardened installs this placeholder exists for. Sanitize runs
    against the imported dump, so the prefix that matters is production's,
    read over the same ssh channel `read_remote_credentials` already uses
    for a `wp_cli` source (see `operations/database.py::_from_wp_cli`) -
    memoised there, so this costs nothing extra beyond what db.dump and the
    Part 3 preflight probe already pay for in the same run.
    """
    configured = context.config(WP_TABLE_PREFIX_CONFIG_KEY, default="")
    if configured:
        return _validated_identifier(configured, source=WP_TABLE_PREFIX_CONFIG_KEY)
    credentials = read_remote_credentials(context)
    if credentials.table_prefix:
        return _validated_identifier(
            credentials.table_prefix, source="production's wp-config.php table_prefix"
        )
    source = context.config("database.remote_credentials", default="env")
    raise OperationError(
        f"could not resolve ${{WP_TABLE_PREFIX}}: 'database.remote_credentials' is "
        f"{source!r}, which has no table prefix to read - only a 'wp_cli' source "
        f"reads production's wp-config.php for it. Set '{WP_TABLE_PREFIX_CONFIG_KEY}' "
        "in .ddev/ddev-sync.yaml to override."
    )


def _substitutions(context: ExecutionContext) -> dict[str, Callable[[], str]]:
    return {
        "${DDEV_SITENAME}": lambda: context.project.name,
        # `DdevProject.docroot` is "" when `.ddev/config.yaml` sets none - the
        # project root itself is then the web root. A feature or profile
        # writing "${DDEV_DOCROOT}/fileadmin" needs that case to produce a
        # relative "./fileadmin", not an absolute "/fileadmin": the latter
        # would point at the filesystem root instead of the project, in the
        # one kind of setup command (`rm -rf`, see
        # features/typo3-processedfiles.yaml) where that distinction is
        # destructive rather than merely wrong.
        "${DDEV_DOCROOT}": lambda: _validated_docroot(context.project.docroot) or ".",
        "${SYNC_TARGET_DATABASE}": lambda: context.config("database.target_database", default="db"),
        "${WP_TABLE_PREFIX}": lambda: _cached(
            context, "${WP_TABLE_PREFIX}", lambda: _resolve_wp_table_prefix(context)
        ),
        # Symfony has no component that creates a `user` table - it is
        # MakerBundle's conventional default, not a guarantee - so a project
        # using `users` or `app_user` instead needs a way to say so without
        # editing profiles/symfony.yaml. A plain config-backed placeholder is
        # enough here: unlike ${WP_TABLE_PREFIX} there is nothing to shell
        # out to, just a default to read, so it needs neither a thunk that
        # does real work nor the memoisation that exists to bound that work.
        "${SYMFONY_USER_TABLE}": lambda: _validated_identifier(
            context.config(SYMFONY_USER_TABLE_CONFIG_KEY, default="user"),
            source=SYMFONY_USER_TABLE_CONFIG_KEY,
        ),
    }


def expand_value(value: Any, context: ExecutionContext) -> Any:
    """Recursively substitute placeholders in strings, lists and mappings.

    Non-string, non-list, non-dict values (bools, ints, `None`, ...) pass
    through unchanged - a sanitize rule's `extra: 7` must stay the integer
    `7`, not become the string `"7"`.
    """
    return _expand(value, _substitutions(context))


def _expand(value: Any, substitutions: Mapping[str, Callable[[], str]]) -> Any:
    if isinstance(value, str):
        text = value
        for placeholder, resolve in substitutions.items():
            if placeholder in text:
                text = text.replace(placeholder, resolve())
        return text
    if isinstance(value, list):
        return [_expand(item, substitutions) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, substitutions) for key, item in value.items()}
    return value


def expand_placeholders(values: Mapping[str, str], context: ExecutionContext) -> dict[str, str]:
    """Substitute the placeholders a profile may use in a flat string mapping.

    Kept alongside the more general `expand_value` because `env.set` is
    always a flat `str -> str` mapping and callers (and their tests) read
    naturally as "expand this mapping", not "expand this arbitrary value".
    """
    substitutions = _substitutions(context)
    return {key: str(_expand(str(value), substitutions)) for key, value in values.items()}
