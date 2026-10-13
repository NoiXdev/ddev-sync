# Docker Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the engine image to Docker Hub and cut the public pre-release
`v0.1.0-rc.1`, through a reusable workflow owned by `NoiXdev/github-workflows`
rather than a fourth copy of a release script.

**Architecture:** A new reusable workflow in `NoiXdev/github-workflows` carries
the release: compute the version, write it into the tree, build and push a
multi-architecture image, commit, tag, release, announce. ddev-sync calls it
from a dispatch-driven `release.yaml` whose `release` job declares
`needs: [gates, verify]`, so the CI gates and the whole verification matrix run
on the ref being released before anything reaches a registry. A repo-local
`tools/set_version.py` keeps the five places that name a version in step, and a
test in the ordinary gate fails when they drift.

**Tech Stack:** GitHub Actions (reusable workflows, composite actions),
`docker/build-push-action` with QEMU for `linux/arm64`, Python 3.13 standard
library only for `tools/set_version.py`, pytest, ruff, mypy, uv.

**Spec:** [docs/specs/2026-10-13-docker-release-pipeline-design.md](../specs/2026-10-13-docker-release-pipeline-design.md)

Two repositories are touched. Unless a task says otherwise, paths are relative
to `/Users/noidee/_dev/ddev-sync`. Task 2 works in
`/Users/noidee/_dev/github-workflows`.

## Global Constraints

- **100 % line AND branch coverage on `src/ddev_sync`.** `pyproject.toml` sets
  `--cov-fail-under=100` in `addopts` and `branch = true`.
- **`# pragma: no cover` must not work.** `[tool.coverage.report]`'s
  `exclude_lines` is emptied on purpose. Never add one, never weaken the gate
  with a flag.
- `tools/` is **not** measured by coverage (`source = ["src/ddev_sync"]`) but
  **is** linted and type-checked.
- **ruff:** `line-length = 100`, `select = ["E", "F", "I", "UP", "B", "SIM", "RUF", "ANN"]`,
  `ignore = ["ANN401"]`, `extend-exclude = ["docs"]`.
- **mypy:** `strict = true` over `src/ddev_sync`, `tests`, `tools`.
- `addopts` already carries `-q` and `-m 'not integration and not matrix'`. A
  single `-v` is cancelled by that `-q`; use `-vv` when you want per-test lines.
- **`tools/set_version.py` uses only the standard library.** The release runner
  invokes it with no dependency install.
- **Conventional Commits**, and every commit ends with the footer
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Everything code-side in English:** code, comments, commit messages, YAML
  comments, test names.
- **Commit, never push.** Pushing and merging are the repository owner's
  decisions, in both repositories.
- **Every third-party action is pinned to a full commit SHA with its concrete
  version in a trailing comment.** `NoiXdev/github-workflows/actions/...@main`
  self-references stay floating, as that repository's README says they
  deliberately do.
- The pins to use, each already in use in `NoiXdev/github-workflows` or
  resolved against `git/matching-refs/tags`:

```
actions/checkout@11d5960a326750d5838078e36cf38b85af677262          # v4.4.0
actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020        # v4.4.0
TriPSs/conventional-changelog-action@ee43def3241429afddab215ba89090cbf64fb45c  # v6.4.0
softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65          # v2.6.2
docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1  # v4.4.0
docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1
docker/login-action@dbcb813823bdd20940b903addbd779551569679f       # v4.6.0
docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302    # v6.2.0
docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc  # v7.4.0
```

- **`compute-version` produces exactly two version shapes:** `X.Y.Z` and
  `X.Y.Z-<id>.<N>`. Every regex, conversion and assertion in this plan is as
  narrow as that, deliberately.

## Review Focus

Five conditions the spec implies and whose absence would bite someone. Each one
has a test in the task that owns the code.

1. **A pre-release version must survive every rewrite target.** A pattern
   anchored to `\d+\.\d+\.\d+` would reject `0.1.0-rc.1` and kill the release
   after the tag was already computed. → Task 1, Step 7.
2. **`pyproject.toml` cannot hold the semver form.** PEP 440 normalises
   `0.1.0-rc.1`, so `--version` would not print what the tag says. → Task 1,
   Steps 1 and 7.
3. **The script must work from any working directory.** The release runner's cwd
   is not a guarantee; resolving targets from `__file__` is. → Task 1, Step 12.
4. **A pattern matching zero or two places must abort naming the file.** A
   silent miss is exactly how a stale version reaches a published compose
   default, and that makes a freshly installed project refuse to start. →
   Task 1, Step 10, proven by mutation in Step 14.
5. **The coherence test must actually fail on drift.** A test that cannot fail
   is decoration. → Task 1, Step 18, by mutation.

---

## File Structure

**ddev-sync**

| File | Responsibility |
|---|---|
| `tools/set_version.py` (new) | Write one version into every file that names one, in the form each file needs |
| `tests/test_set_version.py` (new) | Unit tests for that script, including every failure mode |
| `tests/test_version_coherence.py` (new) | Fail when the real files disagree |
| `.github/workflows/release.yaml` (new) | The caller: gates, matrix, shared release, per-arch smoke |
| `.github/workflows/ci.yaml` (modify) | Gains a `workflow_call` trigger so `release.yaml` can gate on it |
| `pyproject.toml:7`, `version.json`, `docker-compose.ddev-sync.yaml:17`, `ddev-sync/prepare-sidecar.sh:230` (modify) | Carry the version; rewritten by the script |
| `README.md:15-40`, `install.yaml:96-112`, `ddev-sync/prepare-sidecar.sh:256`, `profiles/VERIFIED.md` (modify) | Stop saying there is no published image; record what arm64 means |

**github-workflows** (branch `feat/docker-release-workflow`, based on `chore/pin-actions`)

| File | Responsibility |
|---|---|
| `.github/workflows/create_docker_changelog_version_release.yaml` (new) | The reusable release |
| `blueprints/release-docker.yaml` (new) | The caller vorlage, beside the other three blueprints |
| `README.md` (modify) | Document the workflow where the others are documented |

---

## Task 1: Version propagation and its coherence test

**Files:**
- Create: `tools/set_version.py`
- Create: `tests/test_set_version.py`
- Create: `tests/test_version_coherence.py`
- Modify: `pyproject.toml:7`, `version.json`, `docker-compose.ddev-sync.yaml:17`, `ddev-sync/prepare-sidecar.sh:230` (by running the script, in the final steps)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, and Tasks 2 and 3 rely on these exact names:
  - `tools/set_version.py`, executable, with a `#!/usr/bin/env python3`
    shebang, invoked as `./tools/set_version.py <version>` from any directory.
  - `REPO_ROOT: Path` — the repository root, resolved from `__file__`.
  - `TARGETS: tuple[Target, ...]` where
    `Target = tuple[str, re.Pattern[str], Callable[[str], str]]`: the path
    relative to `REPO_ROOT`, a pattern with a single named group `version`, and
    the function converting the semver into the form that file needs.
  - `pep440(version: str) -> str` — `0.1.0-rc.1` → `0.1.0rc1`; a final version
    passes through unchanged.
  - `rewrite(version: str, root: Path = REPO_ROOT) -> list[Path]` — the files
    whose content changed.
  - `main(argv: list[str] | None = None) -> int` — `0` success, `1` a refused
    version or an unrewritable file, `2` wrong usage.

- [ ] **Step 1: Measure what PEP 440 does to a pre-release version, before writing code that assumes it**

This settles Review Focus 2 by observation rather than by belief. Run from the
repository root:

```bash
cp pyproject.toml /tmp/pyproject.backup
python3 - <<'PY'
import pathlib, re
p = pathlib.Path("pyproject.toml")
p.write_text(re.sub(r'(?m)^version = "[^"]+"$', 'version = "0.1.0-rc.1"', p.read_text()))
PY
uv build --wheel --out-dir /tmp/pep440-probe 2>&1 | tail -3
ls /tmp/pep440-probe
cp /tmp/pyproject.backup pyproject.toml
rm -rf /tmp/pep440-probe /tmp/pyproject.backup
```

Expected: the wheel filename carries `0.1.0rc1`, not `0.1.0-rc.1`. Write the
observed filename into the commit message of Step 20 — it is the evidence for
the `pep440` conversion existing at all. If instead the build **fails**, record
that too: the conversion is then not an improvement but a necessity, and the
rest of this task is unchanged.

