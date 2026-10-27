"""Render a Plan as plain text.

Returning a string rather than writing to a console is deliberate: it makes the
output snapshot-testable, so a change to a profile shows up as a readable diff
in a golden file instead of being discovered in production.
"""

from collections.abc import Mapping

from ddev_sync.cli.colour import Colour, paint_padded
from ddev_sync.config.models import Provider, ResolvedTarget, TargetSpec
from ddev_sync.config.targets import CollectedTarget
from ddev_sync.docs import doc_url
from ddev_sync.engine.plan import Plan, PlannedStep
from ddev_sync.operations.preflight import PreflightReport

_KIND_WIDTH = 14
_WHERE_WIDTH = 7
_MARKER_WIDTH = 6

# A failing doctor row points at the docs entry for that probe. Keyed by the
# row label, because the rows are built from labels and nothing else carries
# which probe a row is. `ssh connection` and `sync endpoint` are the same
# probe under two names (see `render_doctor`).
_DOCTOR_ROW_TOPICS = {
    "ssh connection": "cannot-reach-the-host-over-ssh",
    "sync endpoint": "docker-endpoint",
    "deploy path": "deploy-path-does-not-exist",
    "path readable": "remote-path-is-not-readable",
    "disk space": "not-enough-free-space",
    "credentials": "database",
    "sanitize tables": "required-table-is-missing",
    "web container": "web-container-not-reachable",
    "ssh agent": "no-ssh-agent",
}
_FEATURES_LABEL = "  Features  "
_FEATURES_INDENT = " " * len(_FEATURES_LABEL)


def render_header(plan: Plan) -> str:
    """The two-line summary above any plan or run."""
    return (
        f"  Plan for target {plan.target.name!r}"
        f"{' ' * 8}{plan.profile_label}\n"
        f"  {plan.project_name}  -  {_endpoint(plan)}"
    )


def render_plan(plan: Plan) -> str:
    """The full dry-run output."""
    lines = [render_header(plan), "  Nothing will be executed.", ""]
    lines.extend(f"  ! {notice}" for notice in plan.notices)
    if plan.notices:
        lines.append("")

    features_block = render_features(plan)
    if features_block:
        lines.append(features_block)
        lines.append("")

    if not plan.steps:
        lines.append("  (no steps selected)")
    else:
        for step in plan.steps:
            lines.append(
                f"  {step.index:2d}  "
                f"{_kind_label(step):<{_KIND_WIDTH}}"
                f"{step.where:<{_WHERE_WIDTH}} "
                f"{step.detail}"
                f"   [{step.source}]"
            )
        lines.append("")
        lines.append(f"  Sources:  {_legend(plan)}")

    if plan.post_sync:
        # Always runs, success or failure - never a numbered step (see
        # `PostSyncEntry`) - so it is rendered as its own epilogue rather
        # than folded into the step table above.
        lines.append("")
        lines.append("  post_sync (always runs, regardless of outcome):")
        for entry in plan.post_sync:
            lines.append(f"        {entry.detail}   [{entry.source}]")

    return "\n".join(lines) + "\n"


def render_features(plan: Plan) -> str:
    """The block of active features, shown above the steps.

    Three outcomes, not two: features active (list them, one per line, each
    naming why); none active because nothing matched (render nothing - an
    empty heading would be noise on every ordinary run); none active because
    detection itself was switched off (say so once, so a user who expected
    `node-build` to show up learns why it did not, rather than concluding
    their project was not detected).
    """
    if not plan.features:
        if plan.features_detection_enabled:
            return ""
        # Names whichever kill switch (spec §2) actually disabled detection -
        # `--no-features` or the config file - rather than always blaming the
        # file, which is what this said even for a run disabled purely by the
        # flag, pointing at a file that need not exist.
        return f"{_FEATURES_LABEL}(detection disabled by {plan.features_disabled_reason})"
    width = max(len(activated.feature.name) for activated in plan.features)
    lines = [
        f"{_FEATURES_LABEL if index == 0 else _FEATURES_INDENT}"
        f"{activated.feature.name:<{width}}  {activated.reason}"
        for index, activated in enumerate(plan.features)
    ]
    return "\n".join(lines)


def _kind_label(step: PlannedStep) -> str:
    """Every line names the layer it came from (spec §9.3); a hook must also
    be visibly a hook rather than reading as a second copy of its parent step.
    """
    return "hook" if step.is_hook else step.kind.value


def _endpoint(plan: Plan) -> str:
    target = plan.target
    host = target.host if target.user is None else f"{target.user}@{target.host}"
    if target.port is None:
        return f"{host}  {target.path}"
    return f"{host}:{target.port}  {target.path}"


def _legend(plan: Plan) -> str:
    """Every source that contributed anything the plan will execute.

    A layer whose sole contribution is a `hooks.post_sync` command never
    becomes a `PlannedStep` - `post_sync` is rendered separately, as
    `plan.post_sync` (see `Plan`'s own docstring) - so walking `plan.steps`
    alone would silently drop it here: a step would run with no indication
    of where it came from. `plan.post_sync` is reachable from the same
    `Plan`, so it costs nothing to fold in.
    """
    seen: list[str] = []
    for step in plan.steps:
        if step.source not in seen:
            seen.append(step.source)
    for entry in plan.post_sync:
        if entry.source not in seen:
            seen.append(entry.source)
    return "   ".join(f"[{source}]" for source in seen)


