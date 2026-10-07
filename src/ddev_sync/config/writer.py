"""Place one target, or several settings, into one configuration layer, leaving the rest alone.

Nothing else in this engine writes configuration, so the mechanism is a
choice rather than a pattern to follow. It is text, not YAML: the add-on
installs `.ddev/ddev-sync.yaml` from a template full of explanatory comments
(`install.yaml`), and a `safe_load`/`safe_dump` round trip would delete every
one of them. PyYAML still renders the *new* block - quoting is a solved
problem and not worth re-solving - but it never parses the user's file in
order to write it. One placement mechanism, `place_entry`, serves both
writers below it: `place_target` is the `targets`-shaped special case
`render_target`'s block goes through, `write_settings` walks it once per
dotted key with `render_setting`'s block.

What makes text surgery acceptable is the pair of proofs that follow each
write, not the write itself - the scan above may fail on flow-style mappings,
anchors, quoted scalars or tabs without having to recognise any of them,
because nothing downstream trusts it blindly. `write_target` proves it
changed one target and nothing else: before the write the whole configuration
must load at all - `yamlfile.py` refuses one that names a key twice - and
afterwards, read back from disk, the `name` target must be exactly the block
that was rendered, nothing outside `targets` may differ from before, and no
other target **in this file** may change. `write_settings` proves the
complementary half: it changed exactly the named dotted keys, and no target
at all - the check `write_target` was deliberately not widened to perform,
because admitting a list of exceptions is a weaker promise than admitting
none. Either writer puts the original bytes back if its proof fails, or if
anything at all goes wrong after the first byte is written.
"""

