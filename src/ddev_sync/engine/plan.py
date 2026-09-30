"""Turn a resolved target, profile and configuration into an ordered plan.

The plan is a data structure, not a side effect. That is what makes `--dry-run`
honest - it prints the same object the executor will later walk - and what
makes every branch here testable without a network.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ddev_sync.config.merge import LIST_MODES, MergeResult
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.steps import StepKind, hook_name
from ddev_sync.errors import ConfigError
from ddev_sync.profiles.registry import ResolvedProfile
from ddev_sync.transport.ssh import rsync_pull_command, scp_download_command, ssh_command

DUMP_FILENAME = "dump.sql"
DOWNLOAD_DIR = ".ddev/.downloads"


@dataclass(frozen=True)
class PlannedStep:
    """One line of the plan: what runs, where, and which layer asked for it.

    `kind` always names the parent step, even for a hook - a `post_db_import`
    hook keeps `kind == StepKind.DB_IMPORT` so callers can still group a hook
    with the step it hangs off. `is_hook` is what the renderer uses to tell a
    step from its hooks; the parent's `kind` alone cannot do that.
    """

    index: int
    kind: StepKind
    where: str
    detail: str
    source: str
    is_hook: bool = False
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """An ordered, immutable description of a sync run."""

    target: ResolvedTarget
    profile_label: str
    project_name: str
    steps: tuple[PlannedStep, ...]
    notices: tuple[str, ...] = ()


def build_plan(
    *,
    target: ResolvedTarget,
    project: DdevProject,
    profile: ResolvedProfile,
    merged: MergeResult,
    steps: Sequence[StepKind],
) -> Plan:
    """Expand the selected steps, with their hooks, into numbered plan entries."""
    builder = _Builder(target=target, project=project, merged=merged)
    entries: list[_Entry] = []
    for kind in steps:
        if kind is StepKind.PREFLIGHT:
            # `pre_sync` is a global hook that must run before anything else.
            # PREFLIGHT is always the first selected step (StepKind.ALWAYS),
            # so attaching it here - ahead of PREFLIGHT's own `pre` hooks -
            # gives it "before anything else" semantics through the one hook
            # mechanism the plan already has, rather than a second one.
            entries.extend(builder.hooks_at(kind, ("hooks", "pre_sync")))
        entries.extend(builder.hooks(kind, "pre"))
        entries.extend(builder.step(kind))
        entries.extend(builder.hooks(kind, "post"))

    numbered = tuple(
        PlannedStep(
            index=number,
            kind=entry.kind,
            where=entry.where,
            detail=entry.detail,
            source=entry.source,
            params=entry.params,
            is_hook=entry.is_hook,
        )
        for number, entry in enumerate(entries, start=1)
    )
    notices = () if profile.notice is None else (profile.notice,)
    return Plan(
        target=target,
        profile_label=profile.label,
        project_name=project.name,
        steps=numbered,
        notices=notices,
    )


@dataclass(frozen=True)
class _Entry:
    """One expanded plan entry, before it has been numbered."""

    kind: StepKind
    where: str
    detail: str
    source: str
    params: Mapping[str, Any]
    is_hook: bool = False


class _Builder:
    """Expands one step kind into zero or more plan entries."""

    def __init__(
        self, *, target: ResolvedTarget, project: DdevProject, merged: MergeResult
    ) -> None:
        self._target = target
        self._project = project
        self._data = merged.data
        self._scalar_sources = merged.scalar_sources
        self._list_sources = merged.list_sources

    def step(self, kind: StepKind) -> list[_Entry]:
        handlers = {
            StepKind.PREFLIGHT: self._preflight,
            StepKind.DB_DUMP: self._db_dump,
            StepKind.DB_DOWNLOAD: self._db_download,
            StepKind.DB_IMPORT: self._db_import,
            StepKind.FILES_SYNC: self._files_sync,
            StepKind.SANITIZE: self._sanitize,
            StepKind.SETUP: self._setup,
        }
        return handlers[kind]()

    def hooks(self, kind: StepKind, phase: str) -> list[_Entry]:
        return self.hooks_at(kind, ("hooks", hook_name(kind, phase)))

    def hooks_at(self, kind: StepKind, hook_path: tuple[str, ...]) -> list[_Entry]:
        """Expand the command list found at `hook_path`, as hook entries for `kind`."""
        path = self._resolve_command_path(hook_path)
        return [
            _Entry(
                kind=kind,
                where="local",
                detail=_hook_detail(entry),
                source=source,
                params=dict(entry),
                is_hook=True,
            )
            for entry, source in self._command_entries_at(path)
        ]

    def _preflight(self) -> list[_Entry]:
        # Preview the connection the same way `ssh_command` will actually
        # build it, rather than reimplementing jump-host and port handling
        # here: a second implementation is a second place to forget the
        # port. `ssh_command` always appends a quoted remote command as its
        # last argument; the preview shows only how the host is reached, not
        # the probe script (an implementation detail of the preflight
        # operation), so that trailing element is dropped.
        preview = ssh_command(self._target, ())[:-1]
        detail = " ".join(preview)
        return [
            _Entry(
                kind=StepKind.PREFLIGHT, where="remote", detail=detail, source="built-in", params={}
            )
        ]

    def _db_dump(self) -> list[_Entry]:
        name_key = self._scalar_at(("database", "env_map", "name"), default="DATABASE_NAME")
        detail = f"mysqldump --single-transaction ${name_key}"
        source = self._source(("database", "env_map", "name"))
        return [
            _Entry(kind=StepKind.DB_DUMP, where="remote", detail=detail, source=source, params={})
        ]

    def _db_download(self) -> list[_Entry]:
        remote = f"{self._target.path}{DUMP_FILENAME}"
        local = f"{DOWNLOAD_DIR}/{DUMP_FILENAME}"
        detail = " ".join(scp_download_command(self._target, remote, local))
        return [
            _Entry(
                kind=StepKind.DB_DOWNLOAD, where="scp", detail=detail, source="built-in", params={}
            )
        ]

    def _db_import(self) -> list[_Entry]:
        database = self._scalar_at(("database", "target_database"), default="db")
        detail = (
            f"mysql -h {self._project.db_container} {database} < {DOWNLOAD_DIR}/{DUMP_FILENAME}"
        )
        source = self._source(("database", "target_database"))
        return [
            _Entry(kind=StepKind.DB_IMPORT, where="local", detail=detail, source=source, params={})
        ]

    def _files_sync(self) -> list[_Entry]:
        paths = self._list_at(("files", "paths"))
        sources = self._list_sources.get(("files", "paths"), ())
        entries: list[_Entry] = []
        for position, mapping in enumerate(paths):
            remote_suffix = _require_key(mapping, "remote")
            local_suffix = _require_key(mapping, "local")
            remote = f"{self._target.path}{remote_suffix}"
            local = f"{self._project.root}/{local_suffix}"
            delete = bool(mapping.get("delete", False))
            detail = " ".join(rsync_pull_command(self._target, remote, local, delete=delete))
            entries.append(
                _Entry(
                    kind=StepKind.FILES_SYNC,
                    where="rsync",
                    detail=detail,
                    source=_source_at(sources, position),
                    params=dict(mapping),
                )
            )
        return entries

    def _sanitize(self) -> list[_Entry]:
        rules = self._list_at(("sanitize", "rules"))
        if not rules:
            return []
        names = ", ".join(str(rule.get("name", "unnamed")) for rule in rules)
        plural = "rule" if len(rules) == 1 else "rules"
        sources = self._list_sources.get(("sanitize", "rules"), ())
        return [
            _Entry(
                kind=StepKind.SANITIZE,
                where="sql",
                detail=f"{len(rules)} {plural}: {names}",
                source=_source_at(sources, 0),
                params={},
            )
        ]

    def _setup(self) -> list[_Entry]:
        path = self._resolve_command_path(("setup",))
        return [
            _Entry(
                kind=StepKind.SETUP,
                where="local",
                detail=_hook_detail(entry),
                source=source,
                params=dict(entry),
            )
            for entry, source in self._command_entries_at(path)
        ]

    def _resolve_command_path(self, base_path: tuple[str, ...]) -> tuple[str, ...]:
        # A hook or `setup` block's merged shape depends on whether any layer
        # wrote an explicit `mode:` alongside `commands:` (spec §4.5): with
        # `mode`, `{mode, commands}` is a list directive and collapses to a
        # flat list right at `base_path`; without it, `commands` is an
        # ordinary nested key and the list sits one level deeper, as it
        # always has.
        #
        # `mode` with no `commands` alongside it (typically a typo that
        # dropped `commands:`) merges as ordinary data instead of a
        # directive - `config/merge.py` is deliberately schema-agnostic and
        # cannot tell the difference - and would otherwise disable the whole
        # block with nobody told. This is the one place that does know the
        # schema, so it is rejected here.
        node = self._node_at(base_path)
        if isinstance(node, list):
            return base_path
        if isinstance(node, dict) and "mode" in node and "commands" not in node:
            display = ".".join(base_path)
            valid = ", ".join(sorted(LIST_MODES))
            raise ConfigError(
                f"{display} has 'mode' but no 'commands' - probably a typo; valid modes: {valid}"
            )
        return (*base_path, "commands")

    def _list_at(self, path: tuple[str, ...]) -> list[dict[str, Any]]:
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict):
                return []
            node = node.get(part)
        if not isinstance(node, list):
            return []
        return [entry for entry in node if isinstance(entry, dict)]

    def _node_at(self, path: tuple[str, ...]) -> Any:
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    def _command_entries_at(self, path: tuple[str, ...]) -> list[tuple[dict[str, Any], str]]:
        """Hook and setup command lists (spec §4.5) additionally accept a bare
        string as shorthand for a named built-in operation, e.g.
        `- ddev-sync:flush-cache` normalises to `{"operation": "ddev-sync:flush-cache"}`.
        Anything that is neither a string nor a mapping is malformed and is
        dropped rather than crashing the whole plan.
        """
        node = self._node_at(path)
        if not isinstance(node, list):
            return []
        sources = self._list_sources.get(path, ())
        pairs: list[tuple[dict[str, Any], str]] = []
        for position, entry in enumerate(node):
            if isinstance(entry, str):
                pairs.append(({"operation": entry}, _source_at(sources, position)))
            elif isinstance(entry, dict):
                pairs.append((entry, _source_at(sources, position)))
        return pairs

    def _scalar_at(self, path: tuple[str, ...], *, default: str) -> str:
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict):
                return default
            node = node.get(part)
        return default if node is None else str(node)

    def _source(self, path: tuple[str, ...]) -> str:
        return self._scalar_sources.get(path, "built-in")


def _source_at(sources: Sequence[str], position: int) -> str:
    return sources[position] if position < len(sources) else "built-in"


def _hook_detail(entry: dict[str, Any]) -> str:
    command = entry.get("run")
    if command is None:
        return str(entry.get("operation", "unknown operation"))
    suffix = "  (allow_failure)" if entry.get("allow_failure") else ""
    return f"{command}{suffix}"


def _require_key(mapping: dict[str, Any], key: str) -> str:
    if key not in mapping:
        raise ConfigError(f"files.paths entry {mapping!r} is missing {key!r}")
    return str(mapping[key])
