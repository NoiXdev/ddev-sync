"""Render a Plan as plain text.

Returning a string rather than writing to a console is deliberate: it makes the
output snapshot-testable, so a change to a profile shows up as a readable diff
in a golden file instead of being discovered in production.
"""

from collections.abc import Mapping

from ddev_sync.config.models import ResolvedTarget
from ddev_sync.config.targets import CollectedTarget
from ddev_sync.engine.plan import Plan, PlannedStep
from ddev_sync.operations.preflight import MIN_FREE_BYTES, PreflightReport

_KIND_WIDTH = 14
_WHERE_WIDTH = 7


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
    seen: list[str] = []
    for step in plan.steps:
        if step.source not in seen:
            seen.append(step.source)
    return "   ".join(f"[{source}]" for source in seen)


def render_targets(collected: Mapping[str, CollectedTarget]) -> str:
    """List every target, its endpoint, and the layer that defined it."""
    names = sorted(collected)
    if not names:
        return "  No sync targets are defined.\n  Run 'ddev sync init' to create one.\n"
    width = max(len(name) for name in names)
    lines = ["  Targets", ""]
    for name in names:
        spec = collected[name].spec
        host = spec.ssh_alias or spec.host or "?"
        user = "" if spec.ssh_alias or not spec.user else f"{spec.user}@"
        lines.append(
            f"  {name:<{width}}  {user}{host}:{spec.path or '?'}   [{collected[name].source}]"
        )
    return "\n".join(lines) + "\n"


def render_doctor(report: PreflightReport, target: ResolvedTarget) -> str:
    """Render the preflight report as a checklist."""
    checks = (
        ("ssh connection", report.ssh_ok, f"{target.host}"),
        ("deploy path", report.remote_path_ok, target.path),
        (
            "disk space",
            report.free_bytes >= MIN_FREE_BYTES,
            f"{report.free_bytes // 1024**3} GB free",
        ),
    )
    lines = [f"  Doctor for target {target.name!r}", ""]
    for label, ok, detail in checks:
        # Width 6, not 4: "FAIL" alone is already 4 characters, so padding to
        # exactly its own length adds no separating space before the label -
        # the bug this replaced. Padding past the longest marker guarantees a
        # real gap for every marker, not just the shorter one.
        marker = "OK" if ok else "FAIL"
        lines.append(f"  {marker:<6}{label:<18}{detail}")
    return "\n".join(lines) + "\n"
