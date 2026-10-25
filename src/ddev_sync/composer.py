"""Read a project's composer files: the lock's package entries, and what composer.json requires.

Neutral module: it needs only `Path` and basic JSON parsing. Both `config/detect.py`
(to find a framework version) and `features/detect.py` (to detect feature presence)
need to read `composer.lock`. Two implementations would drift - a change to the file
format or a bug fix in one place would be missed in the other. This module is the
single source of truth, the way `operations/placeholders.py` is for placeholder
substitution. Both callers depend on it; it depends on neither.
"""

import json
from pathlib import Path
from typing import Any


def read_packages(lock_path: Path) -> list[dict[str, Any]]:
    """Return all packages from composer.lock (both 'packages' and 'packages-dev').

    Returns an empty list if the file is missing, cannot be parsed, or does not
    contain the expected structure. Does not raise; invalid input is treated as
    "no packages found".
    """
    if not lock_path.is_file():
        return []

    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []

    if not isinstance(document, dict):
        return []

    entries = []
    for section in ("packages", "packages-dev"):
        section_entries = document.get(section)
        if isinstance(section_entries, list):
            entries.extend(section_entries)

    return entries


def requires(composer_json: Path, package: str) -> bool:
    """Whether composer.json lists `package` in `require` or `require-dev`.

    False, not an error, for a file that is missing or unreadable - the same
    rule `read_packages` follows.
    """
    if not composer_json.is_file():
        return False

    try:
        document = json.loads(composer_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False

    if not isinstance(document, dict):
        return False

    return any(
        isinstance(document.get(section), dict) and package in document[section]
        for section in ("require", "require-dev")
    )
