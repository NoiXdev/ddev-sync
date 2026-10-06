"""A `CommandRunner` that narrates what it runs.

Installed at the CLI edge for `-vv` and above, and absent below it. It lives
here rather than in `transport/` because what it does is presentation: the
runner's job is to execute an argv and report the result, and formatting that
result for a terminal is the CLI's.

It exists at all because the reporter cannot do this. The reporter is handed a
`StepOutcome` per step, while one step runs many commands, so it never sees an
argv or any output. This wrapper sees both.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ddev_sync.transport.runner import CommandResult, CommandRunner

# Sits under the step line, inside its marker column, so it reads as belonging
# to that step; the name column, where failure text goes, stays distinct.
_INDENT = "      "


class VerboseRunner:
    """Delegates to `inner` and prints what it ran, scrubbed of `secrets`."""

    def __init__(
        self,
        inner: CommandRunner,
        *,
        write: Callable[[str], None],
        secrets: set[str],
        verbose: int,
    ) -> None:
        self._inner = inner
        self._write = write
        # Held, not copied: the passwords are resolved during the run, by
        # `operations/database.py::read_remote_credentials`, which is long
        # after this wrapper is built.
        self._secrets = secrets
        self._verbose = verbose

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
        sensitive: bool = False,
    ) -> CommandResult:
        """Run `argv` through the wrapped runner, printing what it did.

        A sensitive command's output is replaced by one line saying so: scrubbing
        cannot protect it, because the secrets in it are not known until the
        caller has parsed it, which is after this method has returned.
        """
        if self._verbose >= 3:
            self._emit(f"$ {' '.join(argv)}")
        result = self._inner.run(
            argv, cwd=cwd, env=env, stdout_path=stdout_path, sensitive=sensitive
        )
        if sensitive:
            # One line rather than nothing: a silent gap reads as a command
            # that printed nothing.
            self._emit("<output withheld: it carries credentials>")
            return result
        for stream in (result.stdout, result.stderr):
            for line in stream.splitlines():
                self._emit(line)
        return result

    def _emit(self, line: str) -> None:
        self._write(f"{_INDENT}{self._scrub(line)}")

    def _scrub(self, line: str) -> str:
        # Longest first: with `abc` and `abcdef` both registered, replacing the
        # shorter one first would leave `***def` of the longer one on screen.
        for secret in sorted(self._secrets, key=len, reverse=True):
            # An empty secret would match between every two characters.
            if secret:
                line = line.replace(secret, "***")
        return line