- [ ] **Step 2: Write the failing test for the four rewrite targets**

Create `tests/test_set_version.py`:

```python
import json
import stat
import subprocess
from pathlib import Path

import pytest

from tools.set_version import REPO_ROOT, main, pep440, rewrite

PYPROJECT = """\
[project]
name = "ddev-sync"
version = "0.1.0"
description = "unchanged"
"""

VERSION_JSON = '{\n  "version": "0.1.0"\n}\n'

COMPOSE = """\
services:
  sync:
    image: ${DDEV_SYNC_IMAGE:-noixdev/ddev-sync}:${DDEV_SYNC_VERSION:-0.1.0}
"""

SIDECAR = """\
image="$(setting DDEV_SYNC_IMAGE noixdev/ddev-sync):$(setting DDEV_SYNC_VERSION 0.1.0)"
"""


def build_tree(root: Path) -> None:
    """A miniature of the four files `set_version` rewrites."""
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "version.json").write_text(VERSION_JSON, encoding="utf-8")
    (root / "docker-compose.ddev-sync.yaml").write_text(COMPOSE, encoding="utf-8")
    (root / "ddev-sync").mkdir()
    (root / "ddev-sync" / "prepare-sidecar.sh").write_text(SIDECAR, encoding="utf-8")


def test_it_writes_the_version_into_every_target(tmp_path: Path) -> None:
    build_tree(tmp_path)

    changed = rewrite("0.2.0", root=tmp_path)

    assert sorted(path.name for path in changed) == [
        "docker-compose.ddev-sync.yaml",
        "prepare-sidecar.sh",
        "pyproject.toml",
        "version.json",
    ]
    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text()
    assert json.loads((tmp_path / "version.json").read_text())["version"] == "0.2.0"
    assert "DDEV_SYNC_VERSION:-0.2.0}" in (
        tmp_path / "docker-compose.ddev-sync.yaml"
    ).read_text()
    assert "DDEV_SYNC_VERSION 0.2.0)" in (
        tmp_path / "ddev-sync" / "prepare-sidecar.sh"
    ).read_text()
```

- [ ] **Step 3: Run it to watch it fail**

Run: `uv run pytest tests/test_set_version.py -vv`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.set_version'`.

- [ ] **Step 4: Write `tools/set_version.py`**

```python
#!/usr/bin/env python3
"""Write one version into every file in this repository that names one.

Five files carry a version and three different forms of it, which is why this
exists rather than a `sed` line in the release workflow:

- `pyproject.toml` feeds `importlib.metadata`, so it must hold a PEP 440
  version. `0.1.0-rc.1` is normalised to `0.1.0rc1` when the wheel is built,
  and `ddev-sync --version` prints the normalised form - so the file holds the
  normalised form to begin with, and nothing has to agree with a value it
  cannot see.
- `version.json` is what `compute-version` reads and what the release workflow
  writes; it holds the semver form, hyphen and all.
- `docker-compose.ddev-sync.yaml` and `ddev-sync/prepare-sidecar.sh` hold the
  same semver as the *image tag* default, and they have to agree with each
  other: the compose file resolves the sidecar's image, and the pre-start hook
  resolves the same image again to read `/etc/passwd` out of it. A default that
  names a tag nobody published does not fail at sync time - the project refuses
  to start at all.

Run it from anywhere; the targets are resolved from this file's location, not
from the working directory, because the release workflow's cwd is not a
promise. Every pattern must match exactly once or the script refuses to write
anything: a pattern that silently matched nothing is how a stale version
reaches a published compose default.
"""

import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two shapes `compute-version` can produce, and nothing else.
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+\.\d+)?$")

# `-rc.1` -> `rc1`: PEP 440 allows the separators but normalises them away.
_PRERELEASE = re.compile(r"-(?P<identifier>[0-9A-Za-z]+)\.(?P<number>\d+)$")

Target = tuple[str, re.Pattern[str], Callable[[str], str]]


def pep440(version: str) -> str:
    """`version` in the form Python packaging will normalise it to anyway."""
    return _PRERELEASE.sub(lambda match: f"{match['identifier']}{match['number']}", version)


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
)


def rewrite(version: str, root: Path = REPO_ROOT) -> list[Path]:
    """Write `version` into every target. Returns the files that changed."""
    if not _VERSION.match(version):
        raise ValueError(f"{version!r} is not a version (expected 1.2.3 or 1.2.3-rc.4)")
    changed: list[Path] = []
    for name, pattern, form in TARGETS:
        path = root / name
        text = path.read_text(encoding="utf-8")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError(
                f"{name}: expected exactly one version to rewrite, found {len(matches)}"
            )
        start, end = matches[0].span("version")
        new = text[:start] + form(version) + text[end:]
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
```

- [ ] **Step 5: Make it executable**

The release workflow runs `./tools/set_version.py` and refuses to continue when
the file is not executable, so the bit is part of the deliverable.

```bash
chmod +x tools/set_version.py
test -x tools/set_version.py && echo "executable"
```

- [ ] **Step 6: Run the test to watch it pass**

Run: `uv run pytest tests/test_set_version.py -vv`
Expected: PASS.

- [ ] **Step 7: Add the test for the two-form behaviour of a pre-release**

Append to `tests/test_set_version.py`:

```python
def test_a_prerelease_lands_as_semver_everywhere_but_pep440_in_pyproject(
    tmp_path: Path,
) -> None:
    build_tree(tmp_path)

    rewrite("0.1.0-rc.1", root=tmp_path)

    # pyproject feeds importlib.metadata, which only ever reports the
    # normalised form, so the file holds that form from the start.
    assert 'version = "0.1.0rc1"' in (tmp_path / "pyproject.toml").read_text()
    # The other three name an image tag and a release, where the hyphen stays.
    assert json.loads((tmp_path / "version.json").read_text())["version"] == "0.1.0-rc.1"
    assert "DDEV_SYNC_VERSION:-0.1.0-rc.1}" in (
        tmp_path / "docker-compose.ddev-sync.yaml"
    ).read_text()
    assert "DDEV_SYNC_VERSION 0.1.0-rc.1)" in (
        tmp_path / "ddev-sync" / "prepare-sidecar.sh"
    ).read_text()


def test_pep440_leaves_a_final_version_alone() -> None:
    assert pep440("1.2.3") == "1.2.3"
    assert pep440("0.1.0-rc.1") == "0.1.0rc1"
    assert pep440("0.1.0-beta.12") == "0.1.0beta12"
```

- [ ] **Step 8: Run it — this is Review Focus 1 and 2**

Run: `uv run pytest tests/test_set_version.py -vv -k prerelease or pep440`

Use this exact invocation, because `-k` takes an expression:

```bash
uv run pytest tests/test_set_version.py -vv -k "prerelease or pep440"
```

Expected: PASS, two tests.

- [ ] **Step 9: Add the idempotency test**

```python
def test_running_it_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    build_tree(tmp_path)

    assert rewrite("0.3.0", root=tmp_path) != []
    assert rewrite("0.3.0", root=tmp_path) == []
```

- [ ] **Step 10: Add the tests for every refusal**

```python
@pytest.mark.parametrize("bad", ["", "1.2", "v1.2.3", "1.2.3-rc", "1.2.3-rc.1-x", "latest"])
def test_it_refuses_anything_that_is_not_a_version(tmp_path: Path, bad: str) -> None:
    build_tree(tmp_path)
    before = (tmp_path / "pyproject.toml").read_text()

    with pytest.raises(ValueError, match="is not a version"):
        rewrite(bad, root=tmp_path)

    # Refused before anything was written: a partial rewrite is the one
    # outcome worse than no rewrite.
    assert (tmp_path / "pyproject.toml").read_text() == before


def test_it_refuses_a_file_whose_pattern_matches_nothing(tmp_path: Path) -> None:
    build_tree(tmp_path)
    (tmp_path / "version.json").write_text('{"nothing": "here"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="version.json: expected exactly one"):
        rewrite("0.4.0", root=tmp_path)


def test_it_refuses_a_file_whose_pattern_matches_twice(tmp_path: Path) -> None:
    build_tree(tmp_path)
    compose = tmp_path / "docker-compose.ddev-sync.yaml"
    compose.write_text(compose.read_text() + compose.read_text(), encoding="utf-8")

    with pytest.raises(ValueError, match="expected exactly one version to rewrite, found 2"):
        rewrite("0.4.0", root=tmp_path)


def test_it_refuses_a_missing_file(tmp_path: Path) -> None:
    build_tree(tmp_path)
    (tmp_path / "version.json").unlink()

    with pytest.raises(FileNotFoundError, match="version.json"):
        rewrite("0.4.0", root=tmp_path)
```

