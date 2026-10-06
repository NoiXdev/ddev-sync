"""Interactive selection, isolated behind two thin functions.

`_select` and `_checkbox` exist so tests can replace the whole questionary
dependency with two lambdas instead of driving a pseudo-terminal.

Both refuse to prompt when stdin is not a terminal. questionary does not
raise there - it waits on a terminal that will never answer - so a run from a
script, a cron job or CI hung with no output at all, which is the worst way
for a tool to fail. Returning `None` instead lets the callers name the flag
that skips the question.

`None` therefore means exactly one thing: there was no terminal to ask. A
user pressing Ctrl-C is not that. questionary's `ask()` turns the interrupt
into `None` as well, which made a cancelled prompt indistinguishable from
"no terminal" and ended with a message about a missing terminal while the
user was looking at one. `unsafe_ask()` lets the `KeyboardInterrupt` through
instead, and `main` ends it with the same sentence and exit code as any other
Ctrl-C.
"""

import sys

import questionary

DEFAULT_STEPS = ("db", "files", "sanitize")


def _select(question: str, choices: list[str]) -> str | None:
    """Ask the user to pick one of `choices`, or `None` with no terminal."""
    if not sys.stdin.isatty():
        return None
    answer: str = questionary.select(question, choices=choices).unsafe_ask()
    return answer


def _checkbox(question: str, choices: list[str], defaults: list[str]) -> list[str] | None:
    """Ask the user to tick any of `choices`, or `None` with no terminal."""
    if not sys.stdin.isatty():
        return None
    options = [questionary.Choice(title=choice, checked=choice in defaults) for choice in choices]
    answer: list[str] = questionary.checkbox(question, choices=options).unsafe_ask()
    return answer


def choose_target(names: list[str], descriptions: dict[str, str]) -> str | None:
    """Pick a target; returns it directly when there is only one."""
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    labels = {f"{name:<12} {descriptions.get(name, '')}".rstrip(): name for name in names}
    chosen = _select("From which target do you want to sync?", list(labels))
    return None if chosen is None else labels[chosen]


def choose_steps(available: list[str]) -> list[str] | None:
    """Pick which step groups to run."""
    defaults = [name for name in available if name in DEFAULT_STEPS]
    return _checkbox("What do you want to sync?", available, defaults)
