"""What phpdotenv reads from a file, modelled apart from the code under test.

A port of `Lines::process` and `EntryParser` from vlucas/phpdotenv 5.7, without
resolving variables: a reference comes back as written. The line grouping matters
as much as the value parsing, since it ignores quote style and looks at a whole
line. A line holding `="` that does not also close starts a multi-line value, even
inside single quotes.

A file phpdotenv refuses raises an AssertionError naming `InvalidFileException`.
"""

import re

_TRIMMED = " \n\r\t\0\x0b"
_SPACE = " \t\n\r\v\f"
_LINE_BREAK = re.compile(r"\r\n|\n|\r")
_UNESCAPED_QUOTE = re.compile(r'(?=[^\\]")')
_NAME = re.compile(r"[a-zA-Z0-9_.]+")
_NAMED_ESCAPES = {"f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _looks_like_stop(line: str, *, started: bool) -> bool:
    if line == '"':
        return True
    count = len(_UNESCAPED_QUOTE.findall(line.replace("\\\\", "")))
    return count > 1 if started else count >= 1


def _looks_like_start(line: str) -> bool:
    position = line.find('="')
    if position < 0:
        return False
    comment = line.find("#")
    if 0 <= comment < position:
        return False
    return not _looks_like_stop(line, started=True)


def group_lines(text: str) -> list[str]:
    """`Lines::process`: one string per entry, comments and blank lines gone."""
    entries: list[str] = []
    buffer: list[str] = []
    for line in _LINE_BREAK.split(text):
        starts = not buffer and _looks_like_start(line)
        if not buffer and not starts:
            if line.strip(_TRIMMED) and not line.strip(_TRIMMED).startswith("#"):
                entries.append(line)
            continue
        buffer.append(line)
        if _looks_like_stop(line, started=starts):
            entries.append("\n".join(buffer))
            buffer = []
    return entries


def _refuse(reason: str, entry: str) -> AssertionError:
    return AssertionError(f"InvalidFileException: {reason} at [{entry}]")


def _parse_name(raw: str) -> str:
    name = raw
    if len(name) > 6 and name.startswith("export") and name[6] in _SPACE:
        name = name[6:].lstrip(_SPACE)
    if len(name) >= 3 and name[0] == name[-1] and name[0] in "\"'":
        name = name[1:-1]
    if _NAME.fullmatch(name) is None:
        raise _refuse("an invalid name", name)
    return name


def _parse_value(raw: str) -> str:  # noqa: C901, PLR0912
    """The transducer of `EntryParser::processToken`, one character at a time."""
    state = "initial"
    value = ""
    for char in raw:
        if state == "initial":
            if char in "'\"":
                state = "single" if char == "'" else "double"
            elif char == "#":
                state = "comment"
            else:
                value += char
                state = "unquoted"
        elif state == "unquoted":
            if char == "#":
                state = "comment"
            elif char in _SPACE:
                state = "whitespace"
            else:
                value += char
        elif state == "single":
            if char == "'":
                state = "whitespace"
            else:
                value += char
        elif state == "double":
            if char == '"':
                state = "whitespace"
            elif char == "\\":
                state = "escape"
            else:
                value += char
        elif state == "escape":
            if char in '"\\$':
                value += char
            elif char in _NAMED_ESCAPES:
                value += _NAMED_ESCAPES[char]
            else:
                raise _refuse("an unexpected escape sequence", raw)
            state = "double"
        elif state == "whitespace":
            if char == "#":
                state = "comment"
            elif char not in _SPACE:
                raise _refuse("unexpected whitespace", raw)
    if state in ("single", "double", "escape"):
        raise _refuse("a missing closing quote", raw)
    return value


def load_like_phpdotenv(text: str) -> dict[str, str]:
    """The names and values phpdotenv reads from `text`, the last assignment winning."""
    found: dict[str, str] = {}
    for entry in group_lines(text):
        name, separator, raw = entry.partition("=")
        if not separator:
            raise _refuse("an invalid name", _parse_name(name.strip(_TRIMMED)))
        found[_parse_name(name.strip(_TRIMMED))] = _parse_value(raw.strip(_TRIMMED))
    return found