- [ ] **Step 11: Add the `main` tests**

```python
def test_main_reports_wrong_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert main(["1.0.0", "2.0.0"]) == 2
    assert "usage: tools/set_version.py" in capsys.readouterr().err


def test_main_reports_a_refused_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["nonsense"]) == 1
    assert "is not a version" in capsys.readouterr().err
```

`main(["nonsense"])` is refused by the version check before any file is read,
so it touches nothing in the real repository. Do **not** write a `main` test
that passes a valid version: it would rewrite this checkout's own files.

- [ ] **Step 12: Add the test that it does not depend on the working directory — Review Focus 3**

```python
def test_it_finds_its_targets_from_any_working_directory(tmp_path: Path) -> None:
    script = REPO_ROOT / "tools" / "set_version.py"
    assert script.stat().st_mode & stat.S_IXUSR, "the release workflow runs it directly"

    # A refused version proves the script started, parsed its argument and
    # reached its own check - from a directory that is not the repository.
    result = subprocess.run(
        [str(script), "nonsense"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "is not a version" in result.stderr
```

- [ ] **Step 13: Run the whole file**

Run: `uv run pytest tests/test_set_version.py -vv`
Expected: PASS, every test.

- [ ] **Step 14: Prove the refusals are load-bearing — Review Focus 4**

A test that cannot fail says nothing. Break the guard, watch the tests catch
it, put it back:

```bash
python3 - <<'PY'
import pathlib
p = pathlib.Path("tools/set_version.py")
p.write_text(p.read_text().replace("if len(matches) != 1:", "if False:"))
PY
uv run pytest tests/test_set_version.py -vv -k "matches" ; echo "exit=$?"
git checkout tools/set_version.py
chmod +x tools/set_version.py
```

Expected: the two `matches` tests FAIL while the guard is disabled, and the
file is restored afterwards. If they pass with the guard disabled, the tests are
wrong — fix them before continuing.

- [ ] **Step 15: Commit the tool and its tests**

```bash
git add tools/set_version.py tests/test_set_version.py
git commit -m "$(cat <<'EOF'
feat(tools): write one version into every file that names one

Five files carry a version in three forms, and until now nothing kept them
in step but a comment in docker-compose.ddev-sync.yaml warning that they
must be. The release pipeline needs that warning to have teeth: a compose
default naming an image tag nobody published does not fail at sync time, it
makes the project refuse to start.

pyproject.toml gets the PEP 440 form, because importlib.metadata only ever
reports the normalised one and `--version` would otherwise disagree with the
tag it shipped under. The other three get the semver form, hyphen and all,
because they name an image tag.

Every pattern must match exactly once or nothing is written. A pattern that
silently matched nothing is the failure this script exists to prevent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 16: Write the coherence test**

Create `tests/test_version_coherence.py`:

```python
"""The four files that name a version must name the same one.

`tools/set_version.py` writes them together, so they can only drift through a
hand edit - which is exactly what this test is for. It runs in the ordinary
gate, so the drift is red before it is pushed, rather than at the moment a
freshly installed project refuses to start.

Shaped after tests/test_no_legacy_terms.py: a repository-level test that reads
the real files rather than a fixture, because a fixture would agree with
itself while the repository drifted.
"""

from tools.set_version import REPO_ROOT, TARGETS, pep440


def _declared(name: str) -> str:
    pattern = next(pattern for target, pattern, _ in TARGETS if target == name)
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    matches = list(pattern.finditer(text))
    assert len(matches) == 1, f"{name}: {len(matches)} versions found, expected exactly one"
    return matches[0]["version"]


def test_every_file_that_names_an_image_tag_names_the_same_one() -> None:
    semver = {
        name: _declared(name)
        for name in (
            "version.json",
            "docker-compose.ddev-sync.yaml",
            "ddev-sync/prepare-sidecar.sh",
        )
    }

    assert len(set(semver.values())) == 1, f"versions disagree: {semver}"


def test_pyproject_carries_the_same_version_in_the_form_packaging_uses() -> None:
    assert _declared("pyproject.toml") == pep440(_declared("version.json"))
```

- [ ] **Step 17: Run it**

Run: `uv run pytest tests/test_version_coherence.py -vv`
Expected: PASS. Both files currently say `0.1.0`, and `pep440("0.1.0")` is
`0.1.0`.

- [ ] **Step 18: Prove the coherence test is load-bearing — Review Focus 5**

```bash
python3 - <<'PY'
import pathlib
p = pathlib.Path("docker-compose.ddev-sync.yaml")
p.write_text(p.read_text().replace("DDEV_SYNC_VERSION:-0.1.0}", "DDEV_SYNC_VERSION:-9.9.9}"))
PY
uv run pytest tests/test_version_coherence.py -vv ; echo "exit=$?"
git checkout docker-compose.ddev-sync.yaml
```

Expected: FAIL with `versions disagree:` naming the compose file, then the file
is restored. If it passes, the test is not reading what it claims to read.

- [ ] **Step 19: Commit the coherence test**

```bash
git add tests/test_version_coherence.py
git commit -m "$(cat <<'EOF'
test: fail when the files that name a version disagree

docker-compose.ddev-sync.yaml already warns in prose that its default and
the pre-start hook's must change together. The warning is correct and has no
teeth; this gives it some, in the ordinary gate rather than at install time.

Verified load-bearing by drifting the compose default to 9.9.9 and watching
it go red.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 20: Reset the version to `0.0.0`, with the script**

`compute-version` bumps before it appends a pre-release identifier, so
`0.1.0-rc.1` is unreachable from `0.1.0` — see the spec's decision 6. From
`0.0.0`, `bump: minor` with `prerelease_id: rc` computes exactly `0.1.0-rc.1`.

Using the script here, rather than editing four files by hand, is also the
first real exercise of the thing Step 15 just built.

```bash
./tools/set_version.py 0.0.0
git diff --stat
```

Expected: four files changed, each by one line.

- [ ] **Step 21: Run the whole gate**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Expected: every test passes, 100 % line and branch coverage, all three linters
silent. The coherence test passes because all four files now say `0.0.0`.

- [ ] **Step 22: Commit the reset**

```bash
git add pyproject.toml version.json docker-compose.ddev-sync.yaml ddev-sync/prepare-sidecar.sh
git commit -m "$(cat <<'EOF'
chore: set the version to 0.0.0 so the first release can be 0.1.0-rc.1

compute-version bumps before it appends a prerelease identifier, so from
0.1.0 the candidate would have to be 0.1.1-rc.1 - a number that asserts a
0.1.0 which was never published. From 0.0.0, a minor bump with id `rc`
computes 0.1.0-rc.1 exactly.

Written by tools/set_version.py rather than by hand, which is also its first
real use.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: The shared reusable Docker release workflow

**Repository:** `/Users/noidee/_dev/github-workflows` — a different repository.
Nothing in this task touches ddev-sync.

**Files:**
- Create: `.github/workflows/create_docker_changelog_version_release.yaml`
- Create: `blueprints/release-docker.yaml`
- Modify: `README.md` (the *Workflow Documentation* section)

**Interfaces:**
- Consumes: `tools/set_version.py` from Task 1 only as a *contract*: the
  workflow invokes `"./$VERSION_SCRIPT" "$VERSION"` and requires the file to be
  executable. It does not read it.
- Produces, and Task 3's caller relies on exactly these:
  - workflow path
    `NoiXdev/github-workflows/.github/workflows/create_docker_changelog_version_release.yaml@main`
  - inputs `bump`, `prerelease_id`, `prerelease`, `dry_run`, `platforms`,
    `version_file`, `version_script`, `product_name`, `release_files`
  - outputs `tag`, `version`, `is_prerelease`, `release_url`, `image_digest`

- [ ] **Step 1: Branch from the pin-actions branch, not from main**

That branch carries the pin convention this new file has to follow; basing on
`main` would either write an unpinned file or conflict on every shared line.

```bash
cd /Users/noidee/_dev/github-workflows
git status --short
git checkout chore/pin-actions
git checkout -b feat/docker-release-workflow
git log --oneline -1
```

Expected: a clean tree, and `8b05c4a` as the branch point. If the tree is dirty,
stop and report it — this repository is not yours to tidy.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/create_docker_changelog_version_release.yaml`:

