"""Place one target into one configuration layer, leaving the rest alone.

Nothing else in this engine writes configuration, so the mechanism is a
choice rather than a pattern to follow. It is text, not YAML: the add-on
installs `.ddev/ddev-sync.yaml` from a template full of explanatory comments
(`install.yaml`), and a `safe_load`/`safe_dump` round trip would delete every
one of them. PyYAML still renders the *new* block - quoting is a solved
problem and not worth re-solving - but it never parses the user's file in
order to write it.

What makes text surgery acceptable is `write_target`'s check, run on the file
as it is on disk after the write: it must still load, the `name` target in it
must be exactly the block that was rendered, nothing else in the file may
differ from before, no other target may change, and no key may newly appear
twice. If any of that fails, or anything at all goes wrong after the first
byte is written, the original bytes are put back. So the scan below may fail
on flow-style mappings, anchors, quoted scalars or tabs without having to
recognise them.
"""

import os
import stat
import tempfile
from collections import Counter
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
    """Return `document` with `block` as the `name` target under `targets:`.

    The line ending the document already uses is the one the block gets, so a
    CRLF file does not end up with both.
    """
    eol = "\r\n" if "\r\n" in document else "\n"
    block = block.replace("\n", eol)
    # A last line without its newline would otherwise run into the block that
    # is placed after it.
    if document and not document.endswith("\n"):
        document += eol
    lines = document.splitlines(keepends=True)
    start = _targets_line(lines)
    if start is None:
        return f"{document}{eol}{TARGETS_KEY}:{eol}{block}"
    existing = _target_line(lines, start, name)
    if existing is None:
        end = _block_end(lines, start)
        return "".join(lines[:end]) + block + "".join(lines[end:])
    end = _block_end(lines, existing)
    return "".join(lines[:existing]) + block + "".join(lines[end:])


def write_target(*, path: Path, paths: ConfigPaths, name: str, block: str) -> None:
    """Place `block` in `path`, then prove it on disk.

    The proof is in `_verify`. Anything that fails after the write starts,
    whether a refusal or an error nobody foresaw, puts the original bytes
    back; `original` is held in memory rather than written to a backup file
    so that a failed write leaves no stray `ddev-sync.yaml.bak` behind. An
    empty file is treated like a missing one, because without `version` the
    loader rejects the layer. The parent directory is created before the
    write and is not removed again.

    Other targets are compared by their merged definition, not by what they
    resolve to. That is deliberate: rewriting a base target changes what its
    children resolve to, which is the point of rewriting it, and comparing
    resolved targets would refuse every edit of a base target.
    """
    before = _targets(paths)
    original = path.read_bytes() if path.exists() else None
    document = original.decode("utf-8") if original else "version: 1\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write(path, place_target(document, name, block).encode("utf-8"))
        _verify(path=path, paths=paths, name=name, block=block, before=before, document=document)
    except BaseException:
        _restore(path, original)
        raise


def _verify(
    *,
    path: Path,
    paths: ConfigPaths,
    name: str,
    block: str,
    before: Mapping[str, Any],
    document: str,
) -> None:
    written = path.read_bytes().decode("utf-8")
    try:
        after = _targets(paths)
    except DdevSyncError as error:
        raise WriteRefused(
            f"writing the target {name!r} to {path} produced a file the loader "
            f"rejected, so nothing was changed: {error}"
        ) from error

    # The merged layers cannot tell this: a `production` in another layer, or
    # a block that landed inside a quoted scalar, leaves `name` present while
    # the file still holds the old definition. So the file is read on its own.
    expected = _definition(yaml.safe_load(block), name)
    found = _definition((yaml.safe_load(written) or {}).get(TARGETS_KEY), name)
    if found is None or found != expected:
        raise WriteRefused(
            f"the target {name!r} in {path} is not the one that was written, so nothing was changed"
        )
    moved = sorted(_outside_targets(document).items() ^ _outside_targets(written).items())
    if moved:
        keys = sorted({key for key, _ in moved})
        raise WriteRefused(
            f"writing the target {name!r} to {path} would also have changed "
            f"{', '.join(map(repr, keys))} outside `targets`, so nothing was changed"
        )
    changed = [
        other for other, spec in before.items() if other != name and after.get(other) != spec
    ]
    if changed:
        raise WriteRefused(
            f"writing the target {name!r} to {path} would also have changed "
            f"{', '.join(sorted(changed))}, so nothing was changed"
        )
    # The loader cannot see this: PyYAML keeps the last of two equal keys and
    # says nothing, so a second `targets:` appended beside a flow-style one
    # reads back as a clean file in which the first has quietly vanished. A
    # duplicate the file already had is not the writer's doing and is left.
    repeated = sorted(_repeated_keys(written) - _repeated_keys(document))
    if repeated:
        raise WriteRefused(
            f"writing the target {name!r} to {path} left the key "
            f"{'.'.join(repeated[0])!r} in the file twice, so nothing was changed"
        )


