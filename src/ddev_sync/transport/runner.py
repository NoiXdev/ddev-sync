"""The one place in production code that executes a process.

Everything else in ddev-sync builds argument vectors and hands them to a
CommandRunner. That separation is what lets the entire engine be tested without
a network, and what makes `--dry-run` show exactly what a real run would do.
"""

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ddev_sync.errors import DdevSyncError


@dataclass(frozen=True)
class CommandResult:
    """What came back from running one command."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when the command exited zero."""
        return self.returncode == 0


class CommandFailed(DdevSyncError):
    """A command that was expected to succeed did not."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        super().__init__(
            f"command failed with exit {result.returncode}: "
            f"{' '.join(result.argv)}\n{result.stderr.strip()}"
        )


class CommandRunner(Protocol):
    """Executes an argument vector and reports what happened."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
        sensitive: bool = False,
        passthrough: bool = False,
    ) -> CommandResult:
        """Execute `argv`; stream stdout to `stdout_path` when given.

        `sensitive` says the output of this command carries credentials. It is
        a statement about what comes back, not an instruction about how to run
        the command: `SubprocessRunner` ignores it, and its only reader is the
        verbose narrator (`cli/verbose.py`), which withholds the output.

        `passthrough` lets the child write its stdout straight to this
        process's stdout instead of having it captured, so a long-running
        command can show progress. stderr is still captured, and the returned
        `stdout` is empty: a caller that needs to read a command's stdout must
        not ask for passthrough. It is meant for a caller that has already
        decided stdout is a terminal. `stdout_path` and `passthrough` both
        redirect stdout and are not meant to be combined; if both are given,
        `stdout_path` wins.
        """


class SubprocessRunner:
    """The real runner. The only production code that imports subprocess."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
        sensitive: bool = False,
        passthrough: bool = False,
    ) -> CommandResult:
        """Execute `argv`, capturing output, streaming it to a file, or passing it through.

        `sensitive` is deliberately unused: see `CommandRunner.run`.
        """
        command = list(argv)
        try:
            if stdout_path is not None:
                return self._run_streaming(command, cwd, env, stdout_path)
            if passthrough:
                return self._run_passthrough(command, cwd, env)
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=dict(env) if env is not None else None,
                capture_output=True,
                text=True,
                check=False,
            )
            return CommandResult(
                argv=tuple(command),
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except OSError as error:
            return CommandResult(argv=tuple(command), returncode=127, stdout="", stderr=str(error))

    def _run_streaming(
        self,
        command: list[str],
        cwd: Path | None,
        env: Mapping[str, str] | None,
        stdout_path: Path,
    ) -> CommandResult:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("wb") as sink:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdout=sink,
                stderr=subprocess.PIPE,
                check=False,
            )
        return CommandResult(
            argv=tuple(command),
            returncode=completed.returncode,
            stdout="",
            stderr=completed.stderr.decode(errors="replace"),
        )

    def _run_passthrough(
        self,
        command: list[str],
        cwd: Path | None,
        env: Mapping[str, str] | None,
    ) -> CommandResult:
        """Let the child write to this process's stdout, keeping its stderr.

        Only stdout is inherited. rsync writes its `--info=progress2` output
        there and its errors to stderr, and `operations/files.py` quotes that
        stderr in its failure message, so inheriting both would empty the one
        thing the error path needs. This is the split `_run_streaming` makes,
        with this process's stdout in place of the file.
        """
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=None,
            stderr=subprocess.PIPE,
            check=False,
        )
        return CommandResult(
            argv=tuple(command),
            returncode=completed.returncode,
            stdout="",
            stderr=completed.stderr.decode(errors="replace"),
        )


def run_checked(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout_path: Path | None = None,
) -> CommandResult:
    """Run `argv` and raise `CommandFailed` unless it exits zero."""
    result = runner.run(argv, cwd=cwd, env=env, stdout_path=stdout_path)
    if not result.ok:
        raise CommandFailed(result)
    return result
