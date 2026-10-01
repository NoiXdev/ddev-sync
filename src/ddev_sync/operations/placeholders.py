"""Expand the `${...}` placeholders a profile may use in configured values.

Neutral module: it needs only `ExecutionContext`, nothing else from `env.py`
or `sanitize.py`. That is what lets both of them depend on it without either
depending on the other - `env.py` re-exports `expand_placeholders` for
backward compatibility, but the substitution logic itself lives here exactly
once, so a sixth place that reads a profile value cannot forget to expand it.

Every placeholder is a thunk, not a precomputed string: `${WP_TABLE_PREFIX}`
shells out to `wp config get table_prefix` when no override is configured,
and that must not run once per rule, per profile, on every sync regardless of
whether anything ever asked for it - only when a value being expanded
actually contains the literal substring. `${DDEV_SITENAME}` and
`${SYNC_TARGET_DATABASE}` are unaffected: reading `context.project.name` or a
config default is cheap enough that wrapping it in a thunk changes nothing
observable, but keeps every placeholder in `_substitutions` the same shape.
"""

from collections.abc import Callable, Mapping
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError

WP_TABLE_PREFIX_CONFIG_KEY = "database.wp_table_prefix"
SYMFONY_USER_TABLE_CONFIG_KEY = "database.symfony_user_table"

_WP_TABLE_PREFIX_COMMAND = ("wp", "config", "get", "table_prefix")


def _cached(context: ExecutionContext, key: str, resolve: Callable[[], str]) -> str:
    """Run `resolve` at most once per `context`, keyed by `key`.

    `context.placeholder_cache` lives as long as `context` does - one whole
    sync run - so this is what turns "called once per rule" into "run once
    per run", however many rules or expansions ask for the same placeholder.
    """
    cache = context.placeholder_cache
    if key not in cache:
        cache[key] = resolve()
    return cache[key]


def _resolve_wp_table_prefix(context: ExecutionContext) -> str:
    configured = context.config(WP_TABLE_PREFIX_CONFIG_KEY, default="")
    if configured:
        return configured
    command = list(_WP_TABLE_PREFIX_COMMAND)
    result = context.runner.run(command, cwd=context.project.docroot_path)
    value = result.stdout.strip()
    if result.ok and value:
        return value
    reason = (result.stderr.strip() or "no error output") if not result.ok else "it printed nothing"
    raise OperationError(
        f"could not resolve ${{WP_TABLE_PREFIX}}: '{' '.join(command)}' "
        f"(run in {context.project.docroot_path}) failed: {reason}. "
        f"Set '{WP_TABLE_PREFIX_CONFIG_KEY}' in .ddev/ddev-sync.yaml to override."
    )


def _substitutions(context: ExecutionContext) -> dict[str, Callable[[], str]]:
    return {
        "${DDEV_SITENAME}": lambda: context.project.name,
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
        "${SYMFONY_USER_TABLE}": lambda: context.config(
            SYMFONY_USER_TABLE_CONFIG_KEY, default="user"
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
