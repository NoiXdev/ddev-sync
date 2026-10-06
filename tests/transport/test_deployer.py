"""The one part of the Deployer integration that runs anything."""

import json

import pytest

from ddev_sync.errors import DeployerError
from ddev_sync.transport.deployer import make_deployer_resolver
from ddev_sync.transport.fake import FakeCommandRunner
from tests.transport.test_container import project

PAYLOAD = json.dumps(
    {
        "web-1": {
            "hostname": "web-1.example",
            "remote_user": "deploy",
            "port": None,
            "deploy_path": "/srv/app",
            "current_path": "/srv/app/current",
        },
        "staging": {
            "hostname": "staging.example",
            "remote_user": "deploy",
            "port": None,
            "deploy_path": "/srv/staging",
            "current_path": "/srv/staging/current",
        },
    }
)

VENDOR = ("vendor/bin/dep", "config", "--format=json")
ON_PATH = ("dep", "config", "--format=json")


def test_it_runs_dep_in_the_web_container_from_the_project_root() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(project("/srv/elsewhere"), runner)

    resolve("web-1")

    argv = runner.calls[0]
    assert argv[:2] == ("docker", "exec")
    # The project root, not a hard-coded path: the engine can be pointed at a
    # project mounted somewhere else with --project-root.
    assert argv[argv.index("-w") + 1] == "/srv/elsewhere"
    assert any(part.startswith("ddev-") and part.endswith("-web") for part in argv)
    assert argv[-3:] == VENDOR


def test_it_falls_back_to_a_dep_on_the_path_when_vendor_bin_is_absent() -> None:
    # Finding the tool is not the same as substituting a value, which is why
    # this is the one alternative the strict rule allows.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stderr="not found")
    resolve = make_deployer_resolver(project(), runner)

    with pytest.raises(DeployerError):
        resolve("web-1")

    assert [call[-3:] for call in runner.calls] == [VENDOR, ON_PATH]


def test_a_recipe_that_fails_does_not_fall_through_to_the_second_candidate() -> None:
    # Exit 127 means the interpreter could not find the program. Any other
    # non-zero means dep ran and its own evaluation failed - trying the next
    # candidate would run a broken recipe twice and report the wrong cause.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="PHP Fatal error in deploy.php")
    resolve = make_deployer_resolver(project(), runner)

    with pytest.raises(DeployerError) as error:
        resolve("web-1")

    assert "deploy.php" in str(error.value)
    assert len(runner.calls) == 1


def test_neither_candidate_present_names_both_places_it_looked() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stderr="not found")
    resolve = make_deployer_resolver(project(), runner)

    with pytest.raises(DeployerError) as error:
        resolve("web-1")

    assert "vendor/bin/dep" in str(error.value)
    assert "PATH" in str(error.value)
    # The runner's own words survive: exit 127 is also what a host without
    # `docker` reports, and that must not read as a missing Deployer.
    assert "not found" in str(error.value)


def test_a_dep_on_the_path_is_used_when_vendor_bin_is_absent() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stderr="not found")
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(project(), runner)

    assert resolve("web-1").host == "web-1.example"
    assert [call[-3:] for call in runner.calls] == [VENDOR, ON_PATH]


def test_the_result_is_resolved_once_and_then_reused() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(project(), runner)

    first = resolve("web-1")
    second = resolve("web-1")

    assert first == second
    assert len(runner.calls) == 1


def test_a_second_alias_is_served_from_the_same_single_invocation() -> None:
    # One `dep config` returns every host, and the command evaluates the
    # project's own recipe - so asking for a second alias must not be a second
    # chance to get a different answer inside one run.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(project(), runner)

    assert resolve("web-1").host == "web-1.example"
    assert resolve("staging").host == "staging.example"
    assert len(runner.calls) == 1
