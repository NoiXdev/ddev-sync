from pathlib import Path

import pytest

from ddev_sync.transport.runner import (
    CommandFailed,
    CommandResult,
    SubprocessRunner,
    run_checked,
)


def test_a_zero_exit_is_ok() -> None:
    result = CommandResult(argv=("true",), returncode=0, stdout="", stderr="")
    assert result.ok is True


def test_a_non_zero_exit_is_not_ok() -> None:
    result = CommandResult(argv=("false",), returncode=1, stdout="", stderr="")
    assert result.ok is False


def test_the_real_runner_captures_stdout() -> None:
    result = SubprocessRunner().run(["echo", "hello"])
    assert result.ok is True
    assert result.stdout.strip() == "hello"
    assert result.argv == ("echo", "hello")


def test_the_real_runner_captures_a_failure_without_raising() -> None:
    result = SubprocessRunner().run(["false"])
    assert result.returncode != 0


def test_the_real_runner_captures_stderr() -> None:
    result = SubprocessRunner().run(["sh", "-c", "echo oops >&2; exit 3"])
    assert result.returncode == 3
    assert "oops" in result.stderr


def test_the_real_runner_honours_cwd(tmp_path: Path) -> None:
    result = SubprocessRunner().run(["pwd"], cwd=tmp_path)
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_the_real_runner_honours_env() -> None:
    result = SubprocessRunner().run(["sh", "-c", "echo $MARKER"], env={"MARKER": "set"})
    assert result.stdout.strip() == "set"


def test_env_replaces_rather_than_extends_the_parent_environment() -> None:
    # /usr/bin/env dumps its actual process environment. sh -c "echo $PATH" was
    # tried first, but macOS's /bin/sh fills in a default PATH itself when the
    # shell finds none set, which pins a shell quirk rather than subprocess's
    # env semantics. Reading the child's real environment sidesteps that.
    result = SubprocessRunner().run(["/usr/bin/env"], env={"MARKER": "x"})
    assert result.stdout.strip() == "MARKER=x"


def test_stdout_path_streams_to_a_file_and_leaves_stdout_empty(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    result = SubprocessRunner().run(["echo", "streamed"], stdout_path=target)
    assert result.ok is True
    assert result.stdout == ""
    assert target.read_text(encoding="utf-8").strip() == "streamed"


def test_a_missing_executable_becomes_a_failed_result_not_an_exception() -> None:
    result = SubprocessRunner().run(["definitely-not-a-real-binary-xyz"])
    assert result.ok is False
    assert "not" in result.stderr.lower() or result.returncode == 127


def test_run_checked_returns_the_result_on_success() -> None:
    assert run_checked(SubprocessRunner(), ["true"]).ok is True


def test_run_checked_raises_with_the_command_and_stderr() -> None:
    with pytest.raises(CommandFailed) as caught:
        run_checked(SubprocessRunner(), ["sh", "-c", "echo bad >&2; exit 4"])
    message = str(caught.value)
    assert "exit 4" in message
    assert "bad" in message
    assert caught.value.result.returncode == 4


def test_passthrough_inherits_stdout_but_still_captures_stderr() -> None:
    # The contract the files step depends on: its progress goes to the
    # terminal, its error message still has something to quote.
    result = SubprocessRunner().run(
        ["sh", "-c", "echo out; echo err >&2; exit 3"], passthrough=True
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert "err" in result.stderr


# Measured against real Docker, because this is the whole reason the property
# exists: `docker exec` splits its failures across the two streams. A
# daemon-level refusal - no such container, container not running - arrives on
# stderr with exit 1, and an OCI runtime failure - a workdir that is not in the
# image, a binary that is not on its PATH - arrives on **stdout** with exit
# 127. Reporting stderr alone printed `did not answer: ` and nothing else for
# the whole second class, which includes a web container missing `wp`, `drush`
# or `dep`.
def test_the_reason_comes_from_stderr_when_that_is_where_it_is() -> None:
    result = CommandResult(
        argv=("docker", "exec"),
        returncode=1,
        stdout="",
        stderr="Error response from daemon: No such container: ddev-acme-web\n",
    )
    assert result.failure_detail() == "Error response from daemon: No such container: ddev-acme-web"


def test_the_reason_comes_from_stdout_when_that_is_where_it_is() -> None:
    result = CommandResult(
        argv=("docker", "exec"),
        returncode=127,
        stdout='OCI runtime exec failed: exec: "wp": executable file not found in $PATH\n',
        stderr="",
    )
    assert "executable file not found" in result.failure_detail()


def test_both_streams_are_kept_when_both_said_something() -> None:
    """Neither is dropped: a failing CLI often explains itself on stdout while
    warning on stderr, and an error message is read once."""
    result = CommandResult(
        argv=("docker", "exec"), returncode=1, stdout="Error: no such post\n", stderr="Warning: x\n"
    )
    assert result.failure_detail() == "Warning: x\nError: no such post"


def test_a_command_that_said_nothing_at_all_says_so() -> None:
    """Better than a message ending in a colon and nothing."""
    result = CommandResult(argv=("docker", "exec"), returncode=137, stdout="", stderr="")
    assert result.failure_detail() == "no output (exit 137)"
