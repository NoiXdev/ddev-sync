"""Collect target definitions across layers and resolve them into complete targets."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ddev_sync.config.deployer import DeployerConnection
from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import (
    DEPLOYER_OWNED_KEYS,
    ENDPOINT_HOST,
    ENDPOINT_USER,
    Layout,
    Provider,
    ResolvedTarget,
    TargetSpec,
)
from ddev_sync.errors import ConfigError, TargetError

MAX_EXTENDS_DEPTH = 16

# Three alternative ways to say "where to connect". The order is the tie-break
# inside a single spec: an `ssh_alias` beats a `host` written beside it, which
# is what `_build` has always done with the pair.
_CONNECTION_SOURCES = ("host", "ssh_alias", "deployer")

# What a `deployer:` block supplies besides the host itself - derived, not
# written out again. A key added to `DEPLOYER_OWNED_KEYS` alone used to be
# dropped by `_flatten`'s `pop` loop while the contradiction check above it
# did not know to look for it, so a stronger literal of that key was
# silently discarded instead of rejected - the one outcome the strictness
# decision exists to prevent. The filter is `_CONNECTION_SOURCES` because
# "detail" means exactly "owned, but not itself a way of saying where to
# connect".
_DEPLOYER_DETAIL_KEYS = tuple(key for key in DEPLOYER_OWNED_KEYS if key not in _CONNECTION_SOURCES)

DeployerResolver = Callable[[str | None], DeployerConnection]


@dataclass(frozen=True)
class CollectedTarget:
    """A target definition merged across layers, plus where it last changed."""

    spec: TargetSpec
    source: str


def collect_targets(layers: Sequence[LayerDoc]) -> dict[str, CollectedTarget]:
    """Merge every layer's `targets:` block and note the strongest contributor."""
    # Before the merge, not after it: `merge_layers` builds its own error
    # messages with `".".join(path)`, and a key that is not a string makes
    # that raise a bare `TypeError` - the very thing `_validated_name` exists
    # to replace - before anything here gets to look at the key. Measured: a
    # target named `yes:` with a malformed `values:` under it gave
    # "sequence item 1: expected str instance, bool found".
    #
    # Only the keys of a `targets:` mapping, and only the top level of it. A
    # layer whose `targets:` is not a mapping at all is left exactly as it
    # was, reported from the merged document below, so that a weak layer's
    # broken block can still be overridden by a stronger layer rather than
    # failing the run here. A non-string key deeper in the document - under a
    # target's `setup:`, or anywhere outside `targets:` - is not this loop's
    # business and never was: `config/merge.py::_display` renders whatever it
    # is given, which is what keeps that case an error message too.
    for entry in layers:
        own = entry.data.get("targets")
        if isinstance(own, dict):
            for raw_name in own:
                _validated_name(raw_name)
    merged = merge_layers(
        [LayerDoc(entry.label, {"targets": entry.data.get("targets", {})}) for entry in layers]
    )
    raw_targets = _as_mapping(merged.data.get("targets"), "targets")
    collected: dict[str, CollectedTarget] = {}
    for raw_name, raw in raw_targets.items():
        name = _validated_name(raw_name)
        collected[name] = CollectedTarget(
            spec=_parse_spec(name, raw),
            source=_strongest_source(name, raw, merged.scalar_sources, layers),
        )
    return collected


def list_target_names(collected: Mapping[str, CollectedTarget]) -> list[str]:
    """Target names in a stable order, for menus and `ddev sync targets`."""
    return sorted(collected)


# The two kinds worth naming, in the words the message uses; everything else
# YAML 1.1 can produce for a bare key - a date, a timestamp, binary - falls to
# the general wording below, because naming each would be a list that goes
# stale rather than help. `bool` before `int` matters only to a reader: the
# lookup is by exact type, so `True` cannot fall through to the `int` entry
# the way `isinstance` would let it.
_NAME_KINDS: dict[type, str] = {
    bool: "a boolean",
    int: "a number",
    float: "a number",
}


