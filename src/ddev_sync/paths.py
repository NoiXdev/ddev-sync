"""A tiny filesystem-path helper shared across the project.

Kept to one function so there is exactly one implementation of "prefer the
installed path, fall back to a checkout": `profiles/registry.py` and
`features/registry.py` each need the identical rule for their respective
`DEFAULT_PROFILE_ROOT`/`DEFAULT_FEATURE_ROOT` constants, and two copies of a
filesystem check that quietly drift apart is exactly this project's own
recurring defect (see `operations/base.py`'s `redact` for the same theme
applied to `operations/database.py` and `operations/preflight.py`).
"""

from pathlib import Path


def existing_or_fallback(primary: Path, fallback: Path) -> Path:
    """`primary` if it exists as a directory right now, else `fallback`.

    `primary` always wins when present - this is what keeps a container,
    where the installed path genuinely exists, from ever silently preferring
    a development checkout that happens to sit at the fallback path instead.
    """
    return primary if primary.is_dir() else fallback