```yaml
name: Create Docker Changelog + Version + Release

# Reusable, dispatch-driven release for a product that ships a container image.
# The caller picks a bump; this workflow computes the next version, writes it
# into the tree, builds and pushes the image, commits, tags, releases and
# announces.
#
# Three things are worth knowing before changing the order of the steps.
#
# The image is built AFTER the version is written, because a product whose
# binary or package metadata carries its version would otherwise ship an
# image claiming the previous one.
#
# The image is pushed BEFORE the commit and the tag. Neither order is atomic,
# so the question is which orphan is cheaper: a pushed image whose release
# failed is a tag nobody references and the same tag can be pushed again,
# while a pushed git tag whose image push failed is a tag in a public
# repository that others may already have fetched.
#
# A pre-release publishes exactly one image tag. `latest` and the truncated
# semver tags are all gated on the release not being one, because a candidate
# that moved `latest` would hand itself to everyone who pins nothing.
on:
  workflow_call:
    inputs:
      bump:
        description: "Version bump: patch, minor or major"
        required: true
        type: string
      prerelease_id:
        description: >
          Prerelease identifier (alpha, beta, rc). Empty produces a final
          version, which is what every existing caller gets. From 0.0.0 with
          bump=minor and id=rc comes 0.1.0-rc.1; calling again with the same
          id yields 0.1.0-rc.2; calling with an empty id promotes it to 0.1.0
          and ignores the bump.
        type: string
        default: ""
      prerelease:
        description: >
          Force the GitHub release to be marked as a pre-release. A version
          carrying a prerelease part marks itself, so this is only needed to
          flag an otherwise final version.
        type: boolean
        default: false
      dry_run:
        description: "Build everything but push nothing: no image, no commit, no tag, no release"
        type: boolean
        default: false
      platforms:
        description: >
          Comma-separated build platforms. The default is the single platform
          every existing caller builds today; naming a second one builds a
          multi-architecture image through QEMU.
        type: string
        default: linux/amd64
      version_file:
        description: >
          JSON file holding the current version. `compute-version` reads it
          with node, so it has to be JSON - a TOML or YAML manifest needs
          `version_script` below instead.
        type: string
        default: package.json
      version_script:
        description: >
          Executable in the repository, invoked as `./<script> <version>`,
          that writes the version into every other file naming one. Empty
          means only `version_file` is written, which is what a product with a
          single manifest wants.
        type: string
        default: ""
      product_name:
        description: >
          Name shown in the release notification and the image title. Empty
          uses the repository name.
        type: string
        default: ""
      release_files:
        description: "Paths attached to the GitHub release. Empty attaches nothing."
        type: string
        default: ""
    outputs:
      tag:
        description: "The created release tag (e.g. v0.1.0-rc.1)"
        value: ${{ jobs.release.outputs.tag }}
      version:
        description: "The released version, without the leading v"
        value: ${{ jobs.release.outputs.version }}
      is_prerelease:
        description: >
          "true" when the released version carries a prerelease part. Callers
          gate anything that must only see final releases on this.
        value: ${{ jobs.release.outputs.is_prerelease }}
      release_url:
        description: "URL of the GitHub release, empty on a dry run"
        value: ${{ jobs.release.outputs.release_url }}
      image_digest:
        description: "Digest of the pushed image, empty on a dry run"
        value: ${{ jobs.release.outputs.image_digest }}

permissions:
  contents: write

jobs:
  release:
    name: Create Docker Changelog + Version + Release
    runs-on: ubuntu-latest
    # A multi-architecture build under emulation is the slow part; the default
    # six hours would hold a runner for a whole working day if it hung.
    timeout-minutes: 90
    # Step outputs are job-local, so everything the announce job and the
    # caller need has to be re-exported here.
    outputs:
      tag: ${{ steps.ver.outputs.tag }}
      version: ${{ steps.ver.outputs.next }}
      is_prerelease: ${{ steps.ver.outputs.is-prerelease }}
      release_url: ${{ steps.gh_release.outputs.url }}
      image_digest: ${{ steps.build.outputs.digest }}
      notes: ${{ steps.changelog.outputs.clean_changelog }}
    steps:
      # First, so that a missing variable is named here instead of surfacing
      # as an authentication error from the registry halfway through.
      - name: Validate the Docker Hub configuration
        env:
          DOCKERHUB_NAMESPACE: ${{ vars.DOCKERHUB_NAMESPACE }}
          DOCKERHUB_IMAGE: ${{ vars.DOCKERHUB_IMAGE }}
          DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
          DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
        run: |
          : "${DOCKERHUB_NAMESPACE:?Variable DOCKERHUB_NAMESPACE is not set}"
          : "${DOCKERHUB_IMAGE:?Variable DOCKERHUB_IMAGE is not set}"
          : "${DOCKERHUB_USERNAME:?Secret DOCKERHUB_USERNAME is not set}"
          : "${DOCKERHUB_TOKEN:?Secret DOCKERHUB_TOKEN is not set}"

      - name: Checkout
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          # The changelog is built from the commits since the last tag, so a
          # shallow clone would see neither the tag nor most of the history.
          fetch-depth: 0
          ref: main
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Node
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        with:
          node-version: "22"

      - name: Compute the next version
        id: ver
        # Deliberately unpinned: it is this repository's own action, and the
        # README records why the self-references float.
        uses: NoiXdev/github-workflows/actions/compute-version@main
        with:
          bump: ${{ inputs.bump }}
          prerelease: ${{ inputs.prerelease_id }}
          version-file: ${{ inputs.version_file }}

      # Through the environment rather than interpolated into the script text,
      # for the reason `compute-version` gives for its own env block: `${{ }}`
      # is substituted before bash sees the line.
      - name: Write the version into the version file
        env:
          VERSION_FILE: ${{ inputs.version_file }}
          VERSION: ${{ steps.ver.outputs.next }}
        run: |
          set -euo pipefail
          node -e '
            const fs = require("fs");
            const file = process.env.VERSION_FILE;
            const data = JSON.parse(fs.readFileSync(file, "utf8"));
            data.version = process.env.VERSION;
            fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n");
          '

      - name: Write the version everywhere else
        if: ${{ inputs.version_script != '' }}
        env:
          VERSION_SCRIPT: ${{ inputs.version_script }}
          VERSION: ${{ steps.ver.outputs.next }}
        run: |
          set -euo pipefail
          if [ ! -x "./${VERSION_SCRIPT}" ]; then
            echo "::error::${VERSION_SCRIPT} is not an executable file in this repository"
            exit 1
          fi
          "./${VERSION_SCRIPT}" "${VERSION}"

      # What catches a version_script that silently did nothing, and a
      # version_file the script rewrote back to something else.
      - name: Verify the version is in sync
        env:
          VERSION_FILE: ${{ inputs.version_file }}
          VERSION: ${{ steps.ver.outputs.next }}
        run: |
          set -euo pipefail
          have="$(node -p "require('./' + process.env.VERSION_FILE).version")"
          if [ "${have}" != "${VERSION}" ]; then
            echo "::error::${VERSION_FILE} says ${have}, the release is ${VERSION}"
            exit 1
          fi
          echo "${VERSION_FILE} says ${have} - in sync"

      - name: Generate CHANGELOG.md
        id: changelog
        # Text only: the version is owned by the steps above, so every skip-*
        # is on to stop this action from bumping, committing or tagging.
        uses: TriPSs/conventional-changelog-action@ee43def3241429afddab215ba89090cbf64fb45c # v6.4.0
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          preset: conventionalcommits
          tag-prefix: "v"
          output-file: CHANGELOG.md
          release-count: 0
          skip-commit: "true"
          skip-tag: "true"
          skip-bump: "true"
          skip-version-file: "true"
          skip-on-empty: "false"

      # Only needed when `platforms` names something the runner is not, but
      # setting it up unconditionally costs seconds and removes a conditional
      # whose failure mode is an unexplained "exec format error".
      - name: Set up QEMU
        uses: docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1 # v4.4.0

      - name: Set up Buildx
        uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1

      - name: Log in to Docker Hub
        if: ${{ inputs.dry_run == false }}
        uses: docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0
        with:
          registry: docker.io
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Compute the image tags
        id: meta
        uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6.2.0
        with:
          images: docker.io/${{ vars.DOCKERHUB_NAMESPACE }}/${{ vars.DOCKERHUB_IMAGE }}
          # `{{version}}` always; everything that moves is gated on this not
          # being a pre-release, derived from the computed version as well as
          # from the input so the two cannot disagree.
          tags: |
            type=semver,pattern={{version}},value=${{ steps.ver.outputs.tag }}
            type=raw,value=latest,enable=${{ inputs.prerelease == false && steps.ver.outputs.is-prerelease != 'true' }}
            type=semver,pattern={{major}}.{{minor}},value=${{ steps.ver.outputs.tag }},enable=${{ inputs.prerelease == false && steps.ver.outputs.is-prerelease != 'true' }}
            type=semver,pattern={{major}},value=${{ steps.ver.outputs.tag }},enable=${{ inputs.prerelease == false && steps.ver.outputs.is-prerelease != 'true' }}
          labels: |
            org.opencontainers.image.title=${{ inputs.product_name || github.event.repository.name }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.version=${{ steps.ver.outputs.next }}

      - name: Build and push the image
        id: build
        uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0
        with:
          context: .
          platforms: ${{ inputs.platforms }}
          push: ${{ inputs.dry_run == false }}
          provenance: mode=max
          sbom: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Commit the version and the changelog, then tag
        if: ${{ inputs.dry_run == false }}
        env:
          GIT_AUTHOR_NAME: noix-dev-bot
          GIT_AUTHOR_EMAIL: noix-dev-bot@noix.dev
          GIT_COMMITTER_NAME: noix-dev-bot
          GIT_COMMITTER_EMAIL: noix-dev-bot@noix.dev
          TAG: ${{ steps.ver.outputs.tag }}
        run: |
          set -euo pipefail
          # -A rather than a fixed list: `version_script` writes files at paths
          # this workflow cannot predict, and a version written but left
          # uncommitted is the worst of both outcomes.
          git add -A
          if git diff --staged --quiet; then
            echo "Nothing to commit."
          else
            # [skip ci] so the release commit does not start another CI run.
            git commit -m "chore(release): ${TAG} [skip ci]"
            git push origin HEAD:main
          fi
          git tag -a "${TAG}" -m "${TAG}"
          git push origin "${TAG}"

      - name: Create the GitHub release
        id: gh_release
        if: ${{ inputs.dry_run == false }}
        uses: softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65 # v2.6.2
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          tag_name: ${{ steps.ver.outputs.tag }}
          name: ${{ steps.ver.outputs.tag }}
          body: ${{ steps.changelog.outputs.clean_changelog }}
          # Either the caller asked for it, or the version says so. Derived
          # rather than trusted, so a release cannot be marked final while
          # carrying a candidate version.
          prerelease: ${{ inputs.prerelease || steps.ver.outputs.is-prerelease == 'true' }}
          files: ${{ inputs.release_files }}

      - name: Summary
        run: |
          {
            echo "### Release ${{ steps.ver.outputs.tag }}"
            echo ""
            echo "| Field | Value |"
            echo "| --- | --- |"
            echo "| Previous version | \`${{ steps.ver.outputs.current }}\` |"
            echo "| New version | \`${{ steps.ver.outputs.next }}\` |"
            echo "| Bump type | \`${{ inputs.bump }}\` |"
            echo "| Prerelease | \`${{ steps.ver.outputs.is-prerelease }}\` |"
            echo "| Platforms | \`${{ inputs.platforms }}\` |"
            echo "| Dry run | \`${{ inputs.dry_run }}\` |"
            echo "| Digest | \`${{ steps.build.outputs.digest }}\` |"
            echo ""
            echo "**Image tags:**"
            echo '```'
            echo "${{ steps.meta.outputs.tags }}"
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"

  announce:
    name: Announce the release
    needs: [release]
    # No `always()`: when the release job fails nothing shipped, so a skipped
    # announcement is the correct outcome. Dry runs create no release either.
    if: ${{ inputs.dry_run == false }}
    runs-on: ubuntu-latest
    steps:
      - name: Post to Discord
        # A notification is the least important thing this workflow does and
        # must never fail the release.
        continue-on-error: true
        uses: NoiXdev/github-workflows/actions/announce-release@main
        with:
          webhook: ${{ secrets.DISCORD_WEBHOOK_RELEASE_NOTIFICATION }}
          tag: ${{ needs.release.outputs.tag }}
          body: ${{ needs.release.outputs.notes }}
          url: ${{ needs.release.outputs.release_url }}
          product-name: ${{ inputs.product_name }}
