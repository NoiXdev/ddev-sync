"""Place one target into one configuration layer, leaving the rest alone.

Nothing else in this engine writes configuration, so the mechanism is a
choice rather than a pattern to follow. It is text, not YAML: the add-on
installs `.ddev/ddev-sync.yaml` from a template full of explanatory comments
(`install.yaml`), and a `safe_load`/`safe_dump` round trip would delete every
one of them. PyYAML still renders the *new* block - quoting is a solved
problem and not worth re-solving - but it never parses the user's file.

What makes text surgery acceptable is `write_target`'s check: the whole
configuration is read through the real loader before and after, every other
target's definition must come out unchanged, the file must not hold a key
twice, and anything else restores the file. So the scan below may fail on
flow-style mappings, anchors or tabs without having to recognise them.
"""

from collections.abc import Mapping
from pathlib import Path
from textwrap import indent
from typing import Any

import yaml

from ddev_sync.config.loader import ConfigPaths, load_user_layers
from ddev_sync.config.targets import collect_targets
from ddev_sync.errors import DdevSyncError

TARGETS_KEY = "targets"
_INDENT = "  "


class WriteRefused(DdevSyncError):
    """The write was undone because it could not be proved correct."""


def render_target(name: str, fields: Mapping[str, Any]) -> str:
    """The YAML lines for one target, indented to sit under `targets:`.

    `sort_keys=False` keeps the order the caller chose, which is the order a
    person reads: where the host is, who connects, which stage it is.
    """
    body = yaml.safe_dump({name: dict(fields)}, default_flow_style=False, sort_keys=False)
    return indent(body, _INDENT)


def place_target(document: str, name: str, block: str) -> str:
    """Return `document` with `block` as the `name` target under `targets:`."""
    # A last line without its newline would otherwise run into the block that
    # is placed after it.
    if document and not document.endswith("\n"):
        document += "\n"
    lines = document.splitlines(keepends=True)
    start = _targets_line(lines)
    if start is None:
        return f"{document}\n{TARGETS_KEY}:\n{block}"
    existing = _target_line(lines, start, name)
    if existing is None:
        end = _block_end(lines, start)
        return "".join(lines[:end]) + block + "".join(lines[end:])
    end = _block_end(lines, existing)
    return "".join(lines[:existing]) + block + "".join(lines[end:])


def write_target(*, path: Path, paths: ConfigPaths, name: str, block: str) -> None:
    """Place `block` in `path`, then prove it through the real loader.

    `original` is held in memory rather than written to a backup file: a
    failed write must not leave a stray `ddev-sync.yaml.bak` behind. An empty
    file is treated like a missing one, because without `version` the loader
    rejects the layer.
    """
    before = _targets(paths)
    original = path.read_text(encoding="utf-8") if path.exists() else None
    document = original or "version: 1\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(place_target(document, name, block), encoding="utf-8")
    try:
        after = _targets(paths)
    except DdevSyncError as error:
        _restore(path, original)
        raise WriteRefused(
            f"writing the target {name!r} to {path} produced a file the loader "
            f"rejected, so nothing was changed: {error}"
        ) from error
    if name not in after:
        _restore(path, original)
        raise WriteRefused(
            f"the target {name!r} is not there after writing it to {path}, so nothing was changed"
        )
    changed = [
        other for other, spec in before.items() if other != name and after.get(other) != spec
    ]
    if changed:
        _restore(path, original)
        raise WriteRefused(
            f"writing the target {name!r} to {path} would also have changed "
            f"{', '.join(sorted(changed))}, so nothing was changed"
        )
    # The loader cannot see this: PyYAML keeps the last of two equal keys and
    # says nothing, so a second `targets:` appended beside a flow-style one
    # reads back as a clean file in which the first has quietly vanished.
    repeated = _repeated_key(path.read_text(encoding="utf-8"))
    if repeated is not None:
        _restore(path, original)
        raise WriteRefused(
            f"writing the target {name!r} to {path} left the key {repeated!r} in the "
            "file twice, so nothing was changed"
        )


def _targets(paths: ConfigPaths) -> dict[str, Any]:
    # `collect_targets` takes the layers themselves and does its own merging
    # (`config/targets.py:42`), so nothing here merges them first.
    collected = collect_targets(load_user_layers(paths))
    return {name: entry.spec for name, entry in collected.items()}


def _repeated_key(document: str) -> str | None:
    """The first mapping key that appears twice in one mapping, if any."""
    return _repeated_key_in(yaml.compose(document, Loader=yaml.SafeLoader))


def _repeated_key_in(node: yaml.Node | None) -> str | None:
    if isinstance(node, yaml.SequenceNode):
        children: list[yaml.Node] = list(node.value)
    elif isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        children = []
        for key, value in node.value:
            # Only plain keys are compared; the tag keeps `1` and `'1'` apart.
            if isinstance(key, yaml.ScalarNode):
                if (key.tag, key.value) in seen:
                    return str(key.value)
                seen.add((key.tag, key.value))
            children.append(value)
    else:
        return None
    for child in children:
        found = _repeated_key_in(child)
        if found is not None:
            return found
    return None


def _restore(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(original, encoding="utf-8")


def _targets_line(lines: list[str]) -> int | None:
    """The index of an uncommented `targets:` at the top level, if any."""
    for index, line in enumerate(lines):
        if line.rstrip("\n") == f"{TARGETS_KEY}:":
            return index
    return None


def _target_line(lines: list[str], start: int, name: str) -> int | None:
    for index in range(start + 1, _block_end(lines, start)):
        if lines[index].rstrip("\n") == f"{_INDENT}{name}:":
            return index
    return None


def _block_end(lines: list[str], start: int) -> int:
    """The index just past the last content line of the block at `start`.

    Blank and comment lines after the last content line belong to whatever
    follows, not to this block: they may be the note above the next target or
    a trailing note at the end of the file, and a replacement that took them
    along would delete someone's comment. A comment between two content
    lines is inside the block and goes with it.
    """
    opening = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(lines[index]) - len(lines[index].lstrip()) <= opening:
            break
        end = index + 1
    return end
