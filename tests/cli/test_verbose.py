from ddev_sync.cli.verbose import VerboseRunner
from ddev_sync.transport.fake import FakeCommandRunner


def runner_with(stdout: str = "", stderr: str = "") -> FakeCommandRunner:
    fake = FakeCommandRunner()
    fake.script_prefix(["echo"], stdout=stdout, stderr=stderr)
    return fake


def test_level_two_prints_the_output_of_every_command() -> None:
    lines: list[str] = []
    runner = VerboseRunner(
        runner_with(stdout="two rows\n"), write=lines.append, secrets=set(), verbose=2
    )
    runner.run(["echo", "hello"])
    assert any("two rows" in line for line in lines)


def test_level_two_prints_what_the_command_wrote_to_stderr_too() -> None:
    lines: list[str] = []
    runner = VerboseRunner(
        runner_with(stderr="warning: slow\n"), write=lines.append, secrets=set(), verbose=2
    )
    runner.run(["echo", "hello"])
    assert any("warning: slow" in line for line in lines)


def test_level_two_does_not_print_the_argument_vector() -> None:
    # The argv carries `--password=` for the local database calls; it belongs
    # to level three, which the user opted into more deliberately.
    lines: list[str] = []
    runner = VerboseRunner(runner_with(), write=lines.append, secrets=set(), verbose=2)
    runner.run(["echo", "--password=sekrit"])
    assert not any("--password" in line for line in lines)


def test_level_three_prints_the_argument_vector() -> None:
    lines: list[str] = []
    runner = VerboseRunner(runner_with(), write=lines.append, secrets=set(), verbose=3)
    runner.run(["echo", "hello"])
    assert any("echo hello" in line for line in lines)


def test_a_registered_secret_never_reaches_the_output() -> None:
    lines: list[str] = []
    secrets = {"hunter2"}
    runner = VerboseRunner(
        runner_with(stdout="password is hunter2\n"), write=lines.append, secrets=secrets, verbose=3
    )
    runner.run(["echo", "--password=hunter2"])
    assert not any("hunter2" in line for line in lines)


def test_an_empty_secret_leaves_the_output_alone() -> None:
    lines: list[str] = []
    runner = VerboseRunner(
        runner_with(stdout="plain\n"), write=lines.append, secrets={""}, verbose=2
    )
    runner.run(["echo", "x"])
    assert lines == ["      plain"]


def test_a_secret_that_contains_another_is_scrubbed_whole() -> None:
    # Replacing the shorter one first would leave `***def` behind, which is
    # most of the longer secret.
    lines: list[str] = []
    runner = VerboseRunner(
        runner_with(stdout="abcdef\n"), write=lines.append, secrets={"abc", "abcdef"}, verbose=2
    )
    runner.run(["echo", "x"])
    assert not any("def" in line for line in lines)


def test_a_secret_registered_after_construction_is_still_scrubbed() -> None:
    # The set is shared with the ExecutionContext and filled during the run,
    # when `read_remote_credentials` first resolves a password - which happens
    # after this wrapper was built.
    lines: list[str] = []
    secrets: set[str] = set()
    runner = VerboseRunner(
        runner_with(stdout="later\n"), write=lines.append, secrets=secrets, verbose=2
    )
    secrets.add("later")
    runner.run(["echo", "x"])
    assert not any("later" in line for line in lines)


def test_the_result_is_returned_unchanged() -> None:
    inner = runner_with(stdout="payload\n")
    runner = VerboseRunner(inner, write=lambda _: None, secrets=set(), verbose=3)
    assert runner.run(["echo", "x"]).stdout == "payload\n"


def test_a_sensitive_commands_output_is_withheld() -> None:
    lines: list[str] = []
    runner = VerboseRunner(
        runner_with(stdout="DB_PASSWORD=hunter2\nAPP_KEY=base64:x\n", stderr="also hunter2\n"),
        write=lines.append,
        secrets=set(),
        verbose=2,
    )
    runner.run(["echo", "x"], sensitive=True)
    assert not any("hunter2" in line for line in lines)
    assert not any("APP_KEY" in line for line in lines)
    # A silent gap would read as a command that printed nothing.
    assert any("withheld" in line for line in lines)


def test_the_sensitive_flag_reaches_the_wrapped_runner_and_the_result_is_returned() -> None:
    inner = runner_with(stdout="payload\n")
    runner = VerboseRunner(inner, write=lambda _: None, secrets=set(), verbose=2)
    result = runner.run(["echo", "x"], sensitive=True)
    assert inner.invocations[0].sensitive is True
    assert result.stdout == "payload\n"
