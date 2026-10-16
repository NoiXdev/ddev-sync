import shlex
from collections.abc import Callable

import pytest

from ddev_sync.config.models import Provider, ResolvedTarget, Stage
from ddev_sync.transport.ssh import (
    quote_remote,
    remote_endpoint,
    rsync_pull_command,
    scp_download_command,
    ssh_command,
)

# Spelled out as literals on purpose: a test that imported the production
# constants would keep passing if someone changed `accept-new` to `no`.
HARDENING = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
HARDENING_SHELL = "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"


def direct() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=2222,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
    )


def with_jump() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        jump_host="bastion.example",
        jump_host_user="tim",
        jump_host_port=2022,
    )


def with_jump_no_user() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        jump_host="bastion.example",
        jump_host_port=2022,
    )


def aliased() -> ResolvedTarget:
    return ResolvedTarget(
        name="production",
        host="app-prod",
        via_ssh_alias=True,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
    )


def test_a_direct_target_produces_port_and_endpoint() -> None:
    assert ssh_command(direct(), ["echo", "ok"]) == [
        "ssh",
        *HARDENING,
        "-p",
        "2222",
        "deploy@web01.example",
        "echo ok",
    ]


def test_a_jump_host_adds_the_J_flag_with_user_and_port() -> None:
    assert ssh_command(with_jump(), ["true"]) == [
        "ssh",
        *HARDENING,
        "-J",
        "tim@bastion.example:2022",
        "-p",
        "22",
        "deploy@web01.example",
        "true",
    ]


def test_an_alias_target_passes_only_the_alias() -> None:
    assert ssh_command(aliased(), ["true"]) == ["ssh", *HARDENING, "app-prod", "true"]


def test_the_remote_command_is_quoted_as_one_argument() -> None:
    command = ssh_command(direct(), ["mysqldump", "--password=p a$$", "db"])
    assert command[-1] == "mysqldump '--password=p a$$' db"


def test_remote_endpoint_omits_the_user_for_aliases() -> None:
    assert remote_endpoint(direct()) == "deploy@web01.example"
    assert remote_endpoint(aliased()) == "app-prod"


def test_quote_remote_escapes_arguments_for_shell() -> None:
    assert quote_remote(["echo", "ok"]) == "echo ok"
    result = quote_remote(["mysqldump", "--password=p a$$", "db"])
    assert result == "mysqldump '--password=p a$$' db"


def test_scp_uses_uppercase_P_for_the_port() -> None:
    assert scp_download_command(direct(), "/srv/app/dump.sql", "/var/www/html/dump.sql") == [
        "scp",
        *HARDENING,
        "-P",
        "2222",
        "deploy@web01.example:/srv/app/dump.sql",
        "/var/www/html/dump.sql",
    ]


def test_scp_carries_the_jump_host() -> None:
    command = scp_download_command(with_jump(), "/srv/app/dump.sql", "/tmp/dump.sql")
    assert command[:7] == ["scp", *HARDENING, "-J", "tim@bastion.example:2022"]


def test_scp_carries_jump_host_without_user() -> None:
    command = scp_download_command(with_jump_no_user(), "/srv/app/dump.sql", "/tmp/dump.sql")
    assert command[:7] == ["scp", *HARDENING, "-J", "bastion.example:2022"]


def test_scp_for_an_alias_target_has_no_port_flag() -> None:
    assert scp_download_command(aliased(), "/srv/a", "/tmp/a") == [
        "scp",
        *HARDENING,
        "app-prod:/srv/a",
        "/tmp/a",
    ]


def test_rsync_pulls_with_archive_and_progress() -> None:
    assert rsync_pull_command(direct(), "/srv/app/public/", "/var/www/html/public/") == [
        "rsync",
        "-a",
        "--info=progress2",
        "-e",
        f"ssh {HARDENING_SHELL} -p 2222",
        "deploy@web01.example:/srv/app/public/",
        "/var/www/html/public/",
    ]


def test_rsync_puts_the_jump_host_inside_the_e_option() -> None:
    command = rsync_pull_command(with_jump(), "/srv/a/", "/tmp/a/")
    assert "-e" in command
    assert (
        command[command.index("-e") + 1]
        == f"ssh {HARDENING_SHELL} -J tim@bastion.example:2022 -p 22"
    )


def test_rsync_for_an_alias_target_still_passes_e_ssh() -> None:
    command = rsync_pull_command(aliased(), "/srv/a/", "/tmp/a/")
    assert command[command.index("-e") + 1] == f"ssh {HARDENING_SHELL}"


def test_rsync_delete_is_opt_in() -> None:
    without = rsync_pull_command(direct(), "/srv/a/", "/tmp/a/")
    with_delete = rsync_pull_command(direct(), "/srv/a/", "/tmp/a/", delete=True)
    assert "--delete" not in without
    assert "--delete" in with_delete