def _definition(targets: Any, name: str) -> str | None:
    """The `name` entry of a parsed `targets` mapping, as text; None if absent."""
    if not isinstance(targets, dict) or name not in targets:
        return None
    # Compared as text rather than with `==`: a document may hold an alias that
    # refers back to itself (`x: &x [*x]`), which `safe_load` accepts and `==`
    # recurses through until it fails. `safe_dump` writes it out as an anchor.
    return _fingerprint(targets[name])


def _fingerprint(value: Any) -> str:
    return yaml.safe_dump(value)


def _outside_targets(document: str) -> dict[str, str]:
    """Every top-level key except `targets`, as text, for comparing two files."""
    loaded = yaml.safe_load(document) or {}
    return {str(key): _fingerprint(value) for key, value in loaded.items() if key != TARGETS_KEY}


def _targets(paths: ConfigPaths) -> dict[str, Any]:
    # `collect_targets` takes the layers themselves and does its own merging
    # (`config/targets.py:42`), so nothing here merges them first.
    collected = collect_targets(load_user_layers(paths))
    return {name: entry.spec for name, entry in collected.items()}


def _write(path: Path, data: bytes) -> None:
    """Replace `path` with `data` in one step, so a failure cannot truncate it.

    The file is written beside the real one and renamed over it. `resolve()`
    makes that the symlink's target, so a linked `ddev-sync.yaml` stays a link.
    The mode of an existing file is kept; a new one gets 0644 like the
    template the add-on installs, since `mkstemp` would make it private.
    """
    target = path.resolve()
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _restore(path: Path, original: bytes | None) -> None:
    if original is None:
        path.resolve().unlink(missing_ok=True)
        return
    _write(path, original)


def _repeated_keys(document: str) -> set[tuple[str, ...]]:
    """The path of every mapping key that appears twice in the same mapping."""
    found: set[tuple[str, ...]] = set()
    _collect_repeats(yaml.compose(document, Loader=yaml.SafeLoader), (), set(), found)
    return found


def _collect_repeats(
    node: yaml.Node | None,
    path: tuple[str, ...],
    visited: set[int],
    found: set[tuple[str, ...]],
) -> None:
    # An alias hands back the node it points at, so `x: &x [*x]` leads here
    # forever unless a node already walked is skipped.
    if node is None or id(node) in visited:
        return
    visited.add(id(node))
    if isinstance(node, yaml.SequenceNode):
        for position, child in enumerate(node.value):
            _collect_repeats(child, (*path, str(position)), visited, found)
    elif isinstance(node, yaml.MappingNode):
        # Scalar keys only, compared by tag and text so that `1` and `'1'`
        # stay apart. A non-scalar key cannot repeat as text.
        keys = Counter(
            (key.tag, key.value) for key, _ in node.value if isinstance(key, yaml.ScalarNode)
        )
        found.update((*path, text) for (_, text), count in keys.items() if count > 1)
        for key, value in node.value:
            _collect_repeats(value, (*path, str(key.value)), visited, found)


def _targets_line(lines: list[str]) -> int | None:
    """The index of the first line that is exactly `targets:`, if any.

    Only that spelling is found: `targets: {...}`, `targets:  # note` and a
    quoted key are not, and the same text inside a multi-line quoted scalar is
    taken for the real thing. `write_target`'s check exists to catch both.
    """
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == f"{TARGETS_KEY}:":
            return index
    return None


def _target_line(lines: list[str], start: int, name: str) -> int | None:
    for index in range(start + 1, _block_end(lines, start)):
        if lines[index].rstrip("\r\n") == f"{_INDENT}{name}:":
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
