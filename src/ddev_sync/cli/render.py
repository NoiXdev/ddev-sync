"""Render a Plan as plain text.

Returning a string rather than writing to a console is deliberate: it makes the
output snapshot-testable, so a change to a profile shows up as a readable diff
in a golden file instead of being discovered in production.
"""

from ddev_sync.engine.plan import Plan

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
            f"{step.kind.value:<{_KIND_WIDTH}}"
            f"{step.where:<{_WHERE_WIDTH}} "
            f"{step.detail}"
        )

    lines.append("")
    lines.append(f"  Sources:  {_legend(plan)}")
    return "\n".join(lines) + "\n"


def _endpoint(plan: Plan) -> str:
    target = plan.target
    host = target.host if target.user is None else f"{target.user}@{target.host}"
    return f"{host}:{target.path}"


def _legend(plan: Plan) -> str:
    seen: list[str] = []
    for step in plan.steps:
        if step.source not in seen:
            seen.append(step.source)
    return "   ".join(f"[{source}]" for source in seen)
