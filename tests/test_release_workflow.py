from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

MAIN_ONLY = "${{ github.ref == 'refs/heads/main' }}"


def _load(name: str) -> dict[Any, Any]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{name} did not parse to a mapping"
    return loaded


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 reads a bare `on` as the boolean True, so the trigger block
    # sits under `True`, not under "on".
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "the workflow declares no trigger block"
    return triggers


def _jobs(name: str) -> dict[str, Any]:
    jobs = _load(name)["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def test_release_has_exactly_the_four_jobs() -> None:
    assert list(_jobs("release.yaml")) == ["gates", "verify", "release", "smoke"], (
        "release.yaml's job set changed: the release's safety argument is written "
        "for gates -> verify -> release -> smoke, so a new or missing job needs "
        "that argument re-made, not just this list edited"
    )


def test_release_waits_for_the_gates_and_the_verification_matrix() -> None:
    needs = _jobs("release.yaml")["release"].get("needs")

    assert needs == ["gates", "verify"], (
        "`release` no longer needs [gates, verify]: it would start publishing to "
        "Docker Hub while this ref's lint, tests and verification matrix are "
        f"still running or red (needs: {needs!r})"
    )


def test_gates_and_verify_refuse_to_run_off_main() -> None:
    jobs = _jobs("release.yaml")

    for name in ("gates", "verify"):
        assert jobs[name].get("if") == MAIN_ONLY, (
            f"`{name}` lost its main-only guard: a dispatch from another branch "
            "would run it, and `release` (which needs it) would then publish "
            "from that branch"
        )


def test_verify_builds_its_own_image_rather_than_pulling_one() -> None:
    with_ = _jobs("release.yaml")["verify"].get("with")

    assert isinstance(with_, dict) and with_.get("image") == "", (
        "`verify` no longer passes image: '' to the matrix: a non-empty image "
        "would verify something other than the tree being released, and the "
        f"gate would pass for the wrong artifact (with: {with_!r})"
    )


def test_ci_and_matrix_can_be_called_by_the_release() -> None:
    for name in ("ci.yaml", "matrix.yaml"):
        assert "workflow_call" in _triggers(_load(name)), (
            f"{name} no longer declares a `workflow_call` trigger: release.yaml "
            "cannot resolve the job that uses it, so the release fails to start "
            "at all"
        )


def test_the_release_builds_the_endpoint_image_from_its_own_context() -> None:
    passed = _jobs("release.yaml")["release"]["with"]

    # The product ships two images and they are one version: the engine, and
    # the endpoint a stack publishes. Built by the same job so neither can be
    # released without the other, and from `endpoint/` rather than from the
    # repository root so an unrelated commit does not invalidate its cache.
    assert passed["extra_image"] == "ddev-sync-endpoint"
    assert passed["extra_image_dockerfile"] == "endpoint/Dockerfile"
    assert passed["extra_image_context"] == "endpoint"


def test_the_smoke_job_touches_both_published_images() -> None:
    smoke = _jobs("release.yaml")["smoke"]
    steps = yaml.safe_dump(smoke["steps"])

    # Both artifacts a user can pull, on both architectures: the engine image
    # the add-on starts as a sidecar, and the endpoint image an operator puts
    # into a production stack. The second one was published by the same job as
    # the first, so nothing but a smoke test says it can run at all.
    assert smoke["strategy"]["matrix"]["runner"] == ["ubuntu-latest", "ubuntu-24.04-arm"]
    assert "vars.DOCKERHUB_IMAGE" in steps
    assert "ddev-sync-endpoint" in steps
    # The endpoint's own promise, against the image a user would pull: it does
    # not start without a key, and it carries the far side of what the engine
    # invokes.
    assert "DDEV_SYNC_AUTHORIZED_KEYS" in steps
    assert "mysqldump" in steps