def describe_endpoint(spec: TargetSpec) -> str:
    """One line saying where a target points, for a listing or a picker.

    A `deployer:` block is shown as the declaration it is. Resolving it would
    mean running `dep config` in the web container, so the listing would
    depend on the project's own `deploy.php` evaluating cleanly - and a
    broken recipe would then hide the very configuration needed to fix it.
    The resolution surfaces where work is already being done: the dry-run
    header, and `doctor`.
    """
    if spec.deployer is not None:
        alias = "" if spec.deployer.host is None else f": {spec.deployer.host}"
        return f"from deployer{alias}"
    host = spec.ssh_alias or spec.host or "?"
    user = "" if spec.ssh_alias or not spec.user else f"{spec.user}@"
    return f"{user}{host}:{spec.path or '?'}"


def render_targets(collected: Mapping[str, CollectedTarget]) -> str:
    """List every target, its endpoint, and the layer that defined it."""
    names = sorted(collected)
    if not names:
        return "  No sync targets are defined.\n  Run 'ddev sync init' to create one.\n"
    width = max(len(name) for name in names)
    lines = ["  Targets", ""]
    for name in names:
        endpoint = describe_endpoint(collected[name].spec)
        lines.append(f"  {name:<{width}}  {endpoint}   [{collected[name].source}]")
    return "\n".join(lines) + "\n"


def render_doctor(report: PreflightReport, target: ResolvedTarget, *, colour: bool = False) -> str:
    """Render the preflight report as a checklist.

    The credentials, sanitize-tables, web-container and ssh-agent rows are
    included only when their `*_ok` field is not `None` - that probe
    genuinely was not run (for credentials and sanitize alike: connectivity
    itself already failed, so there is no remote left to query; for sanitize
    specifically, also that no rule requires a table), and a row would
    otherwise show either a false OK or need a third marker of its own.

    `colour` paints the OK and FAIL markers, and nothing else, green and red.
    It is an argument rather than something this function works out so that
    the renderer stays a pure function of its inputs; the caller decides once
    (see `ddev_sync.cli.colour.colour_enabled`). It defaults to off so every
    caller that predates it, and every snapshot test, keeps its plain output.
    """
    # Under `provider: docker` the far side is a container inside the stack,
    # and the two rows that name it say so. When the endpoint is silent the
    # Docker host itself has usually answered perfectly well, so "web01
    # did not respond" would send a reader to debug the wrong machine; and
    # `path` is the path *inside* the endpoint, which is the one place a
    # reader's intuition from a server target is wrong.
    endpoint = target.provider is Provider.DOCKER
    connection_label = "sync endpoint" if endpoint else "ssh connection"
    path_detail = f"{target.path} (in the endpoint)" if endpoint else target.path
    checks = [
        # `ssh_detail` carries ssh's own reason when the connection failed;
        # the bare host is the fallback for a report that predates the field.
        (connection_label, report.ssh_ok, report.ssh_detail or target.host),
        ("deploy path", report.remote_path_ok, path_detail),
        (
            "disk space",
            report.free_space_ok,
            f"{report.free_bytes // 1024**3} GB free",
        ),
    ]
    if report.path_readable is not None:
        # Omitted when the probe never ran, as every other conditional row is.
        # A path that exists and cannot be listed is the uid mismatch a docker
        # endpoint makes likely, and it has always failed later and deeper on a
        # server target too.
        checks.append(
            (
                "path readable",
                report.path_readable,
                "" if report.path_readable else "exists but cannot be listed - check the uid",
            )
        )
    if report.credentials_ok is not None:
        checks.append(("credentials", report.credentials_ok, report.credentials_detail))
    if report.sanitize_tables_ok is not None:
        checks.append(("sanitize tables", report.sanitize_tables_ok, report.sanitize_tables_detail))
    if report.web_ok is not None:
        checks.append(("web container", report.web_ok, report.web_detail))
    if report.agent_ok is not None:
        checks.append(("ssh agent", report.agent_ok, report.agent_detail))
    lines = [f"  Doctor for target {target.name!r}", ""]
    for label, ok, detail in checks:
        # Width 6, not 4: "FAIL" alone is already 4 characters, so padding to
        # exactly its own length adds no separating space before the label -
        # the bug this replaced. Padding past the longest marker guarantees a
        # real gap for every marker, not just the shorter one.
        # The cell is padded before it is coloured (`paint_padded` does that
        # in the right order): the escape sequences are characters too, so
        # padding the coloured string would collapse this very gap.
        marker = (
            paint_padded("OK", _MARKER_WIDTH, Colour.GREEN, enabled=colour)
            if ok
            else paint_padded("FAIL", _MARKER_WIDTH, Colour.RED, enabled=colour)
        )
        lines.append(f"  {marker}{label:<18}{detail}")
        if not ok:
            # Under the label, not the marker: it belongs to the row's text.
            lines.append(f"  {' ' * _MARKER_WIDTH}More: {doc_url(_DOCTOR_ROW_TOPICS[label])}")
    return "\n".join(lines) + "\n"
