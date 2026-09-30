"""Merge configuration layers weakest-to-strongest while tracking provenance.

Provenance is not a nicety: `--dry-run` annotates every planned step with the
layer that produced it, which is how a user answers "why is it doing that?"
without reading four YAML files.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ddev_sync.errors import ConfigError

LIST_MODES = frozenset({"extend", "replace", "prepend"})
DEFAULT_LIST_MODE = "extend"


@dataclass(frozen=True)
class LayerDoc:
    """One configuration layer and the human-readable name of its origin."""

    label: str
    data: dict[str, Any]


@dataclass(frozen=True)
class MergeResult:
    """The merged document plus, for every value, which layer supplied it."""

    data: dict[str, Any] = field(default_factory=dict)
    scalar_sources: dict[str, str] = field(default_factory=dict)
    list_sources: dict[str, tuple[str, ...]] = field(default_factory=dict)


def merge_layers(layers: Sequence[LayerDoc]) -> MergeResult:
    """Merge `layers` in order; later layers override earlier ones."""
    result = MergeResult()
    for current in layers:
        _merge_into(result, current.data, current.label, prefix="", node=result.data)
    return result


def _merge_into(
    result: MergeResult,
    source: dict[str, Any],
    label: str,
    prefix: str,
    node: dict[str, Any],
) -> None:
    # `node` is the actual dict object to write into, passed down by reference
    # from the caller. `prefix` is only used to build the dotted provenance
    # path (`scalar_sources` / `list_sources` keys); it is never re-parsed to
    # find the node again, because a literal "." inside a key would make that
    # round-trip ambiguous (a key named "." would be indistinguishable from a
    # path separator).
    for key, value in source.items():
        path = f"{prefix}.{key}" if prefix else key
        _merge_value(result, key, value, label, path, parent=node)


def _merge_value(
    result: MergeResult,
    key: str,
    value: Any,
    label: str,
    path: str,
    parent: dict[str, Any],
) -> None:
    if isinstance(value, dict) and _is_list_directive(value, path):
        _apply_list(result, key, value, label, path, parent)
        return
    if isinstance(value, list):
        directive = {"mode": DEFAULT_LIST_MODE, "values": value}
        _apply_list(result, key, directive, label, path, parent)
        return
    if isinstance(value, dict):
        existing = parent.get(key)
        if not isinstance(existing, dict):
            parent[key] = {}
        _merge_into(result, value, label, prefix=path, node=parent[key])
        return
    parent[key] = value
    result.scalar_sources[path] = label


def _is_list_directive(value: dict[str, Any], path: str) -> bool:
    if "values" not in value:
        return False
    unexpected = set(value) - {"mode", "values"}
    if unexpected:
        raise ConfigError(
            f"{path}: a list directive accepts only 'mode' and 'values', "
            f"found {', '.join(sorted(unexpected))}"
        )
    if not isinstance(value["values"], list):
        raise ConfigError(f"{path}: 'values' must be a list")
    mode = value.get("mode", DEFAULT_LIST_MODE)
    if mode not in LIST_MODES:
        raise ConfigError(
            f"{path}: unknown list mode {mode!r}; expected one of {', '.join(sorted(LIST_MODES))}"
        )
    return True


def _apply_list(
    result: MergeResult,
    key: str,
    directive: dict[str, Any],
    label: str,
    path: str,
    parent: dict[str, Any],
) -> None:
    mode = directive.get("mode", DEFAULT_LIST_MODE)
    values = list(directive["values"])
    existing = parent.get(key)
    base = list(existing) if isinstance(existing, list) else []
    base_labels = list(result.list_sources.get(path, ()))
    if len(base_labels) != len(base):
        base_labels = [label] * len(base)
    incoming_labels = [label] * len(values)

    if mode == "replace":
        merged, labels = values, incoming_labels
    elif mode == "prepend":
        merged, labels = values + base, incoming_labels + base_labels
    else:
        merged, labels = base + values, base_labels + incoming_labels

    parent[key] = merged
    result.list_sources[path] = tuple(labels)
