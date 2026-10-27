"""Turn a resolved target, profile and configuration into an ordered plan.

The plan is a data structure, not a side effect. That is what makes `--dry-run`
honest - it prints the same object the executor will later walk - and what
makes every branch here testable without a network.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ddev_sync.config.merge import LIST_MODES, MergeResult
from ddev_sync.config.models import Provider, ResolvedTarget
from ddev_sync.ddev.project import DdevProject
from ddev_sync.engine.steps import SELECTION_GROUPS, StepKind, hook_name, steps_for_selection
from ddev_sync.errors import ConfigError
from ddev_sync.features.resolver import ActivatedFeature, FeatureResolution
from ddev_sync.operations.base import PathEscapesProject, preview_local_path
from ddev_sync.profiles.registry import ResolvedProfile
from ddev_sync.transport.ssh import rsync_pull_command, ssh_command

DUMP_FILENAME = "dump.sql"
DOWNLOAD_DIR = ".ddev/.downloads"

# A module-level singleton rather than constructing one in the signature:
# `FeatureResolution()` is immutable (every field is a tuple or a bool), so
# sharing this one instance across every caller that does not pass its own
# is safe - unlike the `[]` or `{}` this same rule exists to catch.
_NO_FEATURES = FeatureResolution()


@dataclass(frozen=True)
class PlannedStep:
    """One line of the plan: what runs, where, and which layer asked for it.

    `kind` always names the parent step, even for a hook - a `post_db_import`
    hook keeps `kind == StepKind.DB_IMPORT` so callers can still group a hook
    with the step it hangs off. `is_hook` is what tells a step from its
    hooks, since the parent's `kind` alone cannot: the renderer uses it to
    label the line "hook" instead of the kind, and `Executor._dispatch` uses
    it to route a hook's `params` (a command entry) through
    `run_command_entry` instead of looking `kind` up in `OPERATIONS` - which
    would otherwise run the *parent's* operation on the hook's params.
    """

    index: int
    kind: StepKind
    where: str
    detail: str
    source: str
    is_hook: bool = False
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostSyncEntry:
    """One `hooks.post_sync` command, shown in the dry run.

    Deliberately not a `PlannedStep`, even after `Executor._dispatch` learned
    to run an ordinary hook's command correctly: folding `post_sync` into
    `plan.steps` would put it under `Executor.run`'s `stopped` logic, which
    skips every remaining step once one has failed - exactly the one
    guarantee `post_sync` exists to have (it runs once after every step,
    success *or* failure, via the separate `Executor._run_post_sync`, which
    the main loop's stop-on-failure never reaches). It has no `index` among
    the numbered steps and no `kind` to group under for the same reason: it
    is not a step in that sequence at all.
    """

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
    post_sync: tuple[PostSyncEntry, ...] = ()
    features: tuple[ActivatedFeature, ...] = ()
    features_detection_enabled: bool = True
    features_disabled_reason: str = "~/.ddev/ddev-sync/config.yaml"


def _refuse_wp_cli_under_docker(target: ResolvedTarget, merged: MergeResult) -> None:
    """Refuse `remote_credentials: wp_cli` when the far side is an endpoint.

    wp-cli needs PHP and a WordPress installation, and the endpoint image
    carries neither - nor should it, or a container standing in production
    grows a language runtime to answer one question.

    Refused here rather than in `config/targets.py::_build` because only the
    plan sees the database block: a target knows where to connect and nothing
    about what will be read there.

    The message names `${WP_TABLE_PREFIX}` on purpose. `wp_cli` is also its
    only source, so a reader who learns no more than "use env instead" will
    reach for the placeholder next and fail deeper - after the dump, which is
    the expensive place to find out.
    """
    if target.provider is not Provider.DOCKER:
        return
    database = merged.data.get("database")
    source = database.get("remote_credentials") if isinstance(database, dict) else None
    if source != "wp_cli":
        return
    raise ConfigError(
        "database.remote_credentials: wp_cli cannot be used with provider: docker - "
        "wp-cli needs PHP and a WordPress installation, and the sync endpoint has "
        "neither. Use 'env' or 'explicit'. Note that wp_cli is also the only source "
        "of ${WP_TABLE_PREFIX}, so a sanitize rule naming that placeholder cannot be "
        "resolved under this provider either."
    )


def build_plan(
    *,
    target: ResolvedTarget,
    project: DdevProject,
    profile: ResolvedProfile,
    merged: MergeResult,
    steps: Sequence[StepKind],
    features: FeatureResolution = _NO_FEATURES,
    only_feature_setup: bool = False,
) -> Plan:
    """Expand the selected steps, with their hooks, into numbered plan entries.

    `only_feature_setup` is what makes `ddev sync build` mean "rebuild the
    frontend, nothing else" (design doc §6) instead of "run the whole setup
    step, including the profile's own composer install / migrate / cache
    commands, and also node-build". The provenance machinery already labels
    every entry with the layer that contributed it (`[feature:node-build]`
    vs `[profile:laravel]`), so this filters on that label rather than
    inventing a second way to ask "did a feature contribute this" - an entry
    is dropped only if it belongs to the SETUP step and was not contributed
    by a feature layer.
    """
    _refuse_wp_cli_under_docker(target, merged)
    builder = _Builder(target=target, project=project, merged=merged, steps=steps)
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

    if only_feature_setup:
        entries = [
            entry
            for entry in entries
            if entry.kind is not StepKind.SETUP or entry.source.startswith("feature:")
        ]

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
        post_sync=_post_sync_preview(merged),
        features=features.activated,
        features_detection_enabled=features.detection_enabled,
        features_disabled_reason=features.disabled_reason,
    )


def available_selection_groups(
    *,
    target: ResolvedTarget,
    project: DdevProject,
    profile: ResolvedProfile,
    merged: MergeResult,
    features: FeatureResolution = _NO_FEATURES,
    only_feature_setup: bool = False,
) -> frozenset[str]:
    """Which selection groups would actually produce work for this project.

    Answered by building a throwaway plan for each group and seeing which
    kinds came out, rather than by a second set of rules about
    what a project contains. Building a plan is pure - nothing in this
    module runs a command or reads a file - so the probe costs nothing and
    cannot drift from what a real run would do, which a hand-written "does
    this project have S3" check eventually would.

    A group counts as available when *any* of its kinds appears, not all of
    them: `db` names four, and a profile that writes no env file (TYPO3's
    does not) still dumps and imports.

    The caller must pass the same `features` and `only_feature_setup` the
    real run will use. The probe answers "what would this run do", and a
    probe that did not see `only_feature_setup` (which `ddev sync build`
    sets) would call `setup` available on the strength of commands the run
    then filters out.
    """
    return frozenset(
        name
        for name, kinds in SELECTION_GROUPS.items()
        if _group_produces_work(
            name,
            kinds,
            target=target,
            project=project,
            profile=profile,
            merged=merged,
            features=features,
            only_feature_setup=only_feature_setup,
        )
    )


def _group_produces_work(
    name: str,
    kinds: Sequence[StepKind],
    *,
    target: ResolvedTarget,
    project: DdevProject,
    profile: ResolvedProfile,
    merged: MergeResult,
    features: FeatureResolution,
    only_feature_setup: bool,
) -> bool:
    """Plan one group on its own and say whether any of its kinds came out.

    Each group is planned separately because planning can raise for a group's
    own broken configuration (a `files.paths` entry with no `remote`, a hook
    with `mode` but no `commands`). Planned together, one group's error would
    fail a run that never selected it - `--steps db` would die over a typo in
    `files`. A group whose configuration is broken *is* configured, just
    wrongly, so it counts as available: it belongs in the menu, and the error
    surfaces where that group is really planned, with the message it has
    always had. Nothing is swallowed, only deferred to the place that owns it.

    One consequence is worth knowing before it surprises someone: a malformed
    `hooks.pre_sync` makes *every* group available. `pre_sync` is a global
    hook and attaches to PREFLIGHT, which is in `StepKind.ALWAYS` and so part
    of every group's throwaway plan, so each probe raises and each one is
    answered "available". It is self-limiting - the run then fails
    immediately with that hook's own error, whichever group was picked - but
    a full menu on a project that has work for one group is not what a reader
    of this function would predict.
    """
    try:
        plan = build_plan(
            target=target,
            project=project,
            profile=profile,
            merged=merged,
            steps=steps_for_selection([name]),
            features=features,
            only_feature_setup=only_feature_setup,
        )
    except (ConfigError, PathEscapesProject):
        return True
    return any(step.kind in kinds for step in plan.steps)


def _post_sync_preview(merged: MergeResult) -> tuple[PostSyncEntry, ...]:
    """Resolve `hooks.post_sync` through the same shared logic the executor
    uses, so a cleanup hook written as a bare list or a bare string (both
    valid per spec §4.5) shows up here exactly as it will actually run -
    rather than the dry run being unable to reveal that the executor's own
    hand-rolled shape check silently dropped it.
    """
    path = resolve_command_path(merged.data, ("hooks", "post_sync"))
    return tuple(
        PostSyncEntry(detail=_hook_detail(entry), source=source)
        for entry, source in command_entries_at(merged.data, merged.list_sources, path)
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
        self,
        *,
        target: ResolvedTarget,
        project: DdevProject,
        merged: MergeResult,
        steps: Sequence[StepKind],
    ) -> None:
        self._target = target
        self._project = project
        self._data = merged.data
        self._scalar_sources = merged.scalar_sources
        self._list_sources = merged.list_sources
        # Known up front, before any entry is built - unlike every other
        # step, PREFLIGHT must know whether SANITIZE was *also* selected for
        # this run, not just that PREFLIGHT itself always is (StepKind.ALWAYS).
        # The operation reads this from its own params rather than from the
        # Plan, which it never sees (engine/executor.py only ever passes a
        # step's own params to its operation).
        self._sanitize_selected = StepKind.SANITIZE in steps
        # Read from the operation's own params for the same reason
        # `_sanitize_selected` is: the operation never sees the Plan.
        self._db_selected = StepKind.DB_DUMP in steps

    def step(self, kind: StepKind) -> list[_Entry]:
        handlers = {
            StepKind.PREFLIGHT: self._preflight,
            StepKind.DB_DUMP: self._db_dump,
            StepKind.DB_DOWNLOAD: self._db_download,
            StepKind.DB_IMPORT: self._db_import,
            StepKind.ENV: self._env,
            StepKind.FILES_SYNC: self._files_sync,
            StepKind.S3_SYNC: self._s3_sync,
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
                # Every hook is a command entry, and a command entry runs in
                # the web container (operations/exec.py) - the same place its
                # `setup.commands` sibling runs, whatever step it hangs off.
                where="web",
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
        # operation), so that trailing element is dropped. A jump host's
        # `ProxyCommand` is one argument with spaces - and single quotes - in
        # it, so it is shown in double quotes; `shlex.join` would be just as
        # correct and unreadable.
        preview = ssh_command(self._target, ())[:-1]
        detail = " ".join(f'"{part}"' if " " in part else part for part in preview)
        return [
            _Entry(
                kind=StepKind.PREFLIGHT,
                where="remote",
                detail=detail,
                source="built-in",
                params={
                    "sanitize_selected": self._sanitize_selected,
                    "db_selected": self._db_selected,
                },
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
        # db.dump streams mysqldump straight into the local file over the
        # same ssh connection (DbDumpOperation), so there is no remote
        # dump.sql to fetch and no separate transfer ever runs here. The
        # step stays visible because the plan promised it; it just stops
        # pretending an scp happens.
        detail = f"streamed as part of db.dump into {DOWNLOAD_DIR}/{DUMP_FILENAME}"
        return [
            _Entry(
                kind=StepKind.DB_DOWNLOAD, where="-", detail=detail, source="built-in", params={}
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

    def _env(self) -> list[_Entry]:
        node = self._data.get("env", {})
        settings = node if isinstance(node, dict) else {}
        strategy = str(settings.get("strategy", "patch_keys"))
        if strategy == "none":
            return []
        raw_values = settings.get("set", {})
        raw_default = settings.get("default", {})
        if raw_default is not None and not isinstance(raw_default, dict):
            # `profiles/schema.py::_validate_env` rejects this at load, but it
            # only ever sees profile layers; a project's own `ddev-sync.yaml`
            # reaches here unvalidated. The operation would read it as no
            # default at all and carry on - silently, and after the database
            # import. Failing here puts the reason in the dry-run, before
            # anything is written, the same way the `env.file` check below
            # does. A bare `default:` is YAML null, which is nothing written
            # rather than something malformed, so it stays tolerated.
            raise ConfigError(
                f"env.default must be a mapping of keys to values, got {raw_default!r}"
            )
        # A non-mapping `set` is still no block, and the operation reads it so.
        imposed = raw_values if isinstance(raw_values, dict) else {}
        defaulted = raw_default or {}
        if not imposed and not defaulted:
            return []
        if defaulted and strategy == "append_block":
            # `append_block` writes a block rather than reconciling keys, so
            # "only if absent" has nowhere to land. A profile cannot reach this:
            # `profiles/schema.py::_validate_env` rejects it at load. Only a
            # project's own `ddev-sync.yaml` can, and the operation would raise
            # for it mid-sync; failing here puts the reason in the dry-run, the
            # same way the `env.file` check below does.
            raise ConfigError("env.default cannot be used with strategy 'append_block'")
        configured = settings.get("file", [".env.local", ".env"])
        if not isinstance(configured, str | list):
            # A bare `file:` is read by YAML as null, and anything else that is
            # neither a name nor a list of names would otherwise reach the
            # iteration below as a raw TypeError - a traceback instead of a
            # message, and now reachable on every run because the availability
            # probe plans the `db` group even when only `files` was asked for.
            raise ConfigError(
                f"env.file must be a file name or a list of file names, got {configured!r}"
            )
        candidates = [configured] if isinstance(configured, str) else [str(c) for c in configured]
        # An imposed empty string blanks the key rather than setting it, which a
        # bare name in the list reads as the opposite of (`DB_URL` is emptied so
        # that it cannot override the discrete `DB_*` keys). A key whose value
        # is anything else, `MAIL_URL`'s local DSN included, stays in the plain
        # list: naming it here would claim an emptying that does not happen.
        # The predicate reads the value as written in the configuration, while
        # `operations/env.py` writes it after placeholder expansion, so a value
        # that only expands to nothing would be listed as set. No shipped
        # profile has one; the limit is recorded here rather than closed.
        emptied = sorted(str(key) for key, value in imposed.items() if str(value) == "")
        set_keys = sorted(str(key) for key in imposed if str(key) not in emptied)
        # `set` wins a key declared in both, so such a default is never written
        # and is not worth naming.
        written_if_absent = sorted({str(key) for key in defaulted} - {str(key) for key in imposed})
        # Built piece by piece, so a profile with no emptied keys and no
        # defaults renders exactly the string it always has and the goldens of
        # every profile without either do not move.
        groups = [", ".join(set_keys)] if set_keys else []
        if emptied:
            groups.append(f"emptied: {', '.join(emptied)}")
        if written_if_absent:
            groups.append(f"if absent: {', '.join(written_if_absent)}")
        counts = f"{len(imposed)} key(s)"
        if written_if_absent:
            counts += f" + {len(written_if_absent)} if absent"
        detail = f"{strategy}: {counts} -> {', '.join(candidates)} ({'; '.join(groups)})"
        return [
            _Entry(
                kind=StepKind.ENV,
                where="local",
                detail=detail,
                source=self._source(("env", "strategy")),
                params={},
            )
        ]

    def _s3_sync(self) -> list[_Entry]:
        node = self._data.get("s3")
        if not isinstance(node, dict):
            return []
        remote = node.get("remote")
        local = node.get("local")
        if not isinstance(remote, dict) or not isinstance(local, dict):
            return []
        subcommand = "sync" if node.get("delete", False) else "copy"
        detail = f"rclone {subcommand} {remote.get('bucket', '?')} -> {local.get('bucket', '?')}"
        return [
            _Entry(
                kind=StepKind.S3_SYNC,
                where="rclone",
                detail=detail,
                source=self._source(("s3", "remote", "bucket")),
                params=dict(node),
            )
        ]

    def _files_sync(self) -> list[_Entry]:
        paths = self._list_at(("files", "paths"))
        sources = self._list_sources.get(("files", "paths"), ())
        entries: list[_Entry] = []
        for position, mapping in enumerate(paths):
            remote_suffix = _require_key(mapping, "remote")
            local_suffix = _require_key(mapping, "local")
            remote = f"{self._target.data_root}{remote_suffix}"
            local = f"{preview_local_path(self._project.root, local_suffix)}/"
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
        # The switches are read as `is True` here: the plan must not raise on a
        # malformed value (the sanitize operation itself refuses it, with the
        # proper message), and `all` wins over the personal-data switch just
        # as in `operations.sanitize.active_rules`.
        all_off = _node_at(self._data, ("sanitize", "disable_sanitize_all")) is True
        personal_off = _node_at(self._data, ("sanitize", "disable_sanitize")) is True
        if all_off:
            rules = []
        elif personal_off:
            rules = [rule for rule in rules if not _is_personal_data(rule)]
        names = ", ".join(str(rule.get("name", "unnamed")) for rule in rules)
        plural = "rule" if len(rules) == 1 else "rules"
        detail = f"{len(rules)} {plural}: {names}" if rules else "0 rules"
        if all_off:
            detail += " (all rules skipped: disable_sanitize_all)"
        elif personal_off:
            detail += " (personal-data rules skipped: disable_sanitize)"
        sources = self._list_sources.get(("sanitize", "rules"), ())
        return [
            _Entry(
                kind=StepKind.SANITIZE,
                where="sql",
                detail=detail,
                source=_source_at(sources, 0),
                params={},
            )
        ]

    def _setup(self) -> list[_Entry]:
        path = self._resolve_command_path(("setup",))
        return [
            _Entry(
                kind=StepKind.SETUP,
                where="web",
                detail=_hook_detail(entry),
                source=source,
                params=dict(entry),
            )
            for entry, source in self._command_entries_at(path)
        ]

    def _resolve_command_path(self, base_path: tuple[str, ...]) -> tuple[str, ...]:
        return resolve_command_path(self._data, base_path)

    def _list_at(self, path: tuple[str, ...]) -> list[dict[str, Any]]:
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict):
                return []
            node = node.get(part)
        if not isinstance(node, list):
            return []
        return [entry for entry in node if isinstance(entry, dict)]

    def _command_entries_at(self, path: tuple[str, ...]) -> list[tuple[dict[str, Any], str]]:
        return command_entries_at(self._data, self._list_sources, path)

    def _scalar_at(self, path: tuple[str, ...], *, default: str) -> str:
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict):
                return default
            node = node.get(part)
        return default if node is None else str(node)

    def _source(self, path: tuple[str, ...]) -> str:
        return self._scalar_sources.get(path, "built-in")


def _is_personal_data(rule: Mapping[str, Any]) -> bool:
    """Mirror of `SanitizeRule.is_personal_data`, on the raw config entry."""
    return rule.get("operation") == "fake" or rule.get("personal_data") is True


def _node_at(data: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def resolve_command_path(data: Mapping[str, Any], base_path: tuple[str, ...]) -> tuple[str, ...]:
    """Find where a hook or `setup`-shaped command list actually sits.

    Module-level (not a `_Builder` method) because the executor's
    `post_sync` resolution needs the identical logic against
    `context.merged.data` - a hand-rolled second shape check next to this one
    is exactly the kind of duplication spec §4.5's shapes have already
    caused once.

    A hook or `setup` block's merged shape depends on whether any layer wrote
    an explicit `mode:` alongside `commands:` (spec §4.5): with `mode`,
    `{mode, commands}` is a list directive and collapses to a flat list right
    at `base_path`; without it, `commands` is an ordinary nested key and the
    list sits one level deeper, as it always has.

    `mode` with no `commands` alongside it (typically a typo that dropped
    `commands:`) merges as ordinary data instead of a directive -
    `config/merge.py` is deliberately schema-agnostic and cannot tell the
    difference - and would otherwise disable the whole block with nobody
    told. This is the one place that does know the schema, so it is rejected
    here.
    """
    node = _node_at(data, base_path)
    if isinstance(node, list):
        return base_path
    if isinstance(node, dict) and "mode" in node and "commands" not in node:
        display = ".".join(base_path)
        valid = ", ".join(sorted(LIST_MODES))
        raise ConfigError(
            f"{display} has 'mode' but no 'commands' - probably a typo; valid modes: {valid}"
        )
    return (*base_path, "commands")


def command_entries_at(
    data: Mapping[str, Any],
    list_sources: Mapping[tuple[str, ...], tuple[str, ...]],
    path: tuple[str, ...],
) -> list[tuple[dict[str, Any], str]]:
    """Hook and setup command lists (spec §4.5) additionally accept a bare
    string as shorthand for a named built-in operation, e.g.
    `- ddev-sync:flush-cache` normalises to `{"operation": "ddev-sync:flush-cache"}`.
    Anything that is neither a string nor a mapping is malformed and is
    dropped rather than crashing the whole plan.
    """
    node = _node_at(data, path)
    if not isinstance(node, list):
        return []
    sources = list_sources.get(path, ())
    pairs: list[tuple[dict[str, Any], str]] = []
    for position, entry in enumerate(node):
        if isinstance(entry, str):
            pairs.append(({"operation": entry}, _source_at(sources, position)))
        elif isinstance(entry, dict):
            pairs.append((entry, _source_at(sources, position)))
    return pairs


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
