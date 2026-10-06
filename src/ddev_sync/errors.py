"""The exception hierarchy. Every error this tool raises deliberately is one of these."""


class DdevSyncError(Exception):
    """Base class for every error ddev-sync raises on purpose."""


class ConfigError(DdevSyncError):
    """A configuration file is missing, unreadable, or invalid."""


class ProfileError(DdevSyncError):
    """A profile could not be loaded or resolved."""


class FeatureError(DdevSyncError):
    """A feature could not be loaded, resolved or activated."""


class TargetError(DdevSyncError):
    """A sync target could not be resolved."""


class AmbiguousTargetError(TargetError):
    """No target was named and the project does not imply exactly one.

    Its own class because it is the only target error a caller can act on
    without the user changing a file: asking which target to use answers it,
    and nothing else. Every other `TargetError` describes configuration that
    is wrong whichever target is picked, so retrying the resolution with a
    name would reach the same error after repeating all the work - including,
    for a `deployer:` target, a second evaluation of the project's PHP.
    """


class DetectionError(DdevSyncError):
    """Project type or framework version could not be determined."""


class DeployerError(DdevSyncError):
    """The project's Deployer configuration could not be read or understood."""
