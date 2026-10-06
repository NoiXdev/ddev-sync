"""Pydantic models for sync targets, before and after resolution."""

from enum import StrEnum
from typing import Any

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


class DeployerRef(BaseModel):
    """A reference to a host in the project's own Deployer configuration.

    `host` is Deployer's *host alias*, which is not necessarily a hostname: a
    project may name its hosts `production` and `staging`. It may be omitted
    when `dep config` reports exactly one host.
    """

    model_config = ConfigDict(extra="forbid")

    host: str | None = None

    @field_validator("host")
    @classmethod
    def _an_alias_has_to_name_a_host(cls, value: str | None) -> str | None:
        # A blank or whitespace-only alias is not the same as omitting the
        # key. Left to stand it produces two bad outcomes at once: the
        # listing renders `from deployer: ` with a dangling separator, and
        # resolution then fails with `Deployer has no host ''`, which names
        # the symptom and not the mistake. Rejecting it here reports what is
        # actually wrong, in the file where it was written.
        if value is not None and not value.strip():
            raise ValueError(
                "'deployer.host' must name a host in the project's Deployer "
                "configuration; omit the key entirely to let a single host be used"
            )
        return value


# What a `deployer:` block supplies, and therefore what may not be written
# beside it. `jump_host` and its siblings are deliberately absent: Deployer
# has no canonical field for a bastion, so the block has nothing to supply
# there and no conflict is possible.
DEPLOYER_OWNED_KEYS = ("host", "ssh_alias", "user", "port", "path", "layout")


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
    deployer: DeployerRef | None = None

    @model_validator(mode="before")
    @classmethod
    def _deployer_block_accepts_an_empty_value(cls, data: Any) -> Any:
        # `deployer:` with no value parses as None, which pydantic would then
        # read as "the field is absent" - indistinguishable from a target that
        # never mentioned Deployer at all. The two mean different things, so
        # the null is normalised to an empty ref before validation.
        #
        # The key being present is all this can see, so it cannot tell that
        # YAML shape from a Python caller passing `deployer=None` on purpose:
        # `TargetSpec(**{**spec_dict, "deployer": None})` to *clear* a block
        # sets an empty one instead. That is deliberate for the shape it was
        # written for - a config file can only say it the YAML way - but in
        # code the way to clear a block is to omit the key, not to null it.
        if isinstance(data, dict) and "deployer" in data and data["deployer"] is None:
            data = {**data, "deployer": {}}
        return data

    @model_validator(mode="after")
    def _deployer_owns_the_connection(self) -> "TargetSpec":
        if self.deployer is None:
            return self
        # `model_fields_set`, not a comparison with None: `port` defaults to
        # 22, so its value cannot say whether the user wrote it, and a block
        # with no port beside it must not be reported as conflicting.
        owned = [name for name in DEPLOYER_OWNED_KEYS if name in self.model_fields_set]
        if owned:
            raise ValueError(
                f"a 'deployer:' block owns the connection, so {', '.join(owned)} "
                "must not be set beside it; remove them, or remove the block"
            )
        return self


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
