#!/usr/bin/env python3
"""Write one version into every file in this repository that names one.

Seven files carry a version and three different forms of it, which is why this
exists rather than a `sed` line in the release workflow:

- `pyproject.toml` feeds `importlib.metadata`, so it must hold a PEP 440
  version. `0.1.0-rc.1` is normalised to `0.1.0rc1` when the wheel is built,
  and `ddev-sync --version` prints the normalised form - so the file holds the
  normalised form to begin with, and nothing has to agree with a value it
  cannot see. The normalisation is not just "drop the separators": `beta` is
  `b`, `alpha` is `a`, and `pre`/`preview`/`c` are `rc`; an identifier with no
  PEP 440 spelling is refused before anything is written.
- `version.json` is what `compute-version` reads and what the release workflow
  writes; it holds the semver form, hyphen and all.
- `docker-compose.ddev-sync.yaml` and `ddev-sync/prepare-sidecar.sh` hold the
  same semver as the *image tag* default, and they have to agree with each
  other: the compose file resolves the sidecar's image, and the pre-start hook
  resolves the same image again to read `/etc/passwd` out of it. A default that
  names a tag nobody published does not fail at sync time - the project refuses
  to start at all.
- `endpoint/docker-compose.ddev-sync-endpoint.yaml` names the endpoint image's
  tag, in the same semver form and for the same reason: it is the template an
  operator copies into a production stack, and a tag nobody published is a
  stack that does not start.
- `README.md` quick start installs the add-on from a git tag, which is `v` +
  the semver (the release tags are `v0.1.0-rc.4`). Left alone it would name the
  previous release from the day after every release, and a reader following it
  would install the wrong version.
- `uv.lock` records the root project's own version, and `uv run` rewrites it to
  match `pyproject.toml` on sight, so it holds the PEP 440 form too; a release
  that bumped `pyproject.toml` alone would be followed by the next `uv run`
  anyone did producing a dirty tree.

Run it from anywhere; the targets are resolved from this file's location, not
from the working directory, because the release workflow's cwd is not a
promise. Every pattern must match exactly once or the script refuses to write
anything: every target is read and checked before the first one is written, so
a miss on the last file cannot leave the earlier ones rewritten. A pattern that
silently matched nothing is how a stale version reaches a published compose
default.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two shapes `compute-version` can produce, and nothing else.
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+\.\d+)?$")

# `-rc.1` -> `rc1`: PEP 440 allows the separators but normalises them away.
_PRERELEASE = re.compile(r"-(?P<identifier>[0-9A-Za-z]+)\.(?P<number>\d+)$")

# PEP 440's pre-release spellings and the one canonical form each normalises to.
# Anything else is not a pre-release in Python's eyes at all.
_PEP440_PHASES = {
    "a": "a",
    "alpha": "a",
    "b": "b",
    "beta": "b",
    "c": "rc",
    "pre": "rc",
    "preview": "rc",
    "rc": "rc",
}

Target = tuple[str, re.Pattern[str], Callable[[str], str]]


def pep440(version: str) -> str:
    """`version` in the form Python packaging will normalise it to anyway.

    Raises `ValueError` for a pre-release identifier PEP 440 has no spelling
    for, rather than inventing a string the wheel's metadata would disagree
    with.
    """
    match = _PRERELEASE.search(version)
    if match is None:
        return version
    identifier = match["identifier"]
    phase = _PEP440_PHASES.get(identifier.lower())
    if phase is None:
        raise ValueError(
            f"{identifier!r} is not a PEP 440 pre-release identifier "
            f"(expected one of {', '.join(_PEP440_PHASES)})"
        )
    return f"{version[: match.start()]}{phase}{match['number']}"


def _unchanged(version: str) -> str:
    return version


TARGETS: tuple[Target, ...] = (
    ("pyproject.toml", re.compile(r'(?m)^version = "(?P<version>[^"]+)"$'), pep440),
    ("version.json", re.compile(r'"version"\s*:\s*"(?P<version>[^"]+)"'), _unchanged),
    (
        "docker-compose.ddev-sync.yaml",
        re.compile(r"\$\{DDEV_SYNC_VERSION:-(?P<version>[^}]+)\}"),
        _unchanged,
    ),
    (
        "ddev-sync/prepare-sidecar.sh",
        re.compile(r"setting DDEV_SYNC_VERSION (?P<version>[^)]+)\)"),
        _unchanged,
    ),
    # Anchored on the image name rather than on `image:`, so the operator
    # comments around it can be rewritten without moving the target.
    (
        "endpoint/docker-compose.ddev-sync-endpoint.yaml",
        re.compile(r"ddev-sync-endpoint:(?P<version>[\w.+-]+)"),
        _unchanged,
    ),
    # Anchored on the whole command rather than on the version: a README is
    # prose, and another version-shaped string in it must never be rewritten.
    # The `v` sits outside the group, so the new version keeps its tag prefix.
    (
        "README.md",
        re.compile(r"(?m)^ddev add-on get NoiXdev/ddev-sync --version v(?P<version>[\w.+-]+)$"),
        _unchanged,
    ),
    # Anchored on the project's own block: every other package has a
    # `name`/`version` pair of its own.
    (
        "uv.lock",
        re.compile(r'name = "ddev-sync"\nversion = "(?P<version>[^"]+)"'),
        pep440,
    ),
)


def rewrite(version: str, root: Path = REPO_ROOT) -> list[Path]:
    """Write `version` into every target. Returns the files that changed."""
    if not _VERSION.match(version):
        raise ValueError(f"{version!r} is not a version (expected 1.2.3 or 1.2.3-rc.4)")
    # Two passes: resolve every replacement first, write only once all of them
    # have passed the exactly-once guard.
    resolved: list[tuple[Path, str, str]] = []
    for name, pattern, form in TARGETS:
        path = root / name
        text = path.read_text(encoding="utf-8")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError(
                f"{name}: expected exactly one version to rewrite, found {len(matches)}"
            )
        start, end = matches[0].span("version")
        resolved.append((path, text, text[:start] + form(version) + text[end:]))
    changed: list[Path] = []
    for path, text, new in resolved:
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(path)
    return changed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: tools/set_version.py <version>", file=sys.stderr)
        return 2
    try:
        changed = rewrite(args[0])
    except (ValueError, OSError) as error:
        print(f"set_version: {error}", file=sys.stderr)
        return 1
    for path in changed:
        print(f"set {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
