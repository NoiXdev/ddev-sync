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
    """The merged document plus, for every value, which layer supplied it.

    Provenance keys are `tuple[str, ...]`, not dot-joined strings: a literal
    key such as `"a.b"` and the nested path `a -> b` would otherwise produce
    the identical string `"a.b"` and silently collide. Tuples of the raw path
    segments are unambiguous by construction.
    """

    data: dict[str, Any] = field(default_factory=dict)
    scalar_sources: dict[tuple[str, ...], str] = field(default_factory=dict)
    list_sources: dict[tuple[str, ...], tuple[str, ...]] = field(default_factory=dict)


def merge_layers(layers: Sequence[LayerDoc]) -> MergeResult:
    """Merge `layers` in order; later layers override earlier ones."""
    result = MergeResult()
    for current in layers:
        _merge_into(result, current.data, current.label, prefix=(), node=result.data)
    return result


def _merge_into(
    result: MergeResult,
    source: dict[str, Any],
    label: str,
    prefix: tuple[str, ...],
    node: dict[str, Any],
) -> None:
    # `node` is the actual dict object to write into, passed down by reference
    # from the caller. `prefix` is a tuple of raw path segments, used only to
    # build the provenance keys (`scalar_sources` / `list_sources`); it is
    # never joined into a string and re-parsed to find the node again, which
    # is what made the original dot-joined design ambiguous for keys
    # containing a literal ".".
    for key, value in source.items():
        path = (*prefix, key)
        _merge_value(result, key, value, label, path, parent=node)


def _merge_value(
    result: MergeResult,
    key: str,
    value: Any,
    label: str,
    path: tuple[str, ...],
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


def _display(path: tuple[str, ...]) -> str:
    """Render a path tuple for human-readable error text only."""
    return ".".join(path)


def _is_list_directive(value: dict[str, Any], path: tuple[str, ...]) -> bool:
    if "values" not in value:
        return False
    unexpected = set(value) - {"mode", "values"}
    if unexpected:
        raise ConfigError(
            f"{_display(path)}: a list directive accepts only 'mode' and 'values', "
            f"found {', '.join(sorted(unexpected))}"
        )
    if not isinstance(value["values"], list):
        raise ConfigError(f"{_display(path)}: 'values' must be a list")
    mode = value.get("mode", DEFAULT_LIST_MODE)
    if mode not in LIST_MODES:
        raise ConfigError(
            f"{_display(path)}: unknown list mode {mode!r}; expected one of "
            f"{', '.join(sorted(LIST_MODES))}"
        )
    return True


def _apply_list(
    result: MergeResult,
    key: str,
    directive: dict[str, Any],
    label: str,
    path: tuple[str, ...],
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
