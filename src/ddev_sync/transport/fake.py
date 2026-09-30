"""A CommandRunner that records instead of executing.

Scripting is explicit and unscripted calls raise: a test that silently accepts
an unexpected command is a test that will keep passing after the code starts
doing the wrong thing.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ddev_sync.errors import DdevSyncError
from ddev_sync.transport.runner import CommandResult


class UnscriptedCommand(DdevSyncError):
    """A command was run that the test did not script."""


@dataclass(frozen=True)
class Invocation:
    """One recorded call, with everything a test might want to assert on."""

    argv: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str] | None
    stdout_path: Path | None


@dataclass
class _Script:
    match: tuple[str, ...]
    prefix: bool
    returncode: int
    stdout: str
    stderr: str
    used: bool = False


class FakeCommandRunner:
    """Returns scripted results and records every call."""

    def __init__(self) -> None:
        self._scripts: list[_Script] = []
        self.invocations: list[Invocation] = []

    @property
    def calls(self) -> list[tuple[str, ...]]:
        """Just the argument vectors, in call order."""
        return [entry.argv for entry in self.invocations]

    def script(
        self,
        argv: Sequence[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Script an exact-match command."""
        self._scripts.append(_Script(tuple(argv), False, returncode, stdout, stderr))

    def script_prefix(
        self,
        prefix: Sequence[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Script any command starting with `prefix`."""
        self._scripts.append(_Script(tuple(prefix), True, returncode, stdout, stderr))

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
    ) -> CommandResult:
        """Return the first unused matching script, or raise."""
        command = tuple(argv)
        self.invocations.append(
            Invocation(
                argv=command,
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdout_path=stdout_path,
            )
        )
        script = self._find(command)
        if script is None:
            expected = ", ".join(" ".join(entry.match) for entry in self._scripts) or "nothing"
            raise UnscriptedCommand(
                f"unscripted command: {' '.join(command)}\nscripted: {expected}"
            )
        script.used = True
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(script.stdout, encoding="utf-8")
            return CommandResult(command, script.returncode, "", script.stderr)
        return CommandResult(command, script.returncode, script.stdout, script.stderr)

    def _find(self, command: tuple[str, ...]) -> _Script | None:
        for script in self._scripts:
            if script.used:
                continue
            if script.prefix:
                if command[: len(script.match)] == script.match:
                    return script
            elif command == script.match:
                return script
        for script in self._scripts:
            if script.prefix and command[: len(script.match)] == script.match:
                return script
        return None
