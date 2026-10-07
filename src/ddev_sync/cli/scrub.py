"""Remove registered secrets from a line that is about to be printed.

One function, in its own module, because two presentation-layer users need
exactly it: `cli/verbose.py` for the commands and output it narrates, and
`cli/reporter.py` for every line it writes. They used to be one
implementation and no implementation - the reporter's lines were covered by
`operations/base.py::redact` calls at the two error paths that were known to
carry a password, which is a convention rather than a guarantee.

`operations/base.py::redact` stays where it is. It runs earlier and for a
different reason: it takes one known password out of a command's stderr
before that text becomes an exception message. Both running is harmless -
text with the password already gone has nothing left to replace.
"""

from collections.abc import Iterable

PLACEHOLDER = "***"


def scrub(text: str, secrets: Iterable[str]) -> str:
    """Replace every secret in `text` with `PLACEHOLDER`.

    Longest first: with `abc` and `abcdef` both registered, replacing the
    shorter one first would leave `***def` of the longer one on screen.

    An empty secret is skipped. `str.replace("", x)` inserts `x` between
    every two characters, which would garble the message exactly when a
    caller most needs to read it - and a target with an empty password (not a
    missing one) is a real, if unusual, configuration.
    """
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, PLACEHOLDER)
    return text
