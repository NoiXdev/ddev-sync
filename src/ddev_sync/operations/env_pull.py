"""Read the values `env.pull` names from the remote, and refuse what cannot come.

Two sources, and they are written differently because they mean different
things. A remote `.env` already is text a dotenv loader reads, so its right-hand
side is carried over as written, quotes and all: decoding and re-encoding it
could only change what phpdotenv makes of it. The handover holds the value
itself, so it is encoded here into a form phpdotenv reads back as that value.

No value leaves this module except in `RemoteValues.text`, whose `repr` leaves it
out, for the same reason `RemoteCredentials` redacts its password.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ddev_sync.config.env_pull import pull_names
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

_EXAMPLE_FILE = ".env.example"

_BARE_VALUE = re.compile(r"[A-Za-z0-9_./:@+=,-]*")

# The forms phpdotenv accepts as a declaration; see `_DECLARATION` in
# `operations/env.py`, which has no use for the value and so does not capture it.
_ASSIGNMENT = re.compile(
    r"[ \t]*(?:export[ \t]+)?['\"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['\"]?\s*=(?P<value>.*)"
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


def encode_for_env_file(value: str) -> str:
    """Write a verbatim value so that phpdotenv reads back exactly that value.

    In single quotes phpdotenv takes everything literally up to the first `'`, with
    no escapes at all, so they serve any value that does not hold one. A value that
    does goes in double quotes, where `\\`, `"` and `$` need a backslash to stay
    themselves.
    """
    if _BARE_VALUE.fullmatch(value) is not None:
        return value
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def raw_assignments(contents: str) -> tuple[dict[str, str], set[str]]:
    """Each name's right-hand side as written, and the names whose value spans lines.

    The last assignment of a name decides, as it does in phpdotenv. An opening
    quote that does not close on its line makes a multi-line value, and the lines
    up to its close belong to it: one of them looking like `B=2` assigns nothing.

    Split on line feeds alone, and trimmed of what a loader trims: `splitlines`
    and a bare `strip` also take characters such as a form feed, which here can
    be part of the value.
    """
    text: dict[str, str] = {}
    multi_line: set[str] = set()
    open_quote: str | None = None
    for line in contents.split("\n"):
        if open_quote is not None:
            if _CLOSES[open_quote].match(line) is not None:
                open_quote = None
            continue
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        name = match["name"]
        value = match["value"].strip(" \t\r")
        text.pop(name, None)
        multi_line.discard(name)
        quote = value[:1]
        if quote in _CLOSES and _CLOSES[quote].match(value, 1) is None:
            multi_line.add(name)
            open_quote = quote
        else:
            text[name] = value
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
    names = pull_names(context.merged.data)
    if not names:
        return
    context.progress("reading the values env.pull names")
    values = read_remote_values(context, names)
    problems: list[tuple[str, str]] = []
    if values.missing:
        problems.append((_missing_problem(context, values), "env-pull-variable-missing"))
    if values.multi_line:
        problems.append((_line_break_problem(context, values), "env-pull-value-has-a-line-break"))
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
    if _EXAMPLE_FILE in existing or not existing.isdisjoint(candidates):
        return None
    return (
        f"no environment file in the project to write them to; looked at: "
        f"{', '.join(candidates)}, and there is no {_EXAMPLE_FILE} to create one from."
    )
