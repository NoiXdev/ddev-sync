"""Turn a resolved target, profile and configuration into an ordered plan.

The plan is a data structure, not a side effect. That is what makes `--dry-run`
honest - it prints the same object the executor will later walk - and what
makes every branch here testable without a network.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ddev_sync.config.merge import MergeResult
from ddev_sync.config.models import ResolvedTarget
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.steps import StepKind, hook_name
from ddev_sync.profiles.registry import ResolvedProfile
from ddev_sync.transport.ssh import remote_endpoint, scp_download_command

DUMP_FILENAME = "dump.sql"
DOWNLOAD_DIR = ".ddev/.downloads"


@dataclass(frozen=True)
class PlannedStep:
    """One line of the plan: what runs, where, and which layer asked for it."""

    index: int
    kind: StepKind
    where: str
    detail: str
    source: str


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
    entries: list[tuple[StepKind, str, str, str]] = []
    for kind in steps:
        entries.extend(builder.hooks(kind, "pre"))
        entries.extend(builder.step(kind))
        entries.extend(builder.hooks(kind, "post"))

    numbered = tuple(
        PlannedStep(index=number, kind=kind, where=where, detail=detail, source=source)
        for number, (kind, where, detail, source) in enumerate(entries, start=1)
    )
    notices = () if profile.notice is None else (profile.notice,)
    return Plan(
        target=target,
        profile_label=profile.label,
        project_name=project.name,
        steps=numbered,
        notices=notices,
    )


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

    def step(self, kind: StepKind) -> list[tuple[StepKind, str, str, str]]:
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

    def hooks(self, kind: StepKind, phase: str) -> list[tuple[StepKind, str, str, str]]:
        path = ("hooks", hook_name(kind, phase), "commands")
        commands = self._list_at(path)
        sources = self._list_sources.get(path, ())
        return [
            (kind, "local", _hook_detail(entry), _source_at(sources, position))
            for position, entry in enumerate(commands)
        ]

    def _preflight(self) -> list[tuple[StepKind, str, str, str]]:
        detail = f"ssh {' '.join(_ssh_preview(self._target))}"
        return [(StepKind.PREFLIGHT, "remote", detail, "built-in")]

    def _db_dump(self) -> list[tuple[StepKind, str, str, str]]:
        name_key = self._scalar_at(("database", "env_map", "name"), default="DATABASE_NAME")
        detail = f"mysqldump --single-transaction ${name_key}"
        return [(StepKind.DB_DUMP, "remote", detail, self._source(("database", "env_map", "name")))]

    def _db_download(self) -> list[tuple[StepKind, str, str, str]]:
        remote = f"{self._target.path}{DUMP_FILENAME}"
        local = f"{DOWNLOAD_DIR}/{DUMP_FILENAME}"
        command = scp_download_command(self._target, remote, local)
        detail = f"{command[-2]} -> {local}"
        return [(StepKind.DB_DOWNLOAD, "scp", detail, "built-in")]

    def _db_import(self) -> list[tuple[StepKind, str, str, str]]:
        database = self._scalar_at(("sync", "database", "target_database"), default="db")
        detail = (
            f"mysql -h {self._project.db_container} {database} < {DOWNLOAD_DIR}/{DUMP_FILENAME}"
        )
        return [
            (
                StepKind.DB_IMPORT,
                "local",
                detail,
                self._source(("sync", "database", "target_database")),
            )
        ]

    def _files_sync(self) -> list[tuple[StepKind, str, str, str]]:
        paths = self._list_at(("files", "paths"))
        sources = self._list_sources.get(("files", "paths"), ())
        entries: list[tuple[StepKind, str, str, str]] = []
        for position, mapping in enumerate(paths):
            remote = f"{self._target.path}{mapping['remote']}"
            local = f"{self._project.root}/{mapping['local']}"
            detail = f"{remote} -> {local}"
            entries.append((StepKind.FILES_SYNC, "rsync", detail, _source_at(sources, position)))
        return entries

    def _sanitize(self) -> list[tuple[StepKind, str, str, str]]:
        rules = self._list_at(("sanitize", "rules"))
        if not rules:
            return []
        names = ", ".join(str(rule.get("name", "unnamed")) for rule in rules)
        plural = "rule" if len(rules) == 1 else "rules"
        sources = self._list_sources.get(("sanitize", "rules"), ())
        return [
            (
                StepKind.SANITIZE,
                "sql",
                f"{len(rules)} {plural}: {names}",
                _source_at(sources, 0),
            )
        ]

    def _setup(self) -> list[tuple[StepKind, str, str, str]]:
        commands = self._list_at(("setup", "commands"))
        sources = self._list_sources.get(("setup", "commands"), ())
        return [
            (StepKind.SETUP, "local", _hook_detail(entry), _source_at(sources, position))
            for position, entry in enumerate(commands)
        ]

    def _list_at(self, path: tuple[str, ...]) -> list[dict[str, Any]]:
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict):
                return []
            node = node.get(part)
        if not isinstance(node, list):
            return []
        return [entry for entry in node if isinstance(entry, dict)]

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


def _ssh_preview(target: ResolvedTarget) -> list[str]:
    parts: list[str] = []
    if target.jump_host is not None:
        endpoint = target.jump_host
        if target.jump_host_user is not None:
            endpoint = f"{target.jump_host_user}@{endpoint}"
        parts.extend(["-J", f"{endpoint}:{target.jump_host_port}"])
    parts.append(remote_endpoint(target))
    return parts
