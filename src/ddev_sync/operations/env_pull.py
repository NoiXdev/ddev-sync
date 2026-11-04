"""Read the values `env.pull` names from the remote, and refuse what cannot come.

Two sources, and they are written differently because they mean different
things. A remote `.env` already is text a dotenv loader reads, so its right-hand
side is carried over as written, quotes and all: decoding and re-encoding it
could only change what phpdotenv makes of it. The handover holds the value
itself, so it is encoded here into a form phpdotenv reads back as that value.

A value leaves this module only in `RemoteValues.text` and `Assignment.value`,
whose `repr` leaves it out, for the same reason `RemoteCredentials` redacts its
password.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from ddev_sync.config.env_pull import names_to_read, pull_names
from ddev_sync.config.models import Provider
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    HANDOVER_PATH,
    fetch_remote_env_file,
    parse_handover,
)

# The line an endpoint under `all` writes to name what it left out (the endpoint's
# entrypoint holds the writing side).
_SKIPPED_LINE = "DDEV_SYNC_SKIPPED"

EXAMPLE_FILE = ".env.example"

_BARE_VALUE = re.compile(r"[A-Za-z0-9_./:@+=,-]*")

# The forms phpdotenv accepts as a declaration; see `_DECLARATION` in
# `operations/env.py`, which has no use for the value and so does not capture it.
_ASSIGNMENT = re.compile(
    r"[ \t]*(?:export[ \t]+)?['\"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['\"]?\s*="
    r"[ \t]*(?P<value>.*)"
)

# What is left of a quoted value up to its closing quote, as phpdotenv reads it
# (`EntryParser::processToken`): in double quotes a backslash takes the character
# after it along, and in single quotes it is an ordinary character, so `'C:\'`
# is closed.
_CLOSES = {
    '"': re.compile(r'(?:[^"\\]|\\.)*"'),
    "'": re.compile(r"[^']*'"),
}

_ESCAPE = re.compile(r"\\.")
_COMMENT = re.compile(r"(?:^|\s)#")


@dataclass(frozen=True)
class RemoteValues:
    """What the remote has for the names asked about.

    `text` holds each right-hand side ready for the local file, and only for the
    names asked about. Every list keeps the order the names were asked in.
    """

    path: str
    text: dict[str, str] = field(repr=False)
    missing: list[str]
    multi_line: list[str]
    references: list[str]


@dataclass(frozen=True)
class Assignment:
    """One name's assignment in an env file, as many lines as it takes.

    `first` and `last` are indexes into the lines scanned, `last` included. `head`
    is what stands ahead of the value on the first line, and `value` the rest of
    that line, trimmed. `closed` is false for a value whose opening quote closes
    nowhere, which then takes only its own line.
    """

    name: str
    first: int
    last: int
    head: str
    value: str = field(repr=False)
    closed: bool


def logical_assignments(lines: Sequence[str]) -> Iterator[Assignment]:
    """Every assignment in the lines of an env file, in order.

    An opening quote that does not close on its line makes a multi-line value, and
    the lines up to its close belong to it: one of them looking like `B=2` assigns
    nothing. What a quote never closed means is left to the caller, so the scan goes
    on behind it.

    Trimmed of what a loader trims: a bare `strip` also takes characters such as a
    form feed, which here can be part of the value.
    """
    position = 0
    while position < len(lines):
        line = lines[position]
        match = _ASSIGNMENT.match(line)
        if match is None:
            position += 1
            continue
        value = match["value"].strip(" \t\r")
        quote = value[:1]
        last: int | None = position
        if quote in _CLOSES and _CLOSES[quote].match(value, 1) is None:
            last = next(
                (
                    later
                    for later in range(position + 1, len(lines))
                    if _CLOSES[quote].match(lines[later]) is not None
                ),
                None,
            )
        yield Assignment(
            name=match["name"],
            first=position,
            last=position if last is None else last,
            head=line[: match.start("value")],
            value=value,
            closed=last is not None,
        )
        position = (position if last is None else last) + 1


def encode_for_env_file(value: str) -> str:
    """Write a verbatim value so that phpdotenv reads back exactly that value.

    In single quotes phpdotenv takes everything literally up to the first `'`, with
    no escapes at all, so they serve any value that does not hold one, nor a `="`:
    `Lines::looksLikeMultilineStart` ignores quote style, so `A='x="y'` reads as the
    start of a multi-line value and the whole file fails to parse. Those go in double
    quotes, where `\\`, `"` and `$` need a backslash to stay themselves, and the
    escaped quote no longer reads as `="`.
    """
    if _BARE_VALUE.fullmatch(value) is not None:
        return value
    if "'" not in value and '="' not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def raw_assignments(contents: str) -> tuple[dict[str, str], set[str]]:
    """Each name's right-hand side as written, and the names whose value spans lines.

    The last assignment of a name decides, as it does in phpdotenv. A quote never
    closed takes the rest of the file with it: a pulled value has to be a value
    the remote really has, so nothing behind that line can count.

    Split on line feeds alone: `splitlines` also ends a line at characters such as
    a form feed, which here can be part of the value.
    """
    text: dict[str, str] = {}
    multi_line: set[str] = set()
    for entry in logical_assignments(contents.split("\n")):
        text.pop(entry.name, None)
        multi_line.discard(entry.name)
        if entry.closed and entry.first == entry.last:
            text[entry.name] = entry.value
            continue
        multi_line.add(entry.name)
        if not entry.closed:
            break
    return text, multi_line


def _refers_to_a_variable(raw: str) -> bool:
    """Whether a raw right-hand side has a `$` that a loader would act on.

    Not in single quotes, not escaped, and not in the comment behind the value. A
    `$` that names nothing still counts: this only decides whether a notice is
    printed, and what counts as a reference differs between the loaders.
    """
    plain = _ESCAPE.sub("", raw)
    if plain.startswith("'"):
        return False
    if plain.startswith('"'):
        value = plain[1:].partition('"')[0]
    else:
        value = _COMMENT.split(plain, maxsplit=1)[0]
    return "$" in value


def read_remote_values(context: ExecutionContext, names: Sequence[str]) -> RemoteValues:
    """Look `names` up in the file the credentials come from, in one round trip.

    The sources are not merged: a mounted `.env` hides the handover entirely, so
    a name it lacks is missing even when the endpoint would hand it over.
    """
    path, contents = fetch_remote_env_file(context)
    if path == HANDOVER_PATH:
        parsed = parse_handover(contents, path)
        text = {name: encode_for_env_file(value) for name, value in parsed.items()}
        multi_line = set(parsed.get(_SKIPPED_LINE, "").split(","))
        references: set[str] = set()
    else:
        text, multi_line = raw_assignments(contents)
        references = {name for name, raw in text.items() if _refers_to_a_variable(raw)}
    return RemoteValues(
        path=path,
        text={name: text[name] for name in names if name in text},
        missing=[name for name in names if name not in text and name not in multi_line],
        multi_line=[name for name in names if name in multi_line],
        references=[name for name in names if name in references],
    )


def check_pull(context: ExecutionContext) -> None:
    """Refuse a run whose `env.pull` cannot be honoured, listing every problem at once.

    The env step runs after the import, so finding out there would leave
    production data local and the application still not running. The first
    problem's slug is the link: one error carries one link. Only names go into
    the message, never a value.
    """
    if not pull_names(context.merged.data):
        return
    problems: list[tuple[str, str]] = []
    # A name `env.set` keeps is not read: a value that would never be used cannot
    # refuse the run. It still lets the step create a file, so the local check stays.
    names = names_to_read(context.merged.data)
    if names:
        context.progress("reading the values env.pull names")
        values = read_remote_values(context, names)
        if values.missing:
            problems.append((_missing_problem(context, values), "env-pull-variable-missing"))
        if values.multi_line:
            problems.append(
                (_line_break_problem(context, values), "env-pull-value-has-a-line-break")
            )
    local = _local_file_problem(context)
    if local is not None:
        problems.append((local, "env-pull-no-example-file"))
    if problems:
        listed = "\n".join(f"- {problem}" for problem, _ in problems)
        raise OperationError(
            f"env.pull cannot be honoured. Nothing has been changed.\n{listed}",
            doc=problems[0][1],
        )


def _missing_problem(context: ExecutionContext, values: RemoteValues) -> str:
    problem = f"not set in {values.path} on {context.target.host}: {', '.join(values.missing)}. "
    if context.target.provider != Provider.DOCKER:
        return f"{problem}Set them there, or take them out of env.pull."
    if values.path == HANDOVER_PATH:
        return f"{problem}Add them to DDEV_SYNC_EXPORT_ENV on the endpoint, or set it to all."
    return (
        f"{problem}Under provider: docker the mounted file wins over what the endpoint "
        "hands over, so the mount has to go or hold them."
    )


def _line_break_problem(context: ExecutionContext, values: RemoteValues) -> str:
    return (
        f"the value has a line break in {values.path} on {context.target.host}: "
        f"{', '.join(values.multi_line)}. A pulled value has to fit on one line, so set "
        "them locally by hand and take them out of env.pull."
    )


def _local_file_problem(context: ExecutionContext) -> str | None:
    """The problem when the env step would have no file to write and none to copy.

    Reads `env.file` as `EnvRewriteOperation._find_env_file` does, trusting the same
    planner gate, and matches directory entries for the reason its docstring gives:
    `is_file()` on a constructed path folds case on macOS.
    """
    configured = context.merged.data["env"].get("file", [".env.local", ".env"])
    candidates = [configured] if isinstance(configured, str) else [str(n) for n in configured]
    existing = {entry.name for entry in context.project.root.iterdir() if entry.is_file()}
    if EXAMPLE_FILE in existing or not existing.isdisjoint(candidates):
        return None
    return (
        f"no environment file in the project to write them to; looked at: "
        f"{', '.join(candidates)}, and there is no {EXAMPLE_FILE} to create one from."
    )