import os
import stat
import tempfile
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

    Part A's entry point, kept because its callers and its tests name it, and
    because "place a target" is what `init` is doing at that call site. The
    mechanism beneath it is no longer target-specific.
    """
    return place_entry(document, (TARGETS_KEY, name), block)


def place_entry(document: str, path: tuple[str, ...], block: str) -> str:
    """Return `document` with `block` as the value at `path`.

    `block` arrives already indented for its depth - `render_target` and
    `render_setting` both do that - so this function places lines, it does not
    reformat them.

    The walk is the same one part A did by hand for `targets:` then the target
    name, with the depth no longer fixed at two: each key is looked for within
    the block its parent opened, and the first key that is not there ends the
    search, because everything below a key that does not exist cannot exist
    either. The scan stays as naive as part A's - only an exactly-spelled
    `key:` line counts - and `write_target` and `write_settings` are what make
    that safe.
    """
    eol = "\r\n" if "\r\n" in document else "\n"
    block = block.replace("\n", eol)
    # A last line without its newline would otherwise run into the block that
    # is placed after it.
    if document and not document.endswith("\n"):
        document += eol
    lines = document.splitlines(keepends=True)
    low, high = 0, len(lines)
    for level, key in enumerate(path[:-1]):
        found = _key_line(lines, low, high, key, _INDENT * level, inline=False)
        if found is None:
            return _append(lines, high, path[level:-1], block, eol, level)
        low, high = found + 1, _block_end(lines, found)
    depth = len(path) - 1
    found = _key_line(lines, low, high, path[-1], _INDENT * depth, inline=True)
    if found is None:
        return _append(lines, high, (), block, eol, depth)
    return "".join(lines[:found]) + block + "".join(lines[_block_end(lines, found) :])


def _append(
    lines: list[str],
    at: int,
    parents: tuple[str, ...],
    block: str,
    eol: str,
    level: int,
) -> str:
    """Insert `parents` (as empty mappings) and then `block` at line `at`.

    A new **top-level** key is preceded by a blank line, which is what part A
    did for a newly created `targets:` and what keeps the shipped template -
    which is almost entirely comments - readable after `init` has written to
    it. A nested key gets no such separator: a blank line inside a mapping
    reads as the end of it.
    """
    head = "".join(lines[:at])
    if level == 0 and head:
        head += eol
    opening = "".join(
        f"{_INDENT * depth}{name}:{eol}" for depth, name in enumerate(parents, start=level)
    )
    return head + opening + block + "".join(lines[at:])


def render_setting(route: tuple[str, ...], value: Any) -> str:
    """The YAML lines for one setting, indented to sit under its parents.

    Rendered through PyYAML for the same reason `render_target` is: quoting is
    a solved problem and not worth re-solving.

    A list is written in **flow** style - `file: [.env]`, not `file:` over a
    block of items - so the whole setting is one line. Two reasons, and the
    first is enough on its own: a one-line value cannot be half-replaced, so a
    rewrite of this key can never strand part of the old value in the file.
    The second is that `default_flow_style=None`, which would also flow the
    list, flows the *mapping* too and would write
    `{production_url: 'https://...'}` for the scalar case - a key the next
    run's scan could not find. `width=` is passed explicitly and large: PyYAML
    wraps a flow sequence onto a continuation line past its default width
    (80), which a long enough list of names reaches easily, and a wrapped
    value is two physical lines pretending to be the one line this function
    promises.
    """
    key = route[-1]
    if isinstance(value, list):
        rendered = yaml.safe_dump(value, default_flow_style=True, width=1_000_000).strip()
        body = f"{key}: {rendered}\n"
    else:
        body = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False)
    return indent(body, _INDENT * (len(route) - 1))


def write_target(*, path: Path, paths: ConfigPaths, name: str, block: str) -> None:
    """Place `block` in `path`, then prove it on disk.

    The proof is in `_verify`. Anything that fails after the write starts,
    whether a refusal or an error nobody foresaw, puts the original bytes
    back; `original` is held in memory rather than written to a backup file
    so that a failed write leaves no stray `ddev-sync.yaml.bak` behind. An
    empty file is treated like a missing one, because without `version` the
    loader rejects the layer. The parent directory is created before the
    write and is not removed again.

    Other targets are compared as **this file** defines them - parsed from it
    and dumped back to text - rather than merged across layers or resolved.
    Not merged, because a stronger layer defining the same target would hide a
    neighbour this file quietly lost. Not resolved, because rewriting a base
    target changes what its children resolve to, which is the point of
    rewriting it, so comparing resolutions would refuse every such edit.
    """
    # Read before anything is touched, for its error alone: a file the loader
    # refuses - one that already names a key twice, say - is one the engine
    # would misread, so adding a target to it would build on a document it
    # cannot agree on. Refusing here leaves the file alone entirely.
    _targets(paths)
    original = path.read_bytes() if path.exists() else None
    document = original.decode("utf-8") if original else "version: 1\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write(path, place_target(document, name, block).encode("utf-8"))
        _verify(path=path, paths=paths, name=name, block=block, document=document)
    except BaseException:
        _restore(path, original)
        raise


def _verify(
    *,
    path: Path,
    paths: ConfigPaths,
    name: str,
    block: str,
    document: str,
) -> None:
    written = path.read_bytes().decode("utf-8")
    try:
        # Read for its error path alone: this is what proves the whole merged
        # configuration still loads, including the strict loader's refusal of
        # a file that now names a key twice.
        _targets(paths)
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
    # Compared within this file, not across the merged layers: a stronger
    # layer defining the same target would otherwise hide a neighbour this
    # file quietly lost. Reachable, and not only in theory - a quoted target
    # key the scan misses, plus a multi-line quoted scalar for the block to
    # land in, corrupts a neighbour while leaving every check above happy.
    # Both sides parsed once, not once per candidate name: re-parsing the
    # document inside the comprehension made writing one target into a file
    # that already held 300 of them take half a minute.
    was = _file_targets(document)
    now = _file_targets(written)
    changed = [
        other for other in set(was) | set(now) if other != name and was.get(other) != now.get(other)
    ]
    if changed:
        raise WriteRefused(
            f"writing the target {name!r} to {path} would also have changed "
            f"{', '.join(sorted(changed))}, so nothing was changed"
        )


def write_settings(*, path: Path, paths: ConfigPaths, settings: Mapping[str, Any]) -> None:
    """Place each dotted key of `settings` in `path`, then prove it on disk.

    The mirror of `write_target`: that function proves it touched one target
    and nothing else, this one proves it touched the named keys and no target.
    Between them the file is covered. `write_target` was deliberately not
    widened to do this - its check that *nothing* outside `targets` moved is
    worth more than one that admits a list of exceptions the caller supplies.

    As in `write_target`, `original` is held in memory rather than written to a
    backup file, so a refusal leaves no stray `.bak` behind, and an empty file
    is treated like a missing one because the loader rejects a layer with no
    `version`.
    """
    # Read before anything is touched, for its error alone: a file the loader
    # already refuses is one the engine would misread, so writing to it would
    # build on a document it cannot agree on.
    _targets(paths)
    original = path.read_bytes() if path.exists() else None
    document = original.decode("utf-8") if original else "version: 1\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    written = document
    for dotted, value in settings.items():
        route = tuple(dotted.split("."))
        written = place_entry(written, route, render_setting(route, value))
    try:
        _write(path, written.encode("utf-8"))
        _verify_settings(path=path, paths=paths, settings=settings, document=document)
    except BaseException:
        _restore(path, original)
        raise


def _verify_settings(
    *,
    path: Path,
    paths: ConfigPaths,
    settings: Mapping[str, Any],
    document: str,
) -> None:
    written = path.read_bytes().decode("utf-8")
    try:
        # Read for its error path alone: this proves the whole merged
        # configuration still loads, including the strict loader's refusal of
        # a file that now names a key twice - which is exactly what placing a
        # block at a key the scan could not see produces.
        _targets(paths)
    except DdevSyncError as error:
        raise WriteRefused(
            f"writing {', '.join(sorted(settings))} to {path} produced a file the "
            f"loader rejected, so nothing was changed: {error}"
        ) from error

    loaded = yaml.safe_load(written) or {}
    for dotted, value in settings.items():
        if _at(loaded, tuple(dotted.split("."))) != _fingerprint(value):
            raise WriteRefused(
                f"the setting {dotted!r} in {path} is not the one that was written, "
                "so nothing was changed"
            )
    # Only the top-level key of each route is exempt: a route's deeper keys
    # live inside it, so comparing at the top level already covers them.
    touched = {dotted.split(".")[0] for dotted in settings}
    before = {key: value for key, value in _outside_targets(document).items() if key not in touched}
    after = {key: value for key, value in _outside_targets(written).items() if key not in touched}
    moved = sorted(before.items() ^ after.items())
    if moved:
        keys = sorted({key for key, _ in moved})
        raise WriteRefused(
            f"writing {', '.join(sorted(settings))} to {path} would also have changed "
            f"{', '.join(map(repr, keys))}, so nothing was changed"
        )
    # No target may move at all - this writer never has a reason to touch one,
    # so unlike `write_target` there is no name to exempt.
    if _file_targets(document) != _file_targets(written):
        raise WriteRefused(
            f"writing {', '.join(sorted(settings))} to {path} would also have changed "
            "a target, so nothing was changed"
        )


def _at(loaded: Any, route: tuple[str, ...]) -> str | None:
    """The value at `route` in a parsed document, as text; None if absent.

    Compared as text for the reason `_definition` gives: a document may hold an
    alias that refers back to itself, which `safe_load` accepts and `==`
    recurses through until it fails.
    """
    node: Any = loaded
    for key in route:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return _fingerprint(node)


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


def _file_targets(document: str) -> dict[str, str]:
    """Every target this one document defines, as text, keyed by name."""
    targets = (yaml.safe_load(document) or {}).get(TARGETS_KEY)
    if not isinstance(targets, dict):
        return {}
    return {str(name): _fingerprint(value) for name, value in targets.items()}


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


def _key_line(
    lines: list[str], low: int, high: int, key: str, indent: str, *, inline: bool
) -> int | None:
    """The index in `lines[low:high]` of the line opening `key`, if any.

    `inline` is the difference between a key on the way down and the key being
    written. A **parent** must be a bare `key:` - the walk descends into the
    block it opens, and `targets: {a: 1}` opens no block, so matching it would
    mean descending into nothing. A **leaf** may carry its value on the same
    line, because that is what every scalar looks like (`production_url:
    https://...`) and what this module writes for a list (`features: [a, b]`).
    Without that, nothing `write_settings` wrote could ever be rewritten: the
    second run would not find its own key, would append a duplicate, and the
    strict loader would refuse the result - `init` would work exactly once per
    project.

    Still not found, by design: a quoted key, `key:  # note`, and the same text
    inside a multi-line quoted scalar, which is taken for the real thing. The
    checks in `write_target` and `write_settings` exist to catch all three.
    """
    for index in range(low, high):
        text = lines[index].rstrip("\r\n")
        if text == f"{indent}{key}:":
            return index
        if inline and text.startswith(f"{indent}{key}: "):
            return index
    return None


def _block_end(lines: list[str], start: int) -> int:
    """The index just past the last content line of the block at `start`.

    Blank and comment lines after the last content line belong to whatever
    follows, not to this block: they may be the note above the next target or
    a trailing note at the end of the file, and a replacement that took them
    along would delete someone's comment. A comment between two content
    lines is inside the block and goes with it.

    A **sequence item at the key's own indent** is part of that key's value,
    not the start of the next one. `file:` followed by `- .env` at the same
    column is how PyYAML itself writes a list and how most people write one by
    hand; treating those items as outside the block would make a replacement
    leave them behind, stranded under a mapping line, which is not even valid
    YAML. Reachable through `env.file`, which is exactly that shape.
    """
    opening = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        here = len(lines[index]) - len(lines[index].lstrip())
        if here < opening:
            break
        if here == opening and not (stripped == "-" or stripped.startswith("- ")):
            break
        end = index + 1
    return end