def _validated_name(name: Any) -> str:
    """Refuse a target name YAML did not read as text.

    `targets:` keys come straight from the user's file, and YAML 1.1 reads
    `yes:` as the boolean true, `2026:` as a number and `2026-10-07:` as a
    date. Such a key is never the text that was written, so `--target yes`
    cannot match it and `ddev sync targets` lists `True`; with one ordinary
    target beside it, sorting the names raised a bare `TypeError` comparing a
    bool with a str, part-way through a command. `config/writer.py` gets the
    same guard for free, because its pre-write load goes through here: such a
    file is now refused by name instead of reporting that writing one target
    "would also have changed True".

    Called twice, and only the first call can raise. `collect_targets` checks
    each layer's own keys before merging them, which is what makes the refusal
    earlier than every consumer; the call after the merge sees keys that were
    all validated on the way in. It stays because it is what narrows the
    merged mapping's `Any` key to a `str` for the rest of the function, and
    because a future caller that merges by some other route would otherwise
    have no check at all.
    """
    if isinstance(name, str):
        return name
    # An empty key is its own thing: there is no name to quote, so the fix is
    # not the one every other case gets.
    if name is None:
        raise ConfigError(
            "a target under `targets:` has no name at all - an empty key, and `~`, `null`, "
            "`Null` and `NULL`, are all null to YAML, not a name. Give the target a name."
        )
    kind = _NAME_KINDS.get(type(name), "something other than text")
    # The example is fixed rather than built from `name`, because what YAML
    # produced is not the spelling that produced it: `0x10:`, `1_000:`, `017:`
    # and `12:30:00:` are 16, 1000, 15 and 45000, and an earlier version of
    # this message told the user to write `'16':` - which would have renamed
    # their target. `True` loses even more: it could have been any of yes, no,
    # on, off, true or false. So the message says what YAML made of the key,
    # and leaves the spelling where the user can see it, in their own file.
    hint = (
        " - unquoted yes, no, on, off, true and false are all booleans to YAML"
        if isinstance(name, bool)
        else ""
    )
    raise ConfigError(
        f"a target name must be text, and YAML read one of them as {kind}, giving "
        f"{name}{hint}. Quote the name where it is written - `'production':`, with the "
        "quotes - so that YAML keeps it as text."
    )


