"""Ask the project's own Deployer where it deploys.

The only part of this feature that runs anything. It runs in the project's web
container, not on the host and not in the sync sidecar: the sidecar carries no
PHP by design, so `vendor/bin/dep` is visible from it but not executable, and
the host's PHP is not the one the project pinned. Framework commands already
run there for the same reason.

The result is memoised for the life of the resolver, which is one run. A single
`dep config` returns every host, so a second target asking for a second alias
is served from the same invocation - and `dep config` evaluates the project's
recipe, so repeating it would be both slow and a second chance to get a
different answer inside one sync.
"""

from ddev_sync.config.deployer import DeployerConnection, list_dep_hosts, parse_dep_config
from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import DeployerError, DeployerNotFound
from ddev_sync.transport.container import web_command
from ddev_sync.transport.runner import CommandRunner

# The project's own pinned Deployer first, then whatever the container's PATH
# offers. Looking for a tool in two places is not the same as substituting a
# value, which is why this is the one alternative the strict rule allows.
_CANDIDATES = ("vendor/bin/dep", "dep")

_SUBCOMMAND = ("config", "--format=json")

# What a shell - and `docker exec` after it - exits with when it cannot find
# the program. It is the only status that means "try the next place". Any
# other non-zero is usually `dep` itself reporting that the project's recipe
# failed, but not always: `docker exec` against a stopped web container exits
# 1 with "container ... is not running", which looks the same from here. That
# is why the message below quotes the command's own stderr instead of naming
# a cause it cannot tell apart.
_NOT_FOUND = 127


class ProjectDeployer:
    """Resolves `deployer: {host: <alias>}`, and lists the hosts Deployer declares.

    Both read the same `dep config` output, which is fetched on first use and
    kept. `init` lists the hosts to choose from and the check that follows
    resolves the one chosen; with two fetches the project's recipe would be
    evaluated twice, once more than anything needs.
    """

    def __init__(self, project: DdevProject, runner: CommandRunner) -> None:
        self._project = project
        self._runner = runner
        # `None` means "not asked yet", so an empty output is kept as the
        # answer it is instead of being asked for again.
        self._text: str | None = None

    def __call__(self, alias: str | None) -> DeployerConnection:
        return parse_dep_config(self._config(), alias)

    def hosts(self) -> list[str]:
        """The host aliases Deployer declares, in the order it printed them."""
        return list_dep_hosts(self._config())

    def _config(self) -> str:
        if self._text is None:
            self._text = _dep_config(self._project, self._runner)
        return self._text


def make_deployer_resolver(project: DdevProject, runner: CommandRunner) -> ProjectDeployer:
    """Build a resolver for `deployer: {host: <alias>}`, running `dep` at most once."""
    return ProjectDeployer(project, runner)


def _dep_config(project: DdevProject, runner: CommandRunner) -> str:
    """The text `dep config --format=json` printed, from the first place it exists."""
    last_detail = ""
    for candidate in _CANDIDATES:
        # The project root is the working directory because that is where
        # Deployer looks for `deploy.php` or `deploy.yaml`.
        argv = web_command(project, [candidate, *_SUBCOMMAND], workdir=project.root)
        result = runner.run(argv)
        if result.ok:
            return result.stdout
        if result.returncode != _NOT_FOUND:
            raise DeployerError(
                f"`{candidate} {' '.join(_SUBCOMMAND)}` failed with exit "
                f"{result.returncode} in the web container: {result.failure_detail()}"
            )
        last_detail = result.failure_detail()
    # The last attempt's own words stay in the message: exit 127 also comes
    # from a host without `docker` at all, and naming only the two places
    # looked would then send someone hunting for a Deployer that is not the
    # problem.
    #
    # The remedy is named as a command because that is the usual cause -
    # `vendor/bin/dep` is absent because Deployer was never required - and
    # the alternative, a `dep` on the container's PATH, is already named
    # above as a place that was looked in.
    raise DeployerNotFound(
        f"could not find Deployer in the web container: neither {_CANDIDATES[0]} "
        f"nor a `dep` on its PATH ran ({last_detail}); Deployer belongs in the "
        "project's require-dev, so install it with "
        "'ddev composer require --dev deployer/deployer'"
    )


# `--no-scripts` because the project's own hooks are not what was asked for:
# a `post-install-cmd` that runs `artisan` or clears a cache can fail on a
# project that is not set up yet, and Deployer needs none of them to be on
# disk. `--no-interaction` because nobody is there to answer composer.
COMPOSER_INSTALL = ("composer", "install", "--no-scripts", "--no-interaction")


def install_composer_dependencies(project: DdevProject, runner: CommandRunner) -> None:
    """Run `composer install --no-scripts` in the web container, at the project root."""
    argv = web_command(project, list(COMPOSER_INSTALL), workdir=project.root)
    result = runner.run(argv)
    if not result.ok:
        raise DeployerError(
            f"`{' '.join(COMPOSER_INSTALL)}` failed with exit {result.returncode} "
            f"in the web container: {result.failure_detail()}"
        )
