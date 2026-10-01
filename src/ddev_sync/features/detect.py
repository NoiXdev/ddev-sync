"""The closed vocabulary a feature may use to detect itself.

Small and closed in the spirit of the sanitize catalog: a feature
parameterises what exists. A sixth predicate is a decision with its own
review, not something added in passing.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ddev_sync.composer import read_packages
from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import FeatureError

PREDICATES = frozenset({"composer_package", "npm_package", "file_exists", "any_of", "all_of"})


def matches(predicate: Mapping[str, Any], project: DdevProject) -> bool:
    """True when `predicate` holds for `project`."""
    key, value = _single_key(predicate)
    if key == "composer_package":
        return _composer_has(project.root / "composer.lock", str(value))
    if key == "npm_package":
        return _npm_has(project.root / "package.json", str(value))
    if key == "file_exists":
        return _within_root(project, str(value)).exists()
    nested = _nested(key, value)
    if key == "any_of":
        return any(matches(entry, project) for entry in nested)
    return all(matches(entry, project) for entry in nested)


def validate_predicate(name: str, predicate: Any) -> None:
    """Validate a predicate's shape at load time without touching the filesystem.

    Raises FeatureError if the predicate is malformed, naming the feature.
    """
    if not isinstance(predicate, Mapping):
        raise FeatureError(f"feature {name!r}: detect must be a mapping")

    try:
        key, value = _single_key(predicate)
    except FeatureError as e:
        raise FeatureError(f"feature {name!r}: {e}") from e

    if key == "file_exists":
        if not isinstance(value, str) or not value:
            raise FeatureError(f"feature {name!r}: file_exists must be a non-empty string")
    elif key in ("any_of", "all_of"):
        try:
            nested = _nested(key, value)
        except FeatureError as e:
            raise FeatureError(f"feature {name!r}: {e}") from e
        for entry in nested:
            validate_predicate(name, entry)


def _single_key(predicate: Mapping[str, Any]) -> tuple[str, Any]:
    if len(predicate) != 1:
        raise FeatureError(
            f"a detect block holds exactly one predicate, found "
            f"{len(predicate)}: {', '.join(sorted(predicate)) or '(empty)'}. "
            f"Use any_of or all_of to combine them."
        )
    key, value = next(iter(predicate.items()))
    if key not in PREDICATES:
        raise FeatureError(
            f"unknown detection predicate {key!r}; expected one of {', '.join(sorted(PREDICATES))}"
        )
    return key, value


def _nested(key: str, value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(e, Mapping) for e in value):
        raise FeatureError(f"{key} takes a list of predicates")
    return list(value)


def _within_root(project: DdevProject, relative: str) -> Path:
    """Resolve `relative` under the project root, refusing to leave it."""
    if not relative:
        raise FeatureError(
            "file_exists path is empty; a feature must specify a file or directory to check"
        )
    root = project.root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise FeatureError(
            f"file_exists path {relative!r} resolves to {candidate}, outside "
            f"the project root {root}; a feature may only detect itself from "
            f"the project"
        )
    return candidate


def _composer_has(lock_path: Path, package: str) -> bool:
    entries = read_packages(lock_path)
    return any(isinstance(entry, dict) and entry.get("name") == package for entry in entries)


def _npm_has(package_json: Path, package: str) -> bool:
    document = _read_json(package_json)
    if not isinstance(document, dict):
        return False
    for section in ("dependencies", "devDependencies"):
        entries = document.get(section)
        if isinstance(entries, dict) and package in entries:
            return True
    return False


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
