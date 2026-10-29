"""Where an error's explanation lives: the slug registry and the link builder.

Every error the CLI prints ends with `More: <link>`, and the link is the
troubleshooting page of the user docs plus an anchor. The anchors are the
GitHub-style slugs of that page's headings, so this module is the engine's
side of a contract the docs repo checks from the other end: it keeps a copy of
`TOPICS` and fails its build when a slug has no heading.

The module imports nothing from `ddev_sync` (`errors.py` imports it lazily to
validate `doc=`), which is what keeps that cycle from forming.
"""

from types import TracebackType

TROUBLESHOOTING_URL = "https://docs.noix.dev/ddev-sync/reference/troubleshooting/"

# Areas are the coarse fallback: one per group of related failures, in the
# order the page lists them. A link to an area lands on the section that
# explains the whole group when no specific entry fits.
AREAS: tuple[str, ...] = (
    "command-line",
    "configuration",
    "targets",
    "connection",
    "preflight",
    "database",
    "files",
    "env",
    "sanitize",
    "setup",
    "deployer",
    "docker-endpoint",
    "init",
    "profiles-and-features",
    "add-on",
)

# Entries are the specific, named failures a raise site (or a doctor row, or a
# shell script) points at explicitly. Grouped by area as the page is.
ENTRIES: tuple[str, ...] = (
    # connection
    "host-key-changed",
    "cannot-reach-the-host-over-ssh",
    "connection-closed-by-the-remote-host",
    "jump-host-refuses-to-forward",
    "shared-connection-directory",
    # preflight
    "remote-path-is-not-readable",
    "deploy-path-does-not-exist",
    "not-enough-free-space",
    "required-table-is-missing",
    "column-named-by-a-fake-rule-is-missing",
    "no-ssh-agent",
    "web-container-not-reachable",
    # database
    "tls-certificate-verification-fails",
    "no-environment-file-on-the-remote",
    "variable-missing-in-the-remote-env-file",
    "database-credentials-missing",
    "wp-cli-cannot-read-wp-config",
    "dump-is-incomplete",
    # env
    "no-environment-file-in-the-project",
    # sanitize
    "malformed-sanitize-switch",
    "unknown-fake-kind-or-setting",
    "sanitize-rule-failed",
    "production-url-not-set",
    # deployer
    "deployer-not-found",
    "several-deployer-hosts",
    # docker-endpoint
    "sync-port-missing",
    "endpoint-uid-or-gid",
    "no-authorized-keys",
    "endpoint-export-env-name-invalid",
    "endpoint-export-env-variable-unset",
    "endpoint-export-env-value-has-a-newline",
    # targets
    "unknown-target",
    "no-targets-defined",
    # init
    "no-terminal-for-init",
    # add-on
    "ssh-agent-switched-off",
    "sidecar-image-not-found",
)

TOPICS: frozenset[str] = frozenset(AREAS + ENTRIES)

# Longest prefix first: the first match wins, so `config.targets` has to be
# tried before `config`, and `cli.init` before `cli`.
_MODULE_AREAS: tuple[tuple[str, str], ...] = (
    ("ddev_sync.config.targets", "targets"),
    ("ddev_sync.config.deployer", "deployer"),
    ("ddev_sync.config", "configuration"),
    ("ddev_sync.ddev", "configuration"),
    ("ddev_sync.engine.plan", "configuration"),
    ("ddev_sync.transport.deployer", "deployer"),
    ("ddev_sync.transport.ssh", "connection"),
    ("ddev_sync.operations.preflight", "preflight"),
    ("ddev_sync.operations.database", "database"),
    ("ddev_sync.operations.files", "files"),
    ("ddev_sync.operations.s3", "files"),
    ("ddev_sync.operations.env", "env"),
    ("ddev_sync.operations.exec", "setup"),
    ("ddev_sync.operations.sanitize", "sanitize"),
    ("ddev_sync.operations.fake", "sanitize"),
    ("ddev_sync.operations.catalog", "sanitize"),
    ("ddev_sync.operations.placeholders", "sanitize"),
    ("ddev_sync.profiles", "profiles-and-features"),
    ("ddev_sync.features", "profiles-and-features"),
    ("ddev_sync.cli.init", "init"),
    ("ddev_sync.cli", "command-line"),
)

# Keyed by class name, not by class: importing `errors` here would close the
# cycle `errors` -> `docs` -> `errors`. Names are looked up along the MRO, so a
# subclass (`DeployerNotFound`, `AmbiguousTargetError`) follows its parent.
_CLASS_AREAS: dict[str, str] = {
    "ConfigError": "configuration",
    "TargetError": "targets",
    "DeployerError": "deployer",
    "ProfileError": "profiles-and-features",
    "FeatureError": "profiles-and-features",
}


def doc_url(slug: str) -> str:
    """The docs link for a slug; refuses one the registry does not know."""
    if slug not in TOPICS:
        raise ValueError(f"unknown docs topic {slug!r}")
    return f"{TROUBLESHOOTING_URL}#{slug}"


def _module_area(module: str) -> str | None:
    for prefix, area in _MODULE_AREAS:
        if module == prefix or module.startswith(prefix + "."):
            return area
    return None


def _raising_area(traceback: TracebackType | None) -> str | None:
    """The area of the innermost frame whose module maps to one.

    Innermost, because that is where the error was decided; the frames above
    it are only callers passing it along. A frame that belongs to ddev-sync
    but maps to nothing (a shared helper such as `operations.base`) or to
    another package is skipped, so the error takes the area of the code that
    called the helper - the part of the tool the user was actually running.
    """
    found: str | None = None
    while traceback is not None:
        name = traceback.tb_frame.f_globals.get("__name__")
        if isinstance(name, str) and name.startswith("ddev_sync."):
            area = _module_area(name)
            if area is not None:
                found = area
        traceback = traceback.tb_next
    return found


def doc_link(error: BaseException) -> str | None:
    """The docs link to print after `error`, or `None` when there is none.

    Most specific first: the slug the raise site named, then the area of the
    module that raised, then the area of the error's class. A bare error from
    nowhere in particular gets no link rather than a misleading one, and an
    interrupt gets none because the user asked for it - there is nothing to
    look up. The class-name check keeps this module free of an `errors` import.
    """
    classes = {cls.__name__ for cls in type(error).__mro__}
    if "SyncInterrupted" in classes:
        return None
    explicit = getattr(error, "doc", None)
    if isinstance(explicit, str):
        return doc_url(explicit)
    area = _raising_area(error.__traceback__)
    if area is not None:
        return doc_url(area)
    for cls in type(error).__mro__:
        # The module check keeps a foreign class that happens to share a name
        # (a library's own `ConfigError`) from borrowing ours.
        area = _CLASS_AREAS.get(cls.__name__) if cls.__module__.startswith("ddev_sync.") else None
        if area is not None:
            return doc_url(area)
    return None
