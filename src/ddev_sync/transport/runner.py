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
    ) -> CommandResult:
        """Execute `argv`; stream stdout to `stdout_path` when given."""


class SubprocessRunner:
    """The real runner. The only production code that imports subprocess."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
    ) -> CommandResult:
        """Execute `argv`, capturing output or streaming it to a file."""
        command = list(argv)
        try:
            if stdout_path is not None:
                return self._run_streaming(command, cwd, env, stdout_path)
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
