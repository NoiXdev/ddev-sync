"""Which names `env.pull` may bring along from the remote.

Read by the planner and by preflight, so a name that may not come along is
refused before anything has moved. The names are not secrets; the values they
stand for are, and nothing here ever sees one.
"""

import re
from collections.abc import Mapping, Sequence
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

# What a container's environment holds whoever configured it. Under
# `DDEV_SYNC_EXPORT_ENV: all` the endpoint hands these over too, and a laptop's
# `PATH` is not something `env.pull_all` means to replace.
_CONTAINER_NAMES = frozenset(
    {"HOME", "HOSTNAME", "LANG", "OLDPWD", "PATH", "PWD", "SHLVL", "TERM", "USER", "_"}
)


def pull_names(data: Mapping[str, Any]) -> list[str]:
    """The names to pull in the merged configuration, deduplicated, in order.

    `env.pull` as written, or the profile's `env.pull_defaults` when it is not
    written, or the two together under `env.extend: true`. So `pull: []` is how
    a project takes the defaults away.

    `env.set` keeps a profile's safeguards in place only for the keys it names,
    which `generic` has none of, so the names that say where the database is are
    refused outright and whatever the profile: pulled, `DB_HOST` would point a
    laptop at production. A bare `pull:` is YAML null, which is nothing written
    rather than something malformed, like a bare `default:` in the planner.
    """
    env = data.get("env")
    if not isinstance(env, Mapping):
        return []
    raw = env.get("pull")
    if raw == "all":
        raise ConfigError(
            "env.pull must be a list of variable names; to pull everything the remote "
            "has, set env.pull_all: true"
        )
    if raw is not None and not isinstance(raw, list):
        raise ConfigError(f"env.pull must be a list of variable names, not a {type(raw).__name__}")
    listed = [] if raw is None else raw
    if raw is None or _flag(env, "extend"):
        listed = [*_usable_defaults(env), *listed]
    database_names = _database_names(data)
    for position, name in enumerate(listed, start=1):
        # By position, never what it holds: an entry that is no name may be
        # `NAME=value`, with the secret itself pasted in.
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise ConfigError(f"env.pull must list variable names, and entry {position} is not one")
        if name.startswith(_RESERVED_PREFIX):
            raise ConfigError(
                f"env.pull cannot name {name}: {_RESERVED_PREFIX}* is never handed over"
            )
        if name in database_names:
            raise ConfigError(
                f"env.pull cannot name {name}: it says where the database is, and pulled "
                "into the local file it would point this project at production"
            )
    return list(dict.fromkeys(listed))


def optional_names(data: Mapping[str, Any]) -> set[str]:
    """The names a profile's defaults bring and the project did not list itself.

    The project never asked for these, so one the remote cannot give is a notice
    rather than a refused run. Otherwise a sync that worked would stop working
    the day a profile gained a default.
    """
    env = data["env"] if isinstance(data.get("env"), Mapping) else {}
    raw = env.get("pull")
    listed = {name for name in raw if isinstance(name, str)} if isinstance(raw, list) else set()
    return set(pull_names(data)) - listed


def pull_all(data: Mapping[str, Any]) -> bool:
    """Whether `env.pull_all` asks for every name the remote has."""
    env = data.get("env")
    return _flag(env, "pull_all") if isinstance(env, Mapping) else False


def pulls_anything(data: Mapping[str, Any]) -> bool:
    """Whether the env step has anything to bring from the remote."""
    return bool(pull_names(data)) or pull_all(data)


def may_pull_all(name: str, data: Mapping[str, Any]) -> bool:
    """Whether `env.pull_all` takes a name the remote has.

    What a list would be refused for is left out here instead, since nobody
    named it: the settings that say where the database is and the endpoint's own.
    So is what `env.set` keeps, which a list would only read for nothing.
    """
    imposed = data["env"].get("set")
    kept = {str(key) for key in imposed} if isinstance(imposed, Mapping) else set()
    return (
        _NAME.fullmatch(name) is not None
        and not name.startswith(_RESERVED_PREFIX)
        and name not in _CONTAINER_NAMES
        and name not in kept
        and name not in _database_names(data)
    )


def names_to_read(data: Mapping[str, Any]) -> list[str]:
    """`pull_names` without the names `env.set` keeps, whose remote value is never used.

    A non-mapping `set` is no block, as the planner and the env step read it.
    """
    names = pull_names(data)
    if not names:
        return []
    imposed = data["env"].get("set")
    kept = {str(key) for key in imposed} if isinstance(imposed, Mapping) else set()
    return [name for name in names if name not in kept]


def refuse_an_unusable_strategy(pulling: bool, strategy: str) -> None:
    """Neither other strategy can replace a whole right-hand side, and `none` writes nothing.

    Shared by the planner and `cli/init.py`, which runs before any plan does.
    """
    if pulling and strategy != "patch_keys":
        raise ConfigError(
            f"env.pull needs env.strategy: patch_keys, but the strategy is {strategy!r}"
        )


def refuse_unusable_files(pulling: bool, candidates: Sequence[str]) -> None:
    """Refuse `env.file` candidates the env step could not create a file for.

    With `env.pull` a missing file is created as the first candidate, and every
    run looks for the candidates among the entries of the project root. So with
    none there is nowhere to go, and a file created in another directory would
    not be found by the next run. Shared like `refuse_an_unusable_strategy`.
    """
    if not pulling:
        return
    if not candidates:
        raise ConfigError("env.pull needs a file to write to, but env.file lists none")
    for candidate in candidates:
        if "/" in candidate:
            raise ConfigError(
                f"env.pull needs its env.file candidates in the project root, got {candidate}"
            )


def _flag(env: Mapping[str, Any], key: str) -> bool:
    """A yes or no setting of the `env` block, absent or bare meaning no."""
    value = env.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(f"env.{key} must be true or false, not {value!r}")
    return value


def _usable_defaults(env: Mapping[str, Any]) -> list[Any]:
    """The profile's `env.pull_defaults`, if the project's env settings can take them.

    A project that chose another strategy, or an `env.file` a pull cannot use,
    did not ask for a pull, and a default must not refuse what worked before. It
    is dropped, unlike a list the project wrote itself.
    """
    raw = env.get("pull_defaults")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(
            f"env.pull_defaults must be a list of variable names, not a {type(raw).__name__}"
        )
    configured = env.get("file", [".env.local", ".env"])
    candidates = [configured] if isinstance(configured, str) else configured
    usable = (
        env.get("strategy", "patch_keys") == "patch_keys"
        and isinstance(candidates, list)
        and bool(candidates)
        and not any("/" in str(candidate) for candidate in candidates)
    )
    return raw if usable else []


def _database_names(data: Mapping[str, Any]) -> set[str]:
    database = data.get("database")
    env_map = database.get("env_map") if isinstance(database, Mapping) else None
    mapped = env_map if isinstance(env_map, Mapping) else {}
    names = {
        f"DATABASE_{field.upper()}" if mapped.get(field) is None else str(mapped[field])
        for field in _DATABASE_FIELDS
    }
    return names.union(_DATABASE_URLS)
