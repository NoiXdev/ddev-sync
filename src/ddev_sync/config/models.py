"""Pydantic models for sync targets, before and after resolution."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Stage(StrEnum):
    """Which kind of environment a target points at."""

    PRODUCTION = "production"
    STAGING = "staging"
    TESTING = "testing"
    DEV = "dev"


class Layout(StrEnum):
    """Where code and persistent data sit on the remote, relative to `path`.

    This is a property of the deployment, not of the framework (spec §4.4.1):
    a TYPO3 is a TYPO3 whether it was rolled out with Deployer, `git pull`, or
    rsync. `PLAIN` is the default because the tool is generic and Deployer's
    `releases/current/shared` layout is one convention among several.
    """

    PLAIN = "plain"
    DEPLOYER = "deployer"


class TargetSpec(BaseModel):
    """One target as written in a config file.

    Every field is optional: a target in the project layer may supply only
    `path` and inherit its connection details via `extends` from the global
    layer. Completeness is checked during resolution, not here.
    """

    model_config = ConfigDict(extra="forbid")

    extends: str | None = None
    host: str | None = None
    ssh_alias: str | None = None
    user: str | None = None
    port: int = 22
    path: str | None = None
    stage: Stage | None = None
    layout: Layout | None = None
    jump_host: str | None = None
    jump_host_user: str | None = None
    jump_host_port: int = 22


class ResolvedTarget(BaseModel):
    """A target after all layers have been merged and validated.

    When `via_ssh_alias` is true, `host` is an alias from the user's
    `~/.ssh/config` and `user`/`port` are deliberately absent: ssh resolves
    them, and second-guessing it would defeat the point of the alias.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    host: str
    path: str
    stage: Stage
    layout: Layout = Layout.PLAIN
    via_ssh_alias: bool = False
    user: str | None = None
    port: int | None = None
    jump_host: str | None = None
    jump_host_user: str | None = None
    jump_host_port: int = 22

    @field_validator("path", mode="after")
    @classmethod
    def _path_ends_with_exactly_one_separator(cls, path: str) -> str:
        """Every call site joins `path` with a relative suffix by
        concatenation, not `os.path.join`, so the trailing separator is an
        invariant this model must guarantee rather than one every caller has
        to remember to normalise.
        """
        return f"{path.rstrip('/')}/"

    @property
    def code_root(self) -> str:
        """Where the deployed code lives - framework CLIs run here.

        `path` already carries exactly one trailing separator, and appending
        a segment that itself ends in `/` preserves that invariant without a
        second normalisation step.
        """
        if self.layout is Layout.DEPLOYER:
            return f"{self.path}current/"
        return self.path

    @property
    def data_root(self) -> str:
        """Where persistent user data lives - files and the env file."""
        if self.layout is Layout.DEPLOYER:
            return f"{self.path}shared/"
        return self.path

    @model_validator(mode="after")
    def _direct_connections_need_user_and_port(self) -> "ResolvedTarget":
        if self.via_ssh_alias:
            return self
        missing = [
            field for field, value in (("user", self.user), ("port", self.port)) if value is None
        ]
        if missing:
            raise ValueError(
                f"target {self.name!r} is missing {', '.join(missing)}; "
                "set them, or use ssh_alias to let ~/.ssh/config supply them"
            )
        return self
