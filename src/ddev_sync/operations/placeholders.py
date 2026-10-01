"""Expand the `${...}` placeholders a profile may use in configured values.

Neutral module: it needs only `ExecutionContext`, nothing else from `env.py`
or `sanitize.py`. That is what lets both of them depend on it without either
depending on the other - `env.py` re-exports `expand_placeholders` for
backward compatibility, but the substitution logic itself lives here exactly
once, so a sixth place that reads a profile value cannot forget to expand it.
"""

from collections.abc import Mapping
from typing import Any

from ddev_sync.operations.base import ExecutionContext


def _substitutions(context: ExecutionContext) -> dict[str, str]:
    return {
        "${DDEV_SITENAME}": context.project.name,
        "${SYNC_TARGET_DATABASE}": context.config("database.target_database", default="db"),
    }


def expand_value(value: Any, context: ExecutionContext) -> Any:
    """Recursively substitute placeholders in strings, lists and mappings.

    Non-string, non-list, non-dict values (bools, ints, `None`, ...) pass
    through unchanged - a sanitize rule's `extra: 7` must stay the integer
    `7`, not become the string `"7"`.
    """
    return _expand(value, _substitutions(context))


def _expand(value: Any, substitutions: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        text = value
        for placeholder, replacement in substitutions.items():
            text = text.replace(placeholder, replacement)
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