def _as_mapping(raw: Any, label: str) -> dict[str, Any]:
    """An empty `targets:` block is a legitimate "nothing defined yet" - treat
    it as such rather than crashing; anything else that is not a mapping is a
    genuine configuration mistake and is named explicitly.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{label!r} must be a mapping of target definitions")
    return raw


def _parse_spec(name: str, raw: Any) -> TargetSpec:
    if not isinstance(raw, dict):
        raise TargetError(f"target {name!r}: must be a mapping of settings")
    try:
        return TargetSpec(**raw)
    except ValidationError as error:
        raise TargetError(f"target {name!r}: {error}") from error


def _strongest_source(
    name: str,
    raw: dict[str, Any],
    scalar_sources: Mapping[tuple[str, ...], str],
    layers: Sequence[LayerDoc],
) -> str:
    order = {entry.label: index for index, entry in enumerate(layers)}
    labels = [
        scalar_sources[("targets", name, key)]
        for key in raw
        if ("targets", name, key) in scalar_sources
    ]
    if not labels:
        return layers[-1].label
    return max(labels, key=lambda label: order[label])


def resolve_target(
    name: str,
    collected: Mapping[str, CollectedTarget],
    *,
    resolve_deployer: DeployerResolver | None = None,
) -> ResolvedTarget:
    """Flatten a target's `extends` chain and validate that it is complete.

    `resolve_deployer` turns a `deployer:` block's host alias into a
    connection. It is only called for a target whose chain ends up owned by
    such a block.
    """
    chain = _extends_chain(name, collected)
    flattened = _flatten(name, chain)
    return _build(name, flattened, source=collected[name].source, resolve_deployer=resolve_deployer)


def _extends_chain(name: str, collected: Mapping[str, CollectedTarget]) -> list[TargetSpec]:
    if name not in collected:
        known = ", ".join(sorted(collected)) or "none"
        raise TargetError(f"unknown target {name!r}; defined targets: {known}")

    chain: list[TargetSpec] = []
    seen: list[str] = []
    current: str | None = name
    while current is not None:
        if current in seen:
            cycle = " -> ".join([*seen, current])
            raise TargetError(f"target {name!r}: circular extends chain: {cycle}")
        if current not in collected:
            raise TargetError(
                f"target {seen[-1]!r} extends {current!r}, which is not defined anywhere"
            )
        seen.append(current)
        if len(seen) > MAX_EXTENDS_DEPTH:
            raise TargetError(
                f"target {name!r}: extends chain deeper than {MAX_EXTENDS_DEPTH} levels"
            )
        spec = collected[current].spec
        chain.append(spec)
        current = spec.extends
    chain.reverse()
    return chain


def _flatten(name: str, chain: Sequence[TargetSpec]) -> dict[str, Any]:
    # `exclude_unset` (not `exclude_defaults`): a child that explicitly writes
    # `port: 22` must still override a base's `port: 2222`, and `22` happens
    # to equal the field's default. `exclude_defaults` cannot tell "the user
    # wrote this default value" from "the user never mentioned this field",
    # so it silently drops the former too - an explicit default could never
    # win. `exclude_unset` tracks which fields pydantic actually saw on the
    # constructor call, which is the distinction spec §4.4 needs.
    flattened: dict[str, Any] = {}
    set_at: dict[str, int] = {}
    for index, spec in enumerate(chain):
        for key, value in spec.model_dump(exclude_none=True, exclude_unset=True).items():
            if key == "extends":
                continue
            flattened[key] = value
            set_at[key] = index

    # `host`, `ssh_alias` and `deployer` are alternative ways to say "where to
    # connect". Whichever the stronger (later) link set wins and the others
    # are dropped: a weaker layer must not silently shadow a stronger layer's
    # explicit choice, in any direction.
    present = [key for key in _CONNECTION_SOURCES if key in set_at]
    if not present:
        return flattened
    winner = max(present, key=lambda key: (set_at[key], _CONNECTION_SOURCES.index(key)))
    for key in present:
        if key != winner:
            flattened.pop(key)
    if winner != "deployer":
        return flattened

    # A block is strict: it is the only source for user, port, path and
    # layout, with no fallback to a literal. So a base's `port: 2222` is
    # dropped along with its `host` - this is the one place where a value
    # disappears without an error naming it, and it has to, because keeping
    # it would let a Deployer host that states no port connect on a port the
    # block never asked for.
    #
    # The reverse is not silent: a literal set by a link *stronger* than the
    # block contradicts it, exactly as one beside it would, and neither
    # overwriting nor ignoring it would honour an explicit choice.
    contradicting = [
        key for key in _DEPLOYER_DETAIL_KEYS if key in set_at and set_at[key] > set_at["deployer"]
    ]
    if contradicting:
        raise TargetError(
            f"target {name!r}: {', '.join(contradicting)} cannot be set on a target that extends "
            "one with a 'deployer:' block, which owns the connection; "
            "remove them, or give this target its own host"
        )
    for key in DEPLOYER_OWNED_KEYS:
        flattened.pop(key, None)
    return flattened


def _build(
    name: str,
    flattened: dict[str, Any],
    *,
    source: str,
    resolve_deployer: DeployerResolver | None,
) -> ResolvedTarget:
    deployer = flattened.pop("deployer", None)
    if deployer is not None:
        if resolve_deployer is None:
            raise TargetError(
                f"target {name!r} (from {source}) has a 'deployer:' block, which has to be "
                "resolved from the project's own Deployer - and nothing here can do that. "
                "This is a bug in ddev-sync, not in your configuration."
            )
        connection = resolve_deployer(deployer.get("host"))
        flattened["host"] = connection.host
        flattened["user"] = connection.user
        flattened["path"] = connection.path
        flattened["layout"] = connection.layout
        # Omitted, never set to None: `setdefault("port", 22)` below does not
        # replace a key that is present with the value None, so passing the
        # null through would leave port unset, `_ssh_prefix` would omit `-p`
        # entirely, and this target would build a different ssh command than
        # an identical literal one.
        if connection.port is not None:
            flattened["port"] = connection.port

    alias = flattened.pop("ssh_alias", None)
    if alias is not None:
        flattened["host"] = alias
        flattened["via_ssh_alias"] = True
        flattened.pop("user", None)
        flattened.pop("port", None)
    else:
        flattened.setdefault("port", 22)

    # Judged before the rewrite below, so a refused target is never left half
    # translated. Each of these is a configuration that would otherwise
    # connect somewhere plausible and wrong rather than fail.
    provider = flattened.get("provider", Provider.SERVER)
    sync_port = flattened.get("sync_port")
    if provider == Provider.DOCKER:
        if sync_port is None:
            raise TargetError(
                f"target {name!r} (from {source}) uses provider: docker but sets no sync_port; "
                "without it the connection would fall back to port 22 and meet the Docker "
                "host's own sshd as the endpoint user - set sync_port to the port the stack "
                "publishes the endpoint on"
            )
        if "jump_host" in flattened:
            raise TargetError(
                f"target {name!r} (from {source}) uses provider: docker and also sets "
                "jump_host; the provider sets the jump itself (the Docker host), and a "
                "second hop in front of it is not supported yet - remove one of them"
            )
        if flattened.get("layout") == Layout.DEPLOYER:
            raise TargetError(
                f"target {name!r} (from {source}) uses provider: docker with layout: deployer; "
                "a deployer layout resolves releases/current on the far side, and the sync "
                "endpoint has no such structure - use the default layout"
            )
    elif sync_port is not None:
        raise TargetError(
            f"target {name!r} (from {source}) sets sync_port without provider: docker; "
            "sync_port only means anything for a Docker endpoint, and ignoring it would "
            "sync from the host instead - add provider: docker, or remove sync_port"
        )

    # The third rewrite in this function, after `deployer:` and `ssh_alias`
    # above, and deliberately after both: whatever they left in `host`/`user`
    # is the Docker host, alias or not, and that is what becomes the jump. The
    # endpoint behind it is an address rather than an alias, so `via_ssh_alias`
    # is taken back.
    if provider == Provider.DOCKER:
        flattened["jump_host"] = flattened["host"]
        user = flattened.pop("user", None)
        if user is not None:
            flattened["jump_host_user"] = user
        flattened["host"] = ENDPOINT_HOST
        flattened["user"] = ENDPOINT_USER
        flattened["port"] = flattened.pop("sync_port")
        flattened["via_ssh_alias"] = False

    missing = [key for key in ("host", "path", "stage") if key not in flattened]
    if missing:
        raise TargetError(
            f"target {name!r} (from {source}) is missing {', '.join(missing)}; "
            "set them directly or inherit them with extends"
        )
    try:
        return ResolvedTarget(name=name, **flattened)
    except ValidationError as error:
        raise TargetError(f"target {name!r} (from {source}): {error}") from error