```

- [ ] **Step 3: Check the YAML parses, and that every third-party action is pinned**

There is no test framework for a workflow file, so these two checks are what
stands between a typo and a failed release. The first borrows ddev-sync's
interpreter, since this repository has none of its own.

```bash
cd /Users/noidee/_dev/ddev-sync && uv run python - <<'PY'
import sys
import yaml
from pathlib import Path

path = Path("/Users/noidee/_dev/github-workflows/.github/workflows/create_docker_changelog_version_release.yaml")
document = yaml.safe_load(path.read_text(encoding="utf-8"))
# `on` is parsed as the boolean True by YAML 1.1, which is the usual surprise.
trigger = document.get("on", document.get(True))
assert "workflow_call" in trigger, "the workflow is not callable"
inputs = trigger["workflow_call"]["inputs"]
expected = {
    "bump", "prerelease_id", "prerelease", "dry_run", "platforms",
    "version_file", "version_script", "product_name", "release_files",
}
assert set(inputs) == expected, f"inputs differ: {set(inputs) ^ expected}"
outputs = set(trigger["workflow_call"]["outputs"])
assert outputs == {"tag", "version", "is_prerelease", "release_url", "image_digest"}, outputs
print(f"parsed; {len(inputs)} inputs, {len(outputs)} outputs")
PY
```

Expected: `parsed; 9 inputs, 5 outputs`.

```bash
cd /Users/noidee/_dev/github-workflows && grep -n "uses:" \
  .github/workflows/create_docker_changelog_version_release.yaml \
  | grep -v "NoiXdev/github-workflows/actions/" \
  | grep -vE "@[0-9a-f]{40} # v" \
  && echo "UNPINNED ACTION ABOVE" || echo "every third-party action is pinned"
```

Expected: `every third-party action is pinned`.

- [ ] **Step 4: Write the blueprint**

Create `blueprints/release-docker.yaml`:

```yaml
name: Release
run-name: Release ${{ inputs.bump }}${{ inputs.dry_run && ' (dry run)' || '' }}

# Dispatch-driven, not triggered by a push or by CI finishing. A release that
# ships a numbered candidate needs three things the commits cannot supply:
# which part to bump, whether this is a candidate, and whether to rehearse.
#
# The green-commit guarantee that a workflow_run trigger gets by construction
# is bought back here with `needs:` - name your own CI and verification
# workflows there, and they run on the ref being released rather than on an
# earlier run that is trusted to still apply. The branch guard does the rest.
on:
  workflow_dispatch:
    inputs:
      bump:
        description: "Version bump type"
        required: true
        type: choice
        options:
          - patch
          - minor
          - major
      prerelease_id:
        description: "Prerelease identifier (rc, beta); empty promotes to a final version"
        type: string
        default: ""
      dry_run:
        description: "Run every gate and build, push nothing"
        type: boolean
        default: false

concurrency: release

permissions:
  contents: write

jobs:

  gates:
    if: ${{ github.ref == 'refs/heads/main' }}
    uses: ./.github/workflows/ci.yaml

  release:
    needs: [gates]
    uses: NoiXdev/github-workflows/.github/workflows/create_docker_changelog_version_release.yaml@main
    with:
      bump: ${{ inputs.bump }}
      prerelease_id: ${{ inputs.prerelease_id }}
      dry_run: ${{ inputs.dry_run }}
      # Two platforms build through emulation; drop the second to build only
      # what the runner is.
      platforms: linux/amd64,linux/arm64
      version_file: version.json
      version_script: tools/set_version.py
    secrets: inherit
```

- [ ] **Step 5: Document it where the others are documented**

In `README.md`, directly after the *Create Compose Changelog + Version +
Release* entry, add:

```markdown
#### [Create Docker Changelog + Version + Release](./.github/workflows/create_docker_changelog_version_release.yaml)

