"""Which steps the picker starts from, as a project or a target has said.

Read layer by layer rather than from the merged configuration: lists extend
across layers there, and a default that can only ever grow could not say that
this project has no files after all. The strongest layer that names a key wins.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ddev_sync.config.loader import PROJECT_LABEL
from ddev_sync.config.merge import LayerDoc
from ddev_sync.engine.steps import SELECTION_GROUPS
from ddev_sync.errors import ConfigError


@dataclass(frozen=True)
class StepDefaults:
    """`steps:` across the layers. `in_project_file` is what saving rewrites."""

    project: list[str] | None = None
    by_target: dict[str, list[str]] = field(default_factory=dict)
    ask_to_save: bool = True
    in_project_file: dict[str, list[str]] = field(default_factory=dict)

    def for_target(self, name: str) -> list[str] | None:
        """The target's own list, else the project's, else None for the built-in one."""
        return self.by_target.get(name, self.project)


def read_step_defaults(layers: Sequence[LayerDoc]) -> StepDefaults:
    """Collect `steps:` from `layers`, weakest first, refusing what is malformed."""
    project: list[str] | None = None
    by_target: dict[str, list[str]] = {}
    ask_to_save = True
    in_project_file: dict[str, list[str]] = {}
    for layer in layers:
        node = layer.data.get("steps")
        if node is None:
            continue
        if not isinstance(node, Mapping):
            raise ConfigError(f"steps in {layer.label} must be a mapping")
        if node.get("default") is not None:
            project = _names(node["default"], "steps.default", layer.label)
        targets = _targets(node.get("by_target"), layer.label)
        by_target.update(targets)
        if layer.label == PROJECT_LABEL:
            in_project_file = targets
        if node.get("ask_to_save") is not None:
            ask_to_save = _flag(node["ask_to_save"], layer.label)
    return StepDefaults(project, by_target, ask_to_save, in_project_file)


def _targets(raw: Any, label: str) -> dict[str, list[str]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"steps.by_target in {label} must map target names to lists of steps")
    return {
        str(name): _names(names, f"steps.by_target.{name}", label) for name, names in raw.items()
    }


def _names(raw: Any, key: str, label: str) -> list[str]:
    valid = ", ".join(sorted(SELECTION_GROUPS))
    if not isinstance(raw, list):
        raise ConfigError(f"{key} in {label} must be a list of steps from: {valid}")
    unknown = [str(name) for name in raw if name not in SELECTION_GROUPS]
    if unknown:
        raise ConfigError(
            f"{key} in {label} names unknown steps: {', '.join(unknown)}; choose from: {valid}"
        )
    return list(raw)


def _flag(raw: Any, label: str) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError(f"steps.ask_to_save in {label} must be true or false, not {raw!r}")
    return raw
