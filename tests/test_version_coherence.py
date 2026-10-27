"""The seven files that name a version must name the same one.

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
            "endpoint/docker-compose.ddev-sync-endpoint.yaml",
        )
    }
    # The README pins the git tag, which is `v` + the semver the others hold.
    semver["README.md"] = _declared("README.md")

    assert len(set(semver.values())) == 1, f"versions disagree: {semver}"


def test_pyproject_and_the_lock_carry_the_same_version_in_the_form_packaging_uses() -> None:
    expected = pep440(_declared("version.json"))

    assert _declared("pyproject.toml") == expected
    assert _declared("uv.lock") == expected