A dispatch-driven release for a product that ships a container image. It
computes the next version, writes it into the tree, builds and pushes the
image to Docker Hub, commits, tags, creates the release and announces it.
Supports `prerelease_id`, `dry_run` and multi-architecture builds through
`platforms`.

Docker Hub is addressed through `vars.DOCKERHUB_NAMESPACE` /
`vars.DOCKERHUB_IMAGE` and `secrets.DOCKERHUB_USERNAME` /
`secrets.DOCKERHUB_TOKEN`, as the per-repository release workflows it
generalises already do.

A product whose version lives somewhere other than a JSON manifest — a
`pyproject.toml`, a compose default, a shell script — points `version_script`
at an executable in its own repository, invoked as `./<script> <version>`.
How a version reaches those files is the product's business, not this
repository's; the same escape hatch the Tauri workflow has for its macOS
build.
See the blueprint for how to use it [here](./blueprints/release-docker.yaml).
```

- [ ] **Step 6: Check the blueprint parses too**

```bash
cd /Users/noidee/_dev/ddev-sync && uv run python -c "
import yaml, pathlib
for name in ('blueprints/release-docker.yaml',):
    path = pathlib.Path('/Users/noidee/_dev/github-workflows') / name
    yaml.safe_load(path.read_text(encoding='utf-8'))
    print(f'{name}: parsed')
"
```

Expected: `blueprints/release-docker.yaml: parsed`.

- [ ] **Step 7: Commit, in that repository, on that branch**

```bash
cd /Users/noidee/_dev/github-workflows
git add .github/workflows/create_docker_changelog_version_release.yaml \
        blueprints/release-docker.yaml README.md
git commit -m "$(cat <<'EOF'
feat: add a Docker release workflow, generalised from three copies of it

marketix, inventorix and fleetorio each carry their own release.yml with
docker/build-push-action, and this repository's README asks for workflows
that are actually reusable. This is marketix's step order with the
product-specific parts turned into inputs, so a caller written the way
marketix is written today behaves as it does today.

Two things are new rather than copied. `platforms` builds a
multi-architecture image through QEMU, which no existing caller does and
which a tool installed on developer machines needs. `version_script` lets a
product whose version does not live in a JSON manifest write it wherever it
does live - the escape hatch the Tauri workflow already has for its macOS
build.

The release pushes the image before it commits and tags. Neither order is
atomic; an orphan image tag can be pushed over, while an orphan git tag in a
public repository may already have been fetched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

Do **not** push. Pushing and merging this branch, and `chore/pin-actions`
beneath it, are the repository owner's decisions.

---

## Task 3: The caller in ddev-sync

**Files:**
- Create: `.github/workflows/release.yaml`
- Modify: `.github/workflows/ci.yaml` (add a `workflow_call` trigger)

**Interfaces:**
- Consumes: the workflow and inputs produced by Task 2, and
  `tools/set_version.py` from Task 1.
- Produces: nothing later tasks read. Task 4 only describes it in prose.

- [ ] **Step 1: Give `ci.yaml` a `workflow_call` trigger**

In `.github/workflows/ci.yaml`, the `on:` block currently reads:

```yaml
on:
  # Every branch, not just main: this repository merges fast-forward without
  # pull requests, so a main-only push trigger would run only after the merge.
  # The filter is what keeps a bare `push:` from firing on tags as well, as
  # in addon.yaml: a release tag has nothing to say about the gates that its
  # branch did not already say, and running them again builds the image for
  # nothing.
  push:
    branches: ["**"]
  pull_request:
```

Add the third trigger, with its reason:

```yaml
on:
  # Every branch, not just main: this repository merges fast-forward without
  # pull requests, so a main-only push trigger would run only after the merge.
  # The filter is what keeps a bare `push:` from firing on tags as well, as
  # in addon.yaml: a release tag has nothing to say about the gates that its
  # branch did not already say, and running them again builds the image for
  # nothing.
  push:
    branches: ["**"]
  pull_request:
  # So release.yaml can gate on these gates having passed on the very ref it
  # is about to release, rather than trusting that an earlier run still
  # applies. Nothing else about this workflow changes.
  workflow_call:
```

- [ ] **Step 2: Write the caller**

Create `.github/workflows/release.yaml`:

```yaml
name: Release
run-name: Release ${{ inputs.bump }}${{ inputs.prerelease_id != '' && format('-{0}', inputs.prerelease_id) || '' }}${{ inputs.dry_run && ' (dry run)' || '' }}

# Dispatched by hand, because a release here is a deliberate act that needs
# three inputs the commits cannot supply: which part to bump, whether this is
# a candidate, and whether to rehearse. The organisation's Go release workflow
# is triggered by CI finishing instead, which makes "only a green commit on
# the default branch can release" true by construction; both guarantees are
# bought back here - `needs:` runs the gates and the whole verification matrix
# on the ref being released, and the branch guard below refuses a dispatch
# from anywhere but main.
on:
  workflow_dispatch:
    inputs:
      bump:
        description: "Version bump type"
        required: true
        type: choice
        options:
          - patch
          - minor
          - major
      prerelease_id:
        description: "Prerelease identifier; empty promotes to a final version"
        type: string
        default: "rc"
      dry_run:
        description: "Run every gate and build, push nothing"
        type: boolean
        default: false

concurrency: release

permissions:
  contents: write

jobs:
  # Lint, types, the unit suite with its 100 % gate, the image smoke tests and
  # the integration suite - the same workflow every push runs, on this ref.
  gates:
    if: ${{ github.ref == 'refs/heads/main' }}
    uses: ./.github/workflows/ci.yaml

  # Every cell in the registry, against an image built from this ref. An empty
  # `image` is the nightly path, so a release and the nightly run share one
  # definition of a cell. Note that matrix.yaml sets
  # `cancel-in-progress: false`, so a release dispatched while the nightly
  # matrix is running queues behind it rather than cancelling it.
  verify:
    if: ${{ github.ref == 'refs/heads/main' }}
    uses: ./.github/workflows/matrix.yaml
    with:
      image: ""

  release:
    needs: [gates, verify]
    uses: NoiXdev/github-workflows/.github/workflows/create_docker_changelog_version_release.yaml@main
    with:
      bump: ${{ inputs.bump }}
      prerelease_id: ${{ inputs.prerelease_id }}
      dry_run: ${{ inputs.dry_run }}
      # arm64 is not optional: DDEV users run Apple Silicon.
      platforms: linux/amd64,linux/arm64
      # The only JSON file in this repository that holds a version, and the
      # one `compute-version` can read. tools/set_version.py writes the other
      # four places from it, including pyproject.toml in its PEP 440 form.
      version_file: version.json
      version_script: tools/set_version.py
      product_name: ddev-sync
    secrets: inherit

  # What the published artifact is actually touched on both architectures.
  # ci.yaml runs these same checks against a locally built amd64 image; this
  # runs them against the image a user would pull, on a native arm64 runner as
  # well. It is not a sync: the matrix cells install real frameworks and run on
  # amd64 runners, so arm64 is smoke-tested here and that limitation is written
  # down in README.md and profiles/VERIFIED.md rather than glossed over.
  smoke:
    needs: [release]
    if: ${{ inputs.dry_run == false }}
    strategy:
      fail-fast: false
      matrix:
        runner: [ubuntu-latest, ubuntu-24.04-arm]
    runs-on: ${{ matrix.runner }}
    steps:
      - name: Pull the image that was just published
        env:
          IMAGE: docker.io/${{ vars.DOCKERHUB_NAMESPACE }}/${{ vars.DOCKERHUB_IMAGE }}:${{ needs.release.outputs.version }}
        run: |
          set -euo pipefail
          echo "${IMAGE}"
          docker pull "${IMAGE}"
          echo "IMAGE=${IMAGE}" >> "$GITHUB_ENV"

      # The image must be self-sufficient after a pull: nothing installed at
      # container start, no network needed to run. `--network none` is what
      # turns that from an intention into a test.
      - name: It runs with no network at all
        run: |
          docker run --rm --network none "${IMAGE}" --help > /dev/null
          docker run --rm --network none "${IMAGE}" --version

      # One `command -v` per binary: busybox's builtin looks only at its first
      # argument and exits 0 on it, so a single multi-argument call would
      # assert nothing about the others.
      - name: It carries every binary the engine runs locally
        run: |
          docker run --rm --network none --entrypoint sh "${IMAGE}" -c \
            'for b in ssh scp rsync mysql mysqladmin mysqldump rclone docker; do
               command -v "$b" || { echo "MISSING: $b"; exit 1; }
             done'

      - name: It resolves profiles from the installed path, not a checkout
        run: |
          test "$(docker run --rm --network none --entrypoint sh "${IMAGE}" -c \
            'python -c "from ddev_sync.profiles.registry import DEFAULT_PROFILE_ROOT; print(DEFAULT_PROFILE_ROOT)"')" \
            = "/opt/ddev-sync/profiles"

      # The version the image reports has to be the version that was released.
      # It is compared against the PEP 440 form, because that is what the
      # wheel's metadata carries and what `--version` therefore prints:
      # `compute-version` can only produce `X.Y.Z` or `X.Y.Z-<id>.<N>`, and
      # the second normalises to `X.Y.Z<id><N>`.
      - name: It reports the version it was released as
        env:
          VERSION: ${{ needs.release.outputs.version }}
        run: |
          set -euo pipefail
          expected="$(printf '%s' "${VERSION}" | sed -E 's/-([0-9A-Za-z]+)\.([0-9]+)$/\1\2/')"
          printed="$(docker run --rm --network none "${IMAGE}" --version)"
          echo "${printed}"
          if [ "${printed}" != "ddev-sync, version ${expected}" ]; then
            echo "::error::expected 'ddev-sync, version ${expected}', got '${printed}'"
            exit 1
          fi
```

