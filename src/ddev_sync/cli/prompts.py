"""Interactive questions, isolated behind four thin functions.

`_select`, `_checkbox`, `_text` and `_confirm` exist so tests can replace the
whole questionary dependency with lambdas instead of driving a pseudo-terminal.

All four refuse to prompt when stdin is not a terminal. questionary does not
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
from collections.abc import Callable

import questionary

from ddev_sync.config.models import Stage
from ddev_sync.operations.placeholders import validate_production_url

DEFAULT_STEPS = ("db", "files", "sanitize")
MANUAL_SOURCE = "Enter the connection by hand"
DEPLOYER_SOURCE = "Read it from Deployer"
ENV_FILE_NONE = "None of these - I will create the file myself"
REMOTE_ENV_NONE = "None of these - I will fix it another way"
EXPLICIT_SOURCE = "Enter the database credentials by hand"
WP_CLI_SOURCE = "Read them from WordPress on the target (wp-cli)"

# What questionary accepts: True to take the answer, or the sentence to show
# before asking again.
Validator = Callable[[str], bool | str]


def _select(question: str, choices: list[str]) -> str | None:
    """Ask the user to pick one of `choices`, or `None` with no terminal."""
    if not sys.stdin.isatty():
        return None
    # A blank line so the question separates from the step lines above it.
    # After the guard, not before: with no terminal there is no question, and
    # a stray newline would then appear in a piped log for no reason.
    print()
    answer: str = questionary.select(question, choices=choices).unsafe_ask()
    return answer


def _checkbox(question: str, choices: list[str], defaults: list[str]) -> list[str] | None:
    """Ask the user to tick any of `choices`, or `None` with no terminal.

    An empty list is a third answer, distinct from both: questionary's
    checkbox configures no validator, so Enter with nothing ticked exits
    with `result=[]`. It is not filtered here - the question "did the user
    choose nothing" belongs where the choice is acted on, and
    `cli.commands.resolve_selection` refuses it there with a sentence that
    names what was on offer.
    """
    if not sys.stdin.isatty():
        return None
    # A blank line so the question separates from the step lines above it.
    # After the guard, not before: with no terminal there is no question, and
    # a stray newline would then appear in a piped log for no reason.
    print()
    options = [questionary.Choice(title=choice, checked=choice in defaults) for choice in choices]
    answer: list[str] = questionary.checkbox(question, choices=options).unsafe_ask()
    return answer


def _text(question: str, default: str, validate: Validator | None = None) -> str | None:
    """Ask for a line of text, or `None` with no terminal.

    `validate` runs inside the prompt, so a refused answer is asked again
    where the user is looking at it instead of surfacing later as an error
    about a value they no longer see.
    """
    if not sys.stdin.isatty():
        return None
    # A blank line so the question separates from the step lines above it.
    # After the guard, not before: with no terminal there is no question, and
    # a stray newline would then appear in a piped log for no reason.
    print()
    answer: str = questionary.text(question, default=default, validate=validate).unsafe_ask()
    return answer


def _confirm(question: str, default: bool = True) -> bool | None:
    """Ask a yes/no question, or `None` with no terminal.

    `default` is what Enter answers; a question that overwrites something
    passes `False`.
    """
    if not sys.stdin.isatty():
        return None
    # A blank line so the question separates from the step lines above it.
    # After the guard, not before: with no terminal there is no question, and
    # a stray newline would then appear in a piped log for no reason.
    print()
    answer: bool = questionary.confirm(question, default=default).unsafe_ask()
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


def confirm_detection(profile: str, source: str) -> bool | None:
    """Ask whether the detected profile is the right one."""
    return _confirm(f"This looks like {profile} (from {source}). Is that right?")


def ask_target_name(default: str = "production") -> str | None:
    """Ask what the new target should be called."""
    return _text("What should the target be called?", default, _required)


def choose_connection_source(deployer_available: bool) -> str | None:
    """Ask where the connection details come from.

    Deployer is offered only when the project has a recipe for it, so a
    project that does not use the tool is never asked about it. The answer is
    `MANUAL_SOURCE` or `DEPLOYER_SOURCE`.
    """
    choices = [MANUAL_SOURCE]
    if deployer_available:
        choices.append(DEPLOYER_SOURCE)
    return _select("Where do the connection details come from?", choices)


def choose_env_file(looked_for: list[str], present: list[str]) -> str | None:
    """Ask which file holds this project's environment, or `ENV_FILE_NONE`.

    `present` is every `.env*` file really in the project root, offered
    because the usual cause of this question is a file under another name.
    `.env.example` is among them when it is there - hiding a file that exists
    is its own kind of lie - but nothing is preselected: patching the example
    is almost never wanted, and the fix for the state that produces it is to
    copy it, not to configure it.
    """
    return _select(
        f"No environment file found (looked for {', '.join(looked_for)}). Which file is it?",
        [*present, ENV_FILE_NONE],
    )


def choose_remote_env_file(
    looked_for: list[str], present: list[str], hint: str | None = None
) -> str | None:
    """Ask which file on the target holds the database credentials.

    `present` is what the target's data root really contains, so the usual
    cause of this question - the file is there under another name - is one
    keystroke away. The opt-out is last because the override branch behind it
    is the heavier answer, and most projects do not need it.

    `hint` is appended to the question when the caller knows a way out the
    choices cannot offer - a docker endpoint's handover, which is not a file
    in the data root to pick from.
    """
    question = (
        f"No credentials file found on the target (looked for {', '.join(looked_for)}). "
        "Which file is it?"
    )
    return _select(
        question if hint is None else f"{question} {hint}",
        [*present, REMOTE_ENV_NONE],
    )


def choose_features(labels: dict[str, str]) -> list[str] | None:
    """Ask which detected features to keep; every one starts ticked.

    `labels` maps a feature name to the reason it was detected, which
    `ActivatedFeature.reason` already phrases ("detected: composer package
    filament/filament"). The reason is shown because the answer is otherwise a
    guess: a name alone does not say what in this checkout matched.

    An empty answer is "none of them", which is a real answer here and not the
    absence of one - see `features/activation.py::parse_feature_key`.
    """
    shown = {f"{name:<24} {labels[name]}".rstrip(): name for name in labels}
    ticked = _checkbox("Which detected features should the sync use?", list(shown), list(shown))
    return None if ticked is None else [shown[choice] for choice in ticked]


def choose_deployer_host(hosts: list[str]) -> str | None:
    """Ask which of the hosts Deployer knows about to use.

    `hosts` must not be empty: questionary refuses a question with no
    choices, and there is nothing to pick from anyway.
    """
    return _select("Which Deployer host?", hosts)


def confirm_composer_install() -> bool | None:
    """Ask whether to install the project's Composer dependencies to get Deployer."""
    return _confirm(
        "Deployer is in composer.json but not installed. "
        "Run 'composer install --no-scripts' in the web container now?"
    )


def ask_stage() -> str | None:
    """Ask which kind of environment the target is."""
    return _select("Which stage is it?", [stage.value for stage in Stage])


def confirm_replacement(name: str, label: str, existing: str) -> bool | None:
    """Ask before replacing a target the chosen file already defines.

    `existing` is a short description of what is there now, so the answer is
    informed. Enter says no: the safe answer is the one that overwrites nothing.
    """
    return _confirm(
        f"{name} is already defined in {label} ({existing}). Replace it?", default=False
    )


def ask_production_url() -> str | None:
    """Ask what URL the site serves in production.

    Validated through the same function the placeholder resolver uses, so a
    value this question accepts is one a sync can use, and the wording of a
    rejection is written once.
    """
    return _text("What URL does this site serve in production?", "", _valid_production_url)


def _valid_production_url(answer: str) -> bool | str:
    problem = validate_production_url(answer.strip())
    return True if problem is None else f"{problem[0].upper()}{problem[1:]}."


def _required(answer: str) -> bool | str:
    return True if answer.strip() else "This cannot be empty."


def _valid_port(answer: str) -> bool | str:
    try:
        port = int(answer)
    except ValueError:
        port = 0
    return True if 1 <= port <= 65535 else "The port is a number from 1 to 65535."


# key, question, default, validator, conversion. The port is checked in the
# question and converted here: an `int(...)` that raised later on a typo would
# not be a `DdevSyncError` and would reach the user as a traceback.
_CONNECTION_QUESTIONS: tuple[tuple[str, str, str, Validator, Callable[[str], object]], ...] = (
    ("host", "Host?", "", _required, str.strip),
    ("user", "User?", "", _required, str.strip),
    ("port", "Port?", "22", _valid_port, int),
    ("path", "Remote path?", "", _required, str.strip),
)


def ask_connection() -> dict[str, object] | None:
    """Ask for a connection entered by hand, or `None` with no terminal."""
    answers: dict[str, object] = {}
    for key, question, default, validate, convert in _CONNECTION_QUESTIONS:
        answer = _text(question, default, validate)
        if answer is None:
            return None
        answers[key] = convert(answer)
    return answers


def choose_credentials_source(wordpress: bool) -> str | None:
    """Ask where the credentials should come from instead.

    `wp_cli` is offered only for a WordPress profile, the way the Deployer
    import is offered only to a project with a recipe for it: a question
    nobody on this project could answer is a question not worth asking.
    """
    choices = [EXPLICIT_SOURCE]
    if wordpress:
        choices.insert(0, WP_CLI_SOURCE)
    return _select("Where should the credentials come from?", choices)


_CREDENTIAL_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("name", "Database name?"),
    ("user", "Database user?"),
    ("password", "Database password?"),
    ("host", "Database host?"),
    ("port", "Database port?"),
)


def ask_credentials() -> dict[str, str] | None:
    """Ask for the five values an `explicit` source needs.

    No `questionary.password` for the password field: the value is about to be
    written to a file the user can read, the terminal is theirs, and hiding it
    would stop them checking a typo against the one thing that matters here -
    whether it is the password production really uses. The probe that follows
    is the real check.
    """
    answers: dict[str, str] = {}
    for key, question in _CREDENTIAL_QUESTIONS:
        answer = _text(question, "", _required)
        if answer is None:
            return None
        answers[key] = answer.strip()
    return answers
