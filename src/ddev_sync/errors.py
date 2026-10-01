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


class DetectionError(DdevSyncError):
    """Project type or framework version could not be determined."""