@pytest.mark.parametrize(
    "hostile",
    ["db; rm -rf /", "db && curl evil.example", "db`whoami`", "db$(id)", "db|tee /tmp/x"],
)
def test_a_hostile_database_name_cannot_escape_the_remote_command(hostile: str) -> None:
    command = ssh_command(direct(), ["mysqldump", hostile])
    remote = command[-1]
    assert remote.startswith("mysqldump ")
    # The whole hostile value survives as one quoted token, so the remote shell
    # sees an argument, never a second command.
    assert shlex.split(remote) == ["mysqldump", hostile]


# --- host-key policy and connect timeout ------------------------------------
#
# Found on a first real run: the engine set neither, so a container whose
# known_hosts starts empty either prompted with no terminal to answer on or
# failed with "Host key verification failed.", and an unreachable host kept
# `doctor` waiting about 75 seconds. These tests pin the options at every one
# of the three places an ssh argument vector is built - fixing only
# `_ssh_prefix` would leave scp and rsync behind, silently.


def ssh_options_of(site: str, target: ResolvedTarget) -> list[str]:
    """The ssh arguments `site` would run ssh with, as the argv tokens ssh sees."""
    if site == "ssh":
        return ssh_command(target, ["true"])[1:-1]
    if site == "scp":
        return scp_download_command(target, "/srv/a", "/tmp/a")[1:-2]
    command = rsync_pull_command(target, "/srv/a/", "/tmp/a/")
    # rsync splits `-e` itself; shlex mirrors that for options without spaces.
    return shlex.split(command[command.index("-e") + 1])[1:]


SITES = ["ssh", "scp", "rsync"]
TARGETS = [direct, with_jump, with_jump_no_user, aliased]


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("make_target", TARGETS)
def test_every_invocation_site_sets_the_accept_new_policy_and_no_other(
    site: str, make_target: Callable[[], ResolvedTarget]
) -> None:
    # `accept-new` is what trusts an unknown host yet still refuses a changed
    # one, so it must be there and it must be the only host-key policy given.
    options = ssh_options_of(site, make_target())
    policies = [option for option in options if option.startswith("StrictHostKeyChecking=")]
    assert policies == ["StrictHostKeyChecking=accept-new"]


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("make_target", TARGETS)
def test_every_invocation_site_bounds_the_connect_time(
    site: str, make_target: Callable[[], ResolvedTarget]
) -> None:
    options = ssh_options_of(site, make_target())
    assert "ConnectTimeout=10" in options


@pytest.mark.parametrize("site", SITES)
def test_no_invocation_site_disables_host_key_detection(site: str) -> None:
    # `accept-new` is the whole point: `StrictHostKeyChecking=no` or a
    # throwaway known_hosts would also accept a *changed* key, which is the
    # one signal a man-in-the-middle produces.
    joined = " ".join(ssh_options_of(site, direct()))
    assert "StrictHostKeyChecking=no" not in joined
    assert "UserKnownHostsFile" not in joined
    assert "StrictHostKeyChecking=off" not in joined


@pytest.mark.parametrize("site", SITES)
def test_each_option_is_given_exactly_once_per_site(site: str) -> None:
    options = ssh_options_of(site, with_jump())
    assert sum(option.startswith("StrictHostKeyChecking=") for option in options) == 1
    assert sum(option.startswith("ConnectTimeout=") for option in options) == 1


def test_rsync_option_values_survive_rsyncs_split_on_spaces() -> None:
    # rsync splits `-e` on whitespace, so a value containing a space would be
    # torn into two arguments. Splitting the string naively - the way rsync
    # does - must give back exactly the tokens ssh is meant to receive.
    command = rsync_pull_command(with_jump(), "/srv/a/", "/tmp/a/")
    value = command[command.index("-e") + 1]
    assert value.split(" ") == [
        "ssh",
        *HARDENING,
        "-J",
        "tim@bastion.example:2022",
        "-p",
        "22",
    ]


def docker_target() -> ResolvedTarget:
    """What `provider: docker` resolves to, written out.

    Built here as a literal rather than resolved through `config/targets.py`:
    this file is about the command line, and a literal makes it obvious which
    field produces which argument. `tests/config/test_targets.py` owns the
    other half - that a declared docker target really does resolve into these
    values.
    """
    return ResolvedTarget(
        name="production",
        provider=Provider.DOCKER,
        host="127.0.0.1",
        user="sync",
        port=2222,
        jump_host="prod.example",
        jump_host_user="deploy",
        path="/data/",
        stage=Stage.PRODUCTION,
    )


def test_a_docker_target_builds_a_jumped_ssh_command() -> None:
    argv = ssh_command(docker_target(), ["sh", "-c", "true"])

    assert "-J" in argv
    assert argv[argv.index("-J") + 1] == "deploy@prod.example:22"
    assert "sync@127.0.0.1" in argv
    assert argv[argv.index("-p") + 1] == "2222"
