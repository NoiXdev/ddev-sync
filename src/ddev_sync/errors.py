"""The exception hierarchy. Every error this tool raises deliberately is one of
these, with one deliberate exception: `InterruptedAtPrompt` at the bottom of
this file is a `KeyboardInterrupt`, not a `DdevSyncError`, because it has to
keep travelling like the interrupt it reports (see its own docstring).
"""


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


class DeployerNotFound(DeployerError):
    """Neither the project's `vendor/bin/dep` nor a `dep` on the PATH ran.

    Its own class because it is the one Deployer failure `init` can do
    something about: when `composer.json` already requires Deployer, the
    usual cause is a `vendor/` that was never installed, and installing it is
    a question rather than an edit.
    """


class SyncInterrupted(DdevSyncError):
    """The user interrupted a sync part-way through.

    Its message is a formatted sentence, built by
    `engine.executor._interrupted_message` from what had already completed -
    not the outcomes themselves. The state a half-finished sync leaves behind
    is the only thing worth saying at that moment, and `main` prints this
    string as-is, so the wording is decided where the progress is known
    rather than here.
    """


class InterruptedAtPrompt(KeyboardInterrupt):
    """Ctrl-C arrived while a confirmation prompt was waiting for an answer.

    A `KeyboardInterrupt` subclass, deliberately not a `DdevSyncError`: it must
    keep travelling like an interrupt through anything that does not know about
    it. It exists because a step that is merely *asking* whether it may do
    something destructive has done nothing yet, and the executor cannot see
    that from a bare interrupt - which looks the same as one in the middle of
    the work. Saying so once, where the prompt is, is cheaper and harder to
    get wrong than teaching every step's message about its own prompts.
    """
