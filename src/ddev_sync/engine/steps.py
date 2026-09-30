"""The fixed vocabulary of sync steps and how a user selection maps onto them."""

from collections.abc import Sequence
from enum import StrEnum


class StepKind(StrEnum):
    """Every unit of work the engine knows how to schedule."""

    PREFLIGHT = "preflight"
    DB_DUMP = "db.dump"
    DB_DOWNLOAD = "db.download"
    DB_IMPORT = "db.import"
    FILES_SYNC = "files.sync"
    SANITIZE = "sanitize"
    SETUP = "setup"


STEP_ORDER: tuple[StepKind, ...] = (
    StepKind.PREFLIGHT,
    StepKind.DB_DUMP,
    StepKind.DB_DOWNLOAD,
    StepKind.DB_IMPORT,
    StepKind.FILES_SYNC,
    StepKind.SANITIZE,
    StepKind.SETUP,
)

ALWAYS: frozenset[StepKind] = frozenset({StepKind.PREFLIGHT})

SELECTION_GROUPS: dict[str, tuple[StepKind, ...]] = {
    "db": (StepKind.DB_DUMP, StepKind.DB_DOWNLOAD, StepKind.DB_IMPORT),
    "files": (StepKind.FILES_SYNC,),
    "sanitize": (StepKind.SANITIZE,),
    "setup": (StepKind.SETUP,),
}


def hook_name(kind: StepKind, phase: str) -> str:
    """The configuration key for a hook, e.g. `post_db_import`."""
    return f"{phase}_{kind.value.replace('.', '_')}"


def steps_for_selection(selection: Sequence[str]) -> tuple[StepKind, ...]:
    """Expand a user selection into steps, in canonical execution order."""
    chosen = set(ALWAYS)
    for name in selection:
        group = SELECTION_GROUPS.get(name)
        if group is None:
            valid = ", ".join(sorted(SELECTION_GROUPS))
            raise ValueError(f"unknown step {name!r}; choose from: {valid}")
        chosen.update(group)
    return tuple(kind for kind in STEP_ORDER if kind in chosen)
