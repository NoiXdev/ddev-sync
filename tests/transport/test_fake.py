from pathlib import Path

import pytest

from ddev_sync.transport.fake import FakeCommandRunner, UnscriptedCommand


def test_a_scripted_command_returns_its_result() -> None:
    runner = FakeCommandRunner()
    runner.script(["echo", "hi"], stdout="hi\n")
    result = runner.run(["echo", "hi"])
    assert result.stdout == "hi\n"
    assert result.ok is True


def test_calls_are_recorded_in_order() -> None:
    runner = FakeCommandRunner()
    runner.script(["a"])
    runner.script(["b"])
    runner.run(["a"])
    runner.run(["b"])
    assert runner.calls == [("a",), ("b",)]


def test_an_unscripted_command_raises_with_what_was_expected() -> None:
    runner = FakeCommandRunner()
    runner.script(["expected"])
    with pytest.raises(UnscriptedCommand) as caught:
        runner.run(["surprise"])
    message = str(caught.value)
    assert "surprise" in message
    assert "expected" in message


def test_a_scripted_failure_is_returned_not_raised() -> None:
    runner = FakeCommandRunner()
    runner.script(["false"], returncode=1, stderr="nope")
    result = runner.run(["false"])
    assert result.returncode == 1
    assert result.stderr == "nope"


def test_the_same_command_can_be_scripted_twice_with_different_results() -> None:
    runner = FakeCommandRunner()
    runner.script(["check"], stdout="first")
    runner.script(["check"], stdout="second")
    assert runner.run(["check"]).stdout == "first"
    assert runner.run(["check"]).stdout == "second"


def test_a_prefix_script_matches_any_command_starting_with_it() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="remote output")
    assert runner.run(["ssh", "-p", "22", "host", "whoami"]).stdout == "remote output"
    assert runner.run(["ssh", "other"]).stdout == "remote output"


def test_stdout_path_writes_the_scripted_stdout_to_the_file(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="-- dump --\n-- Dump completed\n")
    target = tmp_path / "dump.sql"
    result = runner.run(["ssh", "host", "mysqldump"], stdout_path=target)
    assert result.stdout == ""
    assert "Dump completed" in target.read_text(encoding="utf-8")


def test_a_non_matching_prefix_script_is_skipped_for_a_later_matching_one() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="ssh output")
    runner.script_prefix(["scp"], stdout="scp output")
    assert runner.run(["scp", "host:/a", "/tmp/a"]).stdout == "scp output"


def test_recorded_env_and_cwd_are_available_for_assertions(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script(["x"])
    runner.run(["x"], cwd=tmp_path, env={"A": "1"})
    assert runner.invocations[0].cwd == tmp_path
    assert runner.invocations[0].env == {"A": "1"}
