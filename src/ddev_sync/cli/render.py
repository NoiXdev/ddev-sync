"""Render a Plan as plain text.

Returning a string rather than writing to a console is deliberate: it makes the
output snapshot-testable, so a change to a profile shows up as a readable diff
in a golden file instead of being discovered in production.
"""

from ddev_sync.engine.plan import Plan, PlannedStep

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
        return "\n".join(lines) + "\n"

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
    return "\n".join(lines) + "\n"


def _kind_label(step: PlannedStep) -> str:
    """Every line names the layer it came from (spec §9.3); a hook must also
    be visibly a hook rather than reading as a second copy of its parent step.
    """
    return "hook" if step.is_hook else step.kind.value


def _endpoint(plan: Plan) -> str:
    target = plan.target
    host = target.host if target.user is None else f"{target.user}@{target.host}"
    port = "" if target.port is None else str(target.port)
    return f"{host}:{port}{target.path}"


def _legend(plan: Plan) -> str:
    seen: list[str] = []
    for step in plan.steps:
        if step.source not in seen:
            seen.append(step.source)
    return "   ".join(f"[{source}]" for source in seen)
