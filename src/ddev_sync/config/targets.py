"""Collect target definitions across layers and resolve them into complete targets."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, TargetSpec
from ddev_sync.errors import TargetError

MAX_EXTENDS_DEPTH = 16


@dataclass(frozen=True)
class CollectedTarget:
    """A target definition merged across layers, plus where it last changed."""

    spec: TargetSpec
    source: str


def collect_targets(layers: Sequence[LayerDoc]) -> dict[str, CollectedTarget]:
    """Merge every layer's `targets:` block and note the strongest contributor."""
    merged = merge_layers(
        [LayerDoc(entry.label, {"targets": entry.data.get("targets", {})}) for entry in layers]
    )
    raw_targets = merged.data.get("targets", {})
    collected: dict[str, CollectedTarget] = {}
    for name, raw in raw_targets.items():
        collected[name] = CollectedTarget(
            spec=_parse_spec(name, raw),
            source=_strongest_source(name, raw, merged.scalar_sources, layers),
        )
    return collected


def list_target_names(collected: Mapping[str, CollectedTarget]) -> list[str]:
    """Target names in a stable order, for menus and `ddev sync targets`."""
    return sorted(collected)


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


def resolve_target(name: str, collected: Mapping[str, CollectedTarget]) -> ResolvedTarget:
    """Flatten a target's `extends` chain and validate that it is complete."""
    chain = _extends_chain(name, collected)
    flattened = _flatten(chain)
    return _build(name, flattened)


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


def _flatten(chain: Sequence[TargetSpec]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for spec in chain:
        for key, value in spec.model_dump(exclude_none=True, exclude_defaults=True).items():
            if key != "extends":
                flattened[key] = value
    return flattened


def _build(name: str, flattened: dict[str, Any]) -> ResolvedTarget:
    alias = flattened.pop("ssh_alias", None)
    if alias is not None:
        flattened["host"] = alias
        flattened["via_ssh_alias"] = True
        flattened.pop("user", None)
        flattened.pop("port", None)
    else:
        flattened.setdefault("port", 22)

    missing = [key for key in ("host", "path", "stage") if key not in flattened]
    if missing:
        raise TargetError(
            f"target {name!r} is missing {', '.join(missing)}; "
            "set them directly or inherit them with extends"
        )
    try:
        return ResolvedTarget(name=name, **flattened)
    except ValidationError as error:
        raise TargetError(f"target {name!r}: {error}") from error