- [ ] **Step 3: Check both files parse, and that the caller matches what it calls**

```bash
uv run python - <<'PY'
from pathlib import Path

import yaml

release = yaml.safe_load(Path(".github/workflows/release.yaml").read_text(encoding="utf-8"))
ci = yaml.safe_load(Path(".github/workflows/ci.yaml").read_text(encoding="utf-8"))
matrix = yaml.safe_load(Path(".github/workflows/matrix.yaml").read_text(encoding="utf-8"))


def triggers(document: dict[str, object]) -> dict[str, object]:
    # YAML 1.1 parses the key `on` as the boolean True.
    return document.get("on", document.get(True))  # type: ignore[return-value]


assert "workflow_call" in triggers(ci), "ci.yaml is not callable"
assert "workflow_call" in triggers(matrix), "matrix.yaml is not callable"

jobs = release["jobs"]
assert set(jobs) == {"gates", "verify", "release", "smoke"}, set(jobs)
assert jobs["release"]["needs"] == ["gates", "verify"]
# The input we pass has to be one matrix.yaml declares.
assert "image" in triggers(matrix)["workflow_call"]["inputs"]
assert jobs["verify"]["with"] == {"image": ""}
# Both local jobs carry the branch guard; the release and smoke jobs inherit
# it by depending on them.
for name in ("gates", "verify"):
    assert jobs[name]["if"] == "${{ github.ref == 'refs/heads/main' }}", name
print("release.yaml agrees with ci.yaml and matrix.yaml")
PY
```

Expected: `release.yaml agrees with ci.yaml and matrix.yaml`.

- [ ] **Step 4: Confirm the gates are still green**

Nothing in this task touches Python, but `ci.yaml` was edited and the whole
gate is cheap insurance against a YAML slip that breaks collection.

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Expected: all three silent, 100 % coverage.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yaml .github/workflows/ci.yaml
git commit -m "$(cat <<'EOF'
ci: add the release caller, gated on this ref's gates and matrix

The release itself lives in NoiXdev/github-workflows; this is the caller.
It is dispatched by hand because a numbered candidate needs a bump, a
prerelease identifier and a rehearsal flag that no commit can supply.

What a workflow_run trigger would give by construction is bought back:
`release` needs both `gates` and `verify`, so the CI gates and every matrix
cell run on the ref being released rather than on an earlier run trusted to
still apply, and a branch guard refuses a dispatch from anywhere but main.

The smoke job pulls the published image on amd64 and on a native arm64
runner and runs ci.yaml's own image checks against it, plus one more: the
version it reports must be the version released, compared in its PEP 440
form because that is what the wheel metadata carries.

ci.yaml gains only a workflow_call trigger.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Make the add-on's own texts true

**Files:**
- Modify: `README.md:15-40` (the *Status* section) and the paragraph at
  `README.md:74-78`
- Modify: `install.yaml:96-112` (the *Next steps* message)
- Modify: `ddev-sync/prepare-sidecar.sh:256` (the failure message)
- Modify: `profiles/VERIFIED.md` (*Known limitations*)

**Interfaces:**
- Consumes: nothing executable. It describes Tasks 1-3's result.
- Produces: nothing. This is the last task.

Three places currently tell the user there is no image and to build one. After
Tasks 1-3 that becomes false the moment a release runs, and a pipeline that
publishes an image while the install message sends the user off to build their
own is worse than no pipeline.

**One measured fact this task depends on, so that nobody re-derives it:** a
bare `ddev add-on get <owner>/<repo>` installs a pre-release. At DDEV v1.25.0
and v1.25.4 alike, `pkg/ddevapp/addons.go::GetAddonTarballURL` calls
`github.GetGitHubRelease(owner, repo, "")`, which calls `ListReleases` and
takes `releases[0]` with no pre-release filter. So no `--version` flag is
needed, and the two `.env` lines become unnecessary rather than merely
different — DDEV installs from the release tarball, and that tarball carries
the compose default the release commit wrote.

- [ ] **Step 1: Rewrite the README's Status section**

Replace `README.md` lines 15 through 40 — from `## Status` down to and
including the fenced block ending before `` `init` asks for a name `` — with:

```markdown
## Status

**Published as a release candidate.** `v0.1.0-rc.1` is the first published
version: the engine, the profiles, the container image and the add-on
packaging all exist and are installable, and what has *not* happened yet is
written down rather than implied. No real sync against a real remote host has
been run through the add-on, and `profiles/VERIFIED.md` names every profile
variant whose commands no run has touched.

```bash
cd /your/project
ddev add-on get NoiXdev/ddev-sync
```

No `.ddev/.env` lines are needed: the add-on names the image it shipped with.

Then load your key, restart, and let `init` ask for the target:
```

Keep the `ddev auth ssh` / `ddev restart` / `ddev sync init` block that
follows, unchanged.

Then add, directly after the install block above:

```markdown
To work on the add-on itself, install from a checkout and point it at a local
image instead:

```bash
docker build -t ddev-sync:dev /path/to/ddev-sync
cd /your/project
ddev add-on get /path/to/ddev-sync
printf 'DDEV_SYNC_IMAGE=ddev-sync\nDDEV_SYNC_VERSION=dev\n' >> .ddev/.env
```

**The order matters.** A pre-start hook runs on every project start and reads
the image, so the image has to exist and `.ddev/.env` has to name it *before*
the first `ddev restart`. Get it wrong and the whole project refuses to start,
not just the sidecar.
```

- [ ] **Step 2: Record what arm64 means, in the README paragraph that already records what was exercised**

`README.md:74-78` currently ends with "…Linux, Colima, OrbStack and rootless
Docker have not been tried, and no real sync against a real remote host has yet
been run through the add-on. The CI workflow that tests the add-on has not run
yet either."

Append one sentence to that paragraph:

```markdown
The published image is built for `linux/amd64` and `linux/arm64`; on arm64 it
is smoke-tested only — it starts, carries its binaries, resolves its profiles
and reports its version — because the verification matrix installs real
frameworks and runs on amd64 runners.
```

- [ ] **Step 3: Rewrite the install message**

In `install.yaml`, the second `post_install_actions` entry currently describes
building an image. Replace its comment and its heredoc — lines 96 through 112,
from `cat <<'NEXT'` and its preceding comment lines through the closing
`NEXT` — with:

```yaml
  - |
    #ddev-description:Say what has to happen before the project can start
    # Deliberately outside the template's existence check above: these steps
    # are mandatory on every install, including a reinstall and an install
    # into a project that already has a configuration. This is the only
    # message the add-on prints, so it says all of it rather than the one step
    # that happens to be new.
    cat <<'NEXT'

    Next steps:

      1. ddev auth ssh      (the sidecar reaches remote hosts through DDEV's agent)
      2. ddev restart
      3. ddev sync init

    The add-on names the image version it shipped with, so there is nothing to
    put in .ddev/.env. A pre-start hook pulls that image on the first start,
    which can take a moment, and says so while it happens.

    This is a release candidate. No real sync against a real remote host has
    been run through the add-on yet, and profiles/VERIFIED.md names every
    profile variant whose commands no run has touched.
    NEXT
```

