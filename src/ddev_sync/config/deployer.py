"""A project's Deployer connection; the parser lands here in the next task."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeployerConnection:
    host: str
    user: str | None
    port: int | None
    path: str
    layout: str
