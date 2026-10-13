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


UV_LOCK = """\
version = 1

[[package]]
name = "click"
version = "8.1.7"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "ddev-sync"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "click" },
]
"""


def build_tree(root: Path) -> None:
    """A miniature of the files `set_version` rewrites."""
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "version.json").write_text(VERSION_JSON, encoding="utf-8")
    (root / "docker-compose.ddev-sync.yaml").write_text(COMPOSE, encoding="utf-8")
    (root / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    (root / "ddev-sync").mkdir()
    (root / "ddev-sync" / "prepare-sidecar.sh").write_text(SIDECAR, encoding="utf-8")


def snapshot(root: Path) -> dict[Path, str]:
    """Every file under `root`, so a refusal can prove it wrote nothing at all."""
    return {path: path.read_text() for path in sorted(root.rglob("*")) if path.is_file()}


def test_it_writes_the_version_into_every_target(tmp_path: Path) -> None:
    build_tree(tmp_path)

    changed = rewrite("0.2.0", root=tmp_path)

    assert sorted(path.name for path in changed) == [
        "docker-compose.ddev-sync.yaml",
        "prepare-sidecar.sh",
        "pyproject.toml",
        "uv.lock",
        "version.json",
    ]
    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text()
    lock = (tmp_path / "uv.lock").read_text()
    assert 'name = "ddev-sync"\nversion = "0.2.0"' in lock
    # The anchor is doing work: the other package's version is not touched.
    assert 'name = "click"\nversion = "8.1.7"' in lock
    assert json.loads((tmp_path / "version.json").read_text())["version"] == "0.2.0"
    assert "DDEV_SYNC_VERSION:-0.2.0}" in (tmp_path / "docker-compose.ddev-sync.yaml").read_text()
    assert "DDEV_SYNC_VERSION 0.2.0)" in (tmp_path / "ddev-sync" / "prepare-sidecar.sh").read_text()


def test_a_prerelease_lands_as_semver_everywhere_but_pep440_in_pyproject(
    tmp_path: Path,
) -> None:
    build_tree(tmp_path)

    rewrite("0.1.0-rc.1", root=tmp_path)

    # pyproject feeds importlib.metadata, which only ever reports the
    # normalised form, so the file holds that form from the start.
    assert 'version = "0.1.0rc1"' in (tmp_path / "pyproject.toml").read_text()
    # uv normalises what it reads from pyproject.toml, so the lock does too.
    assert 'name = "ddev-sync"\nversion = "0.1.0rc1"' in (tmp_path / "uv.lock").read_text()
    # The other three name an image tag and a release, where the hyphen stays.
    assert json.loads((tmp_path / "version.json").read_text())["version"] == "0.1.0-rc.1"
    assert (
        "DDEV_SYNC_VERSION:-0.1.0-rc.1}" in (tmp_path / "docker-compose.ddev-sync.yaml").read_text()
    )
    assert (
        "DDEV_SYNC_VERSION 0.1.0-rc.1)"
        in (tmp_path / "ddev-sync" / "prepare-sidecar.sh").read_text()
    )


def test_pep440_leaves_a_final_version_alone() -> None:
    assert pep440("1.2.3") == "1.2.3"
    assert pep440("0.1.0-rc.1") == "0.1.0rc1"
    assert pep440("0.1.0-beta.12") == "0.1.0beta12"


def test_running_it_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    build_tree(tmp_path)

    assert rewrite("0.3.0", root=tmp_path) != []
    assert rewrite("0.3.0", root=tmp_path) == []


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
    # The last target, so every earlier one would already be written by a
    # one-pass loop: this is the case that pins the two-pass order.
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    before = snapshot(tmp_path)

    with pytest.raises(ValueError, match=r"uv\.lock: expected exactly one"):
        rewrite("0.4.0", root=tmp_path)

    assert snapshot(tmp_path) == before


def test_it_refuses_a_file_whose_pattern_matches_twice(tmp_path: Path) -> None:
    build_tree(tmp_path)
    compose = tmp_path / "docker-compose.ddev-sync.yaml"
    compose.write_text(compose.read_text() + compose.read_text(), encoding="utf-8")
    before = snapshot(tmp_path)

    with pytest.raises(ValueError, match="expected exactly one version to rewrite, found 2"):
        rewrite("0.4.0", root=tmp_path)

    assert snapshot(tmp_path) == before


def test_it_refuses_a_missing_file(tmp_path: Path) -> None:
    build_tree(tmp_path)
    (tmp_path / "version.json").unlink()

    with pytest.raises(FileNotFoundError, match=r"version\.json"):
        rewrite("0.4.0", root=tmp_path)


def test_main_reports_wrong_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert main(["1.0.0", "2.0.0"]) == 2
    assert "usage: tools/set_version.py" in capsys.readouterr().err


def test_main_reports_a_refused_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["nonsense"]) == 1
    assert "is not a version" in capsys.readouterr().err


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
