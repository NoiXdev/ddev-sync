"""Interactive selection, isolated behind two thin functions.

`_select` and `_checkbox` exist so tests can replace the whole questionary
dependency with two lambdas instead of driving a pseudo-terminal.
"""

import questionary

DEFAULT_STEPS = ("db", "files", "sanitize")


def _select(question: str, choices: list[str]) -> str | None:
    """Ask the user to pick one of `choices`."""
    answer: str | None = questionary.select(question, choices=choices).ask()
    return answer


def _checkbox(question: str, choices: list[str], defaults: list[str]) -> list[str] | None:
    """Ask the user to tick any of `choices`."""
    options = [questionary.Choice(title=choice, checked=choice in defaults) for choice in choices]
    answer: list[str] | None = questionary.checkbox(question, choices=options).ask()
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
