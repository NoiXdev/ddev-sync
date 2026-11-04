"""Which names `env.pull` may bring along from the remote.

Read by the planner and by preflight, so a name that may not come along is
refused before anything has moved. The names are not secrets; the values they
stand for are, and nothing here ever sees one.
"""

import re
from collections.abc import Mapping
from typing import Any

from ddev_sync.errors import ConfigError

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The endpoint's own namespace: it never hands these over, so listing one can
# only ever end in a refusal further down the line.
_RESERVED_PREFIX = "DDEV_SYNC_"

# The fields `database.env_map` can rename, as `operations/database.py` reads
# them. Spelled out rather than imported: nothing in `config` depends on
# `operations`, which depends on it.
_DATABASE_FIELDS = ("name", "user", "password", "host", "port")
_DATABASE_URLS = ("DB_URL", "DATABASE_URL")


def pull_names(data: Mapping[str, Any]) -> list[str]:
    """The names `env.pull` lists in the merged configuration, deduplicated, in order.

    `env.set` keeps a profile's safeguards in place only for the keys it names,
    which `generic` has none of, so the names that say where the database is are
    refused outright and whatever the profile: pulled, `DB_HOST` would point a
    laptop at production. A bare `pull:` is YAML null, which is nothing written
    rather than something malformed, like a bare `default:` in the planner.
    """
    env = data.get("env")
    raw = env.get("pull") if isinstance(env, Mapping) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"env.pull must be a list of variable names, got {raw!r}")
    database_names = _database_names(data)
    for name in raw:
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise ConfigError(f"env.pull must list variable names, got {name!r}")
        if name.startswith(_RESERVED_PREFIX):
            raise ConfigError(
                f"env.pull cannot name {name}: {_RESERVED_PREFIX}* is never handed over"
            )
        if name in database_names:
            raise ConfigError(
                f"env.pull cannot name {name}: it says where the database is, and pulled "
                "into the local file it would point this project at production"
            )
    return list(dict.fromkeys(raw))


def _database_names(data: Mapping[str, Any]) -> set[str]:
    database = data.get("database")
    env_map = database.get("env_map") if isinstance(database, Mapping) else None
    mapped = env_map if isinstance(env_map, Mapping) else {}
    names = {
        f"DATABASE_{field.upper()}" if mapped.get(field) is None else str(mapped[field])
        for field in _DATABASE_FIELDS
    }
    return names.union(_DATABASE_URLS)
