"""Pydantic models for sync targets, before and after resolution."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class Stage(StrEnum):
    """Which kind of environment a target points at."""

    PRODUCTION = "production"
    STAGING = "staging"
    TESTING = "testing"
    DEV = "dev"


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
    via_ssh_alias: bool = False
    user: str | None = None
    port: int | None = None
    jump_host: str | None = None
    jump_host_user: str | None = None
    jump_host_port: int = 22

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
