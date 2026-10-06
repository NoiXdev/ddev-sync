"""The fixed vocabulary of sync steps and how a user selection maps onto them."""

from collections.abc import Sequence
from enum import StrEnum


class StepKind(StrEnum):
    """Every unit of work the engine knows how to schedule."""

    PREFLIGHT = "preflight"
    DB_DUMP = "db.dump"
    DB_DOWNLOAD = "db.download"
    DB_IMPORT = "db.import"
    ENV = "env"
    FILES_SYNC = "files.sync"
    S3_SYNC = "s3.sync"
    SANITIZE = "sanitize"
    SETUP = "setup"


STEP_ORDER: tuple[StepKind, ...] = (
    StepKind.PREFLIGHT,
    StepKind.DB_DUMP,
    StepKind.DB_DOWNLOAD,
    StepKind.DB_IMPORT,
    # ENV must run after DB_IMPORT and before FILES_SYNC: it points the local
    # app at the freshly imported database, so anything that could read the
    # app's configuration must not run before the database it describes is
    # actually in place.
    StepKind.ENV,
    StepKind.FILES_SYNC,
    StepKind.S3_SYNC,
    StepKind.SANITIZE,
    StepKind.SETUP,
)

# What the default output shows instead of a command line. Kept beside the
# enum rather than in the reporter so that adding a kind without describing it
# fails in the module that defines it - the reporter would only print a blank
# column. The text is English like every other string this tool prints; there
# is no i18n layer.
STEP_DESCRIPTIONS: dict[StepKind, str] = {
    StepKind.PREFLIGHT: "checking the connection, paths and free space",
    StepKind.DB_DUMP: "dumping the remote database",
    StepKind.DB_DOWNLOAD: "fetching the dump",
    StepKind.DB_IMPORT: "replacing the local database",
    StepKind.ENV: "updating the local env file",
    StepKind.FILES_SYNC: "syncing files from the remote",
    StepKind.S3_SYNC: "syncing the S3 bucket",
    StepKind.SANITIZE: "removing production data",
    StepKind.SETUP: "running the project's setup commands",
}

ALWAYS: frozenset[StepKind] = frozenset({StepKind.PREFLIGHT})

SELECTION_GROUPS: dict[str, tuple[StepKind, ...]] = {
    # ENV rides along with the database group: an env rewrite with no fresh
    # database behind it just points the local app at a stale (or absent)
    # one, so it makes no sense to select independently of "db".
    "db": (StepKind.DB_DUMP, StepKind.DB_DOWNLOAD, StepKind.DB_IMPORT, StepKind.ENV),
    "files": (StepKind.FILES_SYNC,),
    # Deliberately its own group, and deliberately absent from
    # `cli.prompts.DEFAULT_STEPS`: no shipped profile uses S3, and pulling a
    # whole media bucket is a deliberate act a project opts into, not
    # something that should be ticked by default.
    "s3": (StepKind.S3_SYNC,),
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
