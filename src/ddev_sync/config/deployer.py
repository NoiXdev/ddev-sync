"""Parse `dep config --format=json` into a sync target's connection details.

Nothing here executes anything - `transport/deployer.py` runs the command and
hands the text over. That split is the same one `transport/ssh.py` makes, and
it is what lets every shape this parser has to survive be a string in a test
instead of a project on a disk.

What the real output forces, all three verified against a TYPO3 13 project
deployed with Deployer 7.5:

- `port` arrives as JSON null rather than absent, so "not set" is a value that
  has to be recognised rather than a key that is missing.
- A single key's value can be an error object - `{"error": "..."}` - while the
  command itself exits zero. So a value that is neither a string nor null is
  not a crash and not usable: for a key this parser reads it is a failure with
  Deployer's own text, and for one it does not read it is ignored.
- `shared_path` is null even on a remote that plainly has a shared directory,
  which is why the layout is derived from `current_path` and `deploy_path`
  alone.
"""

import json
import posixpath
from dataclasses import dataclass
from typing import Any

from ddev_sync.errors import DeployerError

# How much of unparseable output is quoted back. Enough to recognise a usage
# banner or a PHP warning, short enough not to bury the sentence around it.
_QUOTED_OUTPUT_LIMIT = 200

_MAX_PORT = 65535


@dataclass(frozen=True)
class DeployerConnection:
    """The four connection values, plus the layout read off the remote's shape."""

    host: str
    # Required, unlike `port`: a direct connection without a user is rejected
    # by `ResolvedTarget` later, with advice about `ssh_alias` that makes no
    # sense for a target whose connection comes from Deployer. A null port, on
    # the other hand, is legitimate - the default of 22 is filled in later.
    user: str
    port: int | None
    path: str
    layout: str


def parse_dep_config(payload: str, alias: str | None) -> DeployerConnection:
    """Read one host's connection out of `dep config --format=json` output."""
    hosts = _hosts(payload)
    name, config = _select(hosts, alias)
    path = _required_text(config, "deploy_path")
    return DeployerConnection(
        host=_text(config, "hostname") or name,
        user=_required_text(config, "remote_user"),
        port=_port(config),
        path=path,
        layout=_layout(config, path),
    )


def _hosts(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        quoted = payload.strip()[:_QUOTED_OUTPUT_LIMIT]
        printed = f"it printed: {quoted}" if quoted else "it printed nothing"
        raise DeployerError(f"`dep config --format=json` did not print JSON; {printed}") from None
    if not isinstance(data, dict):
        raise DeployerError(
            "`dep config --format=json` should print a mapping of hosts, "
            f"but printed a JSON {type(data).__name__}"
        )
    return data


def _select(hosts: dict[str, Any], alias: str | None) -> tuple[str, dict[str, Any]]:
    if not hosts:
        raise DeployerError("Deployer reports no hosts; declare one in deploy.php or deploy.yaml")
    found = ", ".join(hosts)
    if alias is None:
        if len(hosts) > 1:
            raise DeployerError(
                f"Deployer declares several hosts ({found}); "
                "name the one to use with `deployer: {host: <alias>}`"
            )
        name = next(iter(hosts))
    elif alias not in hosts:
        raise DeployerError(f"Deployer has no host {alias!r}; the hosts it declares are: {found}")
    else:
        name = alias
    config = hosts[name]
    if not isinstance(config, dict):
        raise DeployerError(f"Deployer's configuration for host {name!r} is not a mapping")
    return name, config


def _value(config: dict[str, Any], key: str) -> Any:
    """A key's raw value, refusing the error object Deployer puts there on failure."""
    value = config.get(key)
    if isinstance(value, dict):
        raise DeployerError(f"Deployer could not resolve {key!r}: {value.get('error', value)}")
    return value


def _text(config: dict[str, Any], key: str) -> str | None:
    value = _value(config, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeployerError(f"Deployer's {key!r} should be a string, but is {value!r}")
    return value


def _required_text(config: dict[str, Any], key: str) -> str:
    value = _text(config, key)
    if not value or not value.strip():
        raise DeployerError(f"Deployer has no {key!r} for this host; set it in the host's config")
    return value


def _port(config: dict[str, Any]) -> int | None:
    """`None` for null; an integer, or a string of ASCII digits, in 1-65535.

    Deployer serialises whatever PHP value `deploy.php` set, so a port taken
    from `getenv()` or written as `port: "2222"` in YAML arrives as a string.
    That is accepted - but only a strict digit string, never the loose
    stringification of a null that once produced the text "None" as a port.
    `type(value) is int` rather than `isinstance`, so a JSON `true` (an `int`
    subclass in Python) is not read as port 1.
    """
    value = _value(config, "port")
    if value is None:
        return None
    if type(value) is int:
        port = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        port = int(value)
    else:
        raise DeployerError(f"Deployer's 'port' should be an integer, but is {value!r}")
    if not 1 <= port <= _MAX_PORT:
        raise DeployerError(
            f"Deployer's 'port' should be between 1 and {_MAX_PORT}, but is {value!r}"
        )
    return port


def _layout(config: dict[str, Any], path: str) -> str:
    """`deployer` when `current_path` is `deploy_path` + `current`, else `plain`.

    `shared_path` is deliberately not consulted: it is null on remotes that
    plainly have a shared directory. These are remote paths, so `posixpath` and
    never `os.path`, and both sides are normalised because `deploy_path` may or
    may not carry a trailing slash.
    """
    current = _text(config, "current_path")
    if current is None:
        raise DeployerError(
            "could not determine the layout: Deployer has no 'current_path' for this host"
        )
    expected = posixpath.join(posixpath.normpath(path), "current")
    return "deployer" if posixpath.normpath(current) == expected else "plain"