- [ ] **Step 4: Rewrite the pre-start hook's failure message**

In `ddev-sync/prepare-sidecar.sh`, line 256 currently reads:

```
    "The project cannot start without it. Until the image is published, build it from the ddev-sync checkout (docker build -t ddev-sync:dev .) and put DDEV_SYNC_IMAGE=ddev-sync and DDEV_SYNC_VERSION=dev in .ddev/.env."
```

Replace it with:

```
    "The project cannot start without it. Check that the image name and version are right (DDEV_SYNC_IMAGE and DDEV_SYNC_VERSION in .ddev/.env override the add-on's own defaults) and that Docker can reach the registry. To run against a local build instead: docker build -t ddev-sync:dev . and put DDEV_SYNC_IMAGE=ddev-sync and DDEV_SYNC_VERSION=dev in .ddev/.env."
```

- [ ] **Step 5: Record the arm64 limitation where limitations are recorded**

In `profiles/VERIFIED.md`, under `## Known limitations`, after the existing
`mysqldump --password=` bullet, add:

```markdown
- The published image is built for `linux/amd64` and `linux/arm64`, but only
  amd64 is covered by the verification matrix: a cell installs a real
  framework and the runners that do so are amd64. On arm64 the release
  pipeline asserts that the image starts with no network, carries every binary
  the engine invokes, resolves its profiles from the installed path and reports
  the version it was released as — which is a smoke test, not a sync. Nothing
  here claims a profile's commands have been run on arm64.
```

- [ ] **Step 6: Check that nothing asserted the old texts**

The bats suite exercises installation and could have been written against the
message that just changed. Confirm it was not:

```bash
grep -n -E "Next steps|Until the image is published|build it from the ddev-sync checkout" tests/test.bats || echo "the bats suite asserts none of the changed strings"
grep -rn "Until the image is published" --include="*.py" --include="*.bats" --include="*.yaml" --include="*.sh" . | grep -v "\.git/" || echo "no other reference to the old message"
```

Expected: both lines report that nothing references the changed strings. If
something does, fix that assertion in the same commit and say so in the message.

- [ ] **Step 7: Confirm the shell and YAML still parse**

```bash
bash -n ddev-sync/prepare-sidecar.sh && echo "prepare-sidecar.sh parses"
uv run python -c "
import yaml, pathlib
yaml.safe_load(pathlib.Path('install.yaml').read_text(encoding='utf-8'))
print('install.yaml parses')
"
uv run pytest
```

Expected: both parse, and the suite passes — `tests/test_no_legacy_terms.py`
walks every tracked file, so a stray banned term in the new prose is caught
here.

- [ ] **Step 8: Commit**

```bash
git add README.md install.yaml ddev-sync/prepare-sidecar.sh profiles/VERIFIED.md
git commit -m "$(cat <<'EOF'
docs: stop saying there is no published image

Three places told the user to build an image themselves: the README's status
section, the add-on's only install message, and the pre-start hook's failure
message. With the release pipeline in place that becomes false the moment a
release runs, and an install message that sends the user off to build their
own image is worse than no message.

The plain `ddev add-on get NoiXdev/ddev-sync` is now the instruction, with no
.ddev/.env lines at all: DDEV installs from the release tarball, which
carries the compose default the release commit wrote. A pre-release is
reachable that way - at DDEV v1.25.0 and v1.25.4, GetAddonTarballURL asks
GetGitHubRelease for the newest release and that function takes releases[0]
with no prerelease filter.

What has not happened is still said out loud: no real sync against a real
remote host, and arm64 smoke-tested rather than matrix-verified, the latter
now also under Known limitations where it belongs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Rollout

These steps need the repository owner, or an artifact that does not exist until
they act. They are not tasks, because a task has to be able to finish green.

1. **Prerequisites**, in this order: make `NoiXdev/ddev-sync` public; push
   `main`; land `chore/pin-actions` and `feat/docker-release-workflow` on
   `github-workflows`' `main` (the caller references the workflow at `@main`,
   so until then the release cannot resolve its own jobs); add the repository
   variable `DOCKERHUB_IMAGE=ddev-sync`; confirm the organisation carries
   `DOCKERHUB_NAMESPACE`, `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` and
   `DISCORD_WEBHOOK_RELEASE_NOTIFICATION`; confirm `sha_pinning_required` is
   off for this repository and that `main` accepts a push from `GITHUB_TOKEN`.

2. **Rehearse.** Dispatch *Release* with `bump: minor`, `prerelease_id: rc`,
   `dry_run: true`. It runs the gates, every matrix cell, the multi-architecture
   build and the changelog, and pushes nothing. Read the step summary: it names
   the version it *would* have released and the image tags it *would* have
   pushed, and for a candidate that list must hold exactly one tag,
   `0.1.0-rc.1`. Note how long the arm64 leg took under emulation — if it is
   unreasonable, the documented fallback is a native two-runner build with a
   digest merge, a change to the shared workflow rather than to ddev-sync.

3. **Release.** The same dispatch with `dry_run: false`. Expect: `v0.1.0-rc.1`
   tagged and marked as a pre-release, one image tag on
   `docker.io/noixdev/ddev-sync`, `latest` untouched, the smoke job green on
   both architectures.

4. **Then, and only then, switch the bats release test on.** `tests/test.bats`
   already holds it at lines 341-346, tagged `# bats test_tags=release`, with a
   body that is one `skip` reading "nothing is published yet", and
   `addon.yaml`'s second bats step asserts through the TAP plan that exactly
   one test was selected and that it skipped. It is excluded from the tasks
   above because it cannot pass until a release exists.

   Three edits, and the first is the one that is easy to miss. `setup()` at
   line 22 currently returns early for this test:

```bash
  # The release test only skips; building an image and configuring a project
  # for it would be wasted time, and a failure here would fail a test that is
  # meant to be skipped.
  case "${BATS_TEST_DESCRIPTION}" in *release*) return 0 ;; esac
```

   so `DIR`, `PROJNAME` and `TESTDIR` are unset for it and no DDEV project
   exists. The release test needs the project but must **not** get the two
   steps at lines 84-85, which build a local image and write
   `DDEV_SYNC_IMAGE` / `DDEV_SYNC_VERSION` into `.ddev/.env` — the whole point
   is that the published image needs neither. So remove that early return, and
   guard those two lines instead (their own comment already says "Plan 8
   removes these two steps"):

```bash
  # The published image carries its own version default, so the release test
  # takes neither the local build nor the .env lines that override it.
  case "${BATS_TEST_DESCRIPTION}" in
    *release*) ;;
    *)
      docker build -q -t ddev-sync:bats "${DIR}" >/dev/null
      printf 'DDEV_SYNC_IMAGE=ddev-sync\nDDEV_SYNC_VERSION=bats\n' >> .ddev/.env
      ;;
  esac
```

   Leave the stale-pair `printf` at line 90 in place for both: it is about the
   hook migrating `DDEV_SYNC_DOCKER_GID` out of `.env`, which the release test
   exercises just as well.

   Then the test body:

```bash
# bats test_tags=release
@test "install from release" {
  set -eu -o pipefail
  # setup() left us in ${TESTDIR} with a configured project and no image
  # override, which is exactly what a user installing the release has.
  ddev add-on get NoiXdev/ddev-sync
  ddev restart -y
  run ddev sync --version
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"ddev-sync, version "* ]] || false
  ddev add-on remove ddev-sync
}
```

   And `addon.yaml`'s *Install from a release* step: change the assertion from
   `^ok 1 .* # skip` to `^ok 1 ` **without** `# skip`, so a silent re-skip
   cannot pass. Keep the `1\.\.1` plan assertion — it is what catches a typo in
   the `test_tags` comment emptying the step.

5. **The first real sync.** What this whole plan was for: a real project, a
   real remote host, `--dry-run` first. Back up the project's `.env`; the
   Laravel profile writes about nineteen keys into it and empties `DB_URL`,
   `DATABASE_URL` and `REDIS_URL`.

## Out of scope

Listing the add-on in the DDEV add-on registry (that belongs to the final
release); migrating `marketix`, `inventorix` or `fleetorio` onto the new shared
workflow; arm64 matrix cells; the four open items in
`docs/plans/2026-10-12-service-feature-verification-follow-ups.md`; and
verifying `typo3@9`, `typo3@10` or `shopware6`.
