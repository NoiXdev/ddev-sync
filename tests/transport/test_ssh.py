import os
import shlex
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from ddev_sync.config.models import Provider, ResolvedTarget, Stage
from ddev_sync.docs import doc_link
from ddev_sync.errors import DdevSyncError
from ddev_sync.transport import ssh
from ddev_sync.transport.ssh import (
    quote_remote,
    remote_endpoint,
    rsync_pull_command,
    scp_download_command,
    ssh_command,
)

# Spelled out as literals on purpose: a test that imported the production
# constants would keep passing if someone changed `accept-new` to `no`.
HARDENING = [
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
]
HARDENING_SHELL = "-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"
# What the jump host's own ssh runs: the hop `-J` would build, plus the options.
JUMP_HOP = f"ssh {HARDENING_SHELL} -l tim -p 2022 -W '[%h]:%p' bastion.example"
JUMP_HOP_NO_USER = f"ssh {HARDENING_SHELL} -p 2022 -W '[%h]:%p' bastion.example"


def bare(argv: list[str]) -> list[str]:
    """`argv` without the connection-sharing options, for tests about its shape.

    Those options are pinned on their own below; leaving them in every
    expected command line would make each of these tests about them too.
    """
    out: list[str] = []
    for part in argv:
        if out and out[-1] == "-o" and part.startswith("Control"):
            out.pop()
            continue
        out.append(" ".join(bare(part.split(" "))) if part.startswith("ssh ") else part)
    return out


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
    assert bare(ssh_command(direct(), ["echo", "ok"])) == [
        "ssh",
        *HARDENING,
        "-p",
        "2222",
        "deploy@web01.example",
        "echo ok",
    ]


def test_a_jump_host_is_reached_by_a_proxy_command_with_user_and_port() -> None:
    assert bare(ssh_command(with_jump(), ["true"])) == [
        "ssh",
        *HARDENING,
        "-o",
        f"ProxyCommand={JUMP_HOP}",
        "-p",
        "22",
        "deploy@web01.example",
        "true",
    ]


def test_an_alias_target_passes_only_the_alias() -> None:
    assert bare(ssh_command(aliased(), ["true"])) == ["ssh", *HARDENING, "app-prod", "true"]


def test_the_remote_command_is_quoted_as_one_argument() -> None:
    command = bare(ssh_command(direct(), ["mysqldump", "--password=p a$$", "db"]))
    assert command[-1] == "mysqldump '--password=p a$$' db"


def test_remote_endpoint_omits_the_user_for_aliases() -> None:
    assert remote_endpoint(direct()) == "deploy@web01.example"
    assert remote_endpoint(aliased()) == "app-prod"


def test_quote_remote_escapes_arguments_for_shell() -> None:
    assert quote_remote(["echo", "ok"]) == "echo ok"
    result = quote_remote(["mysqldump", "--password=p a$$", "db"])
    assert result == "mysqldump '--password=p a$$' db"


def test_scp_uses_uppercase_P_for_the_port() -> None:
    assert bare(scp_download_command(direct(), "/srv/app/dump.sql", "/var/www/html/dump.sql")) == [
        "scp",
        *HARDENING,
        "-P",
        "2222",
        "deploy@web01.example:/srv/app/dump.sql",
        "/var/www/html/dump.sql",
    ]


def test_scp_carries_the_jump_host() -> None:
    command = bare(scp_download_command(with_jump(), "/srv/app/dump.sql", "/tmp/dump.sql"))
    assert command[:9] == ["scp", *HARDENING, "-o", f"ProxyCommand={JUMP_HOP}"]


def test_scp_carries_jump_host_without_user() -> None:
    command = bare(scp_download_command(with_jump_no_user(), "/srv/app/dump.sql", "/tmp/dump.sql"))
    assert command[:9] == ["scp", *HARDENING, "-o", f"ProxyCommand={JUMP_HOP_NO_USER}"]


def test_scp_for_an_alias_target_has_no_port_flag() -> None:
    assert bare(scp_download_command(aliased(), "/srv/a", "/tmp/a")) == [
        "scp",
        *HARDENING,
        "app-prod:/srv/a",
        "/tmp/a",
    ]


def test_rsync_pulls_with_archive_and_progress() -> None:
    assert bare(rsync_pull_command(direct(), "/srv/app/public/", "/var/www/html/public/")) == [
        "rsync",
        "-a",
        "--info=progress2",
        "-e",
        f"ssh {HARDENING_SHELL} -p 2222",
        "deploy@web01.example:/srv/app/public/",
        "/var/www/html/public/",
    ]


def test_rsync_puts_the_jump_host_inside_the_e_option() -> None:
    command = bare(rsync_pull_command(with_jump(), "/srv/a/", "/tmp/a/"))
    assert "-e" in command
    assert (
        command[command.index("-e") + 1]
        == f'ssh {HARDENING_SHELL} -o "ProxyCommand={JUMP_HOP}" -p 22'
    )


def test_rsync_for_an_alias_target_still_passes_e_ssh() -> None:
    command = bare(rsync_pull_command(aliased(), "/srv/a/", "/tmp/a/"))
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
    return rsync_split(command[command.index("-e") + 1])[1:]


def rsync_split(value: str) -> list[str]:
    """Split `-e` the way rsync does: on spaces, honouring ' and ", no backslashes.

    A transcription of the loop in rsync's `do_cmd` (main.c), checked against
    GNU rsync 3.4 with a stand-in ssh that printed its arguments. A doubled
    quote inside a quoted run stands for itself.
    """
    words: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == " ":
            index += 1
            continue
        word: list[str] = []
        quote = ""
        while index < len(value) and (value[index] != " " or quote):
            character = value[index]
            if character in "'\"":
                if not quote:
                    quote = character
                    index += 1
                    continue
                if character == quote:
                    index += 1
                    if index >= len(value) or value[index] != quote:
                        quote = ""
                        continue
            word.append(value[index])
            index += 1
        words.append("".join(word))
    return words


def proxy_hop(options: list[str]) -> list[str]:
    """The jump host's own ssh argv, from a site's ssh arguments."""
    [proxy] = [option for option in options if option.startswith("ProxyCommand=")]
    return shlex.split(proxy.removeprefix("ProxyCommand="))


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
    # rsync splits `-e` itself, so the ProxyCommand - the one value with spaces
    # in it - must come back from rsync's split as a single argument, exactly
    # as `ssh_command` passes it.
    command = rsync_pull_command(with_jump(), "/srv/a/", "/tmp/a/")
    value = command[command.index("-e") + 1]
    assert rsync_split(value) == ssh_command(with_jump(), ["true"])[:-2]


def test_an_rsync_word_with_a_double_quote_survives_rsyncs_split() -> None:
    # No jump host is named like this, but the quoting must not depend on it.
    target = with_jump()
    target = ResolvedTarget(
        name=target.name,
        host=target.host,
        user=target.user,
        port=target.port,
        path=target.path,
        stage=target.stage,
        jump_host='bastion"x.example',
        jump_host_port=2022,
    )
    command = rsync_pull_command(target, "/srv/a/", "/tmp/a/")
    value = command[command.index("-e") + 1]
    assert rsync_split(value) == ssh_command(target, ["true"])[:-2]


# --- non-interactive, and the jump host included -----------------------------
#
# Nobody is there to answer ssh: a password, passphrase or host-key question
# goes to /dev/tty, past the captured output, and the run looks hung.


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("make_target", TARGETS)
def test_every_invocation_site_refuses_to_ask_anything(
    site: str, make_target: Callable[[], ResolvedTarget]
) -> None:
    assert "BatchMode=yes" in ssh_options_of(site, make_target())


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("make_target", [with_jump, with_jump_no_user])
def test_the_jump_hosts_own_ssh_carries_the_same_options(
    site: str, make_target: Callable[[], ResolvedTarget]
) -> None:
    # `-J` would start the bastion's ssh without these, and an unknown
    # bastion key was then a question with no terminal to answer it.
    hop = proxy_hop(ssh_options_of(site, make_target()))
    assert hop[: 1 + len(HARDENING)] == ["ssh", *HARDENING]
    assert hop[-3:] == ["-W", "[%h]:%p", "bastion.example"]
    assert "-J" not in ssh_options_of(site, make_target())


def test_the_jump_hosts_ssh_does_not_take_the_targets_host_key_alias() -> None:
    # The alias names the target in known_hosts; the bastion keeps its own name.
    options = ssh_options_of("ssh", docker_target())
    assert "HostKeyAlias=ddev-sync-endpoint-prod.example-2222" in options
    assert not any("HostKeyAlias" in part for part in proxy_hop(options))


def test_a_hostile_jump_host_cannot_escape_the_proxy_command() -> None:
    # ssh runs ProxyCommand through a shell, so the bastion's name is quoted.
    target = ResolvedTarget(
        name="production",
        host="web01.example",
        user="deploy",
        port=22,
        path="/srv/app/",
        stage=Stage.PRODUCTION,
        jump_host="bastion; touch /tmp/pwned",
        jump_host_user="tim $(id)",
    )
    hop = proxy_hop(ssh_command(target, ["true"]))
    assert hop[-1] == "bastion; touch /tmp/pwned"
    assert hop[hop.index("-l") + 1] == "tim $(id)"


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
        host_key_alias="ddev-sync-endpoint-prod.example-2222",
        path="/data/",
        stage=Stage.PRODUCTION,
    )


def test_a_docker_target_builds_a_jumped_ssh_command() -> None:
    argv = ssh_command(docker_target(), ["sh", "-c", "true"])

    hop = proxy_hop(argv)
    assert hop[hop.index("-l") + 1] == "deploy"
    assert hop[hop.index("-p") + 1] == "22"
    assert hop[-1] == "prod.example"
    assert "sync@127.0.0.1" in argv
    assert argv[argv.index("-p") + 1] == "2222"


def test_a_host_key_alias_is_passed_to_every_transport_shape() -> None:
    """Without it two stacks share one `known_hosts` entry, and the files and
    download paths would disagree with the command path about which host they
    are talking to."""
    expected = "HostKeyAlias=ddev-sync-endpoint-prod.example-2222"
    target = docker_target()

    assert expected in ssh_command(target, ["true"])
    assert expected in scp_download_command(target, "/data/x", "/tmp/x")
    assert expected in " ".join(rsync_pull_command(target, "/data/", "/tmp/here"))


def test_a_server_target_passes_no_host_key_alias() -> None:
    assert not any("HostKeyAlias" in part for part in ssh_command(direct(), ["true"]))


# --- one connection per target ------------------------------------------------
#
# Measured on a server that rate-limits SSH: the preflight's third connection
# was cut off mid-session and port 22 then stayed closed to the source
# address. Every call to a target therefore shares one master connection.

CONTROL = ["ControlMaster=auto", "ControlPersist=60"]


def control_path(options: list[str]) -> str:
    [path] = [option for option in options if option.startswith("ControlPath=")]
    return path.removeprefix("ControlPath=")


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("make_target", [*TARGETS, docker_target])
def test_every_invocation_site_shares_one_connection_per_target(
    site: str, make_target: Callable[[], ResolvedTarget]
) -> None:
    options = ssh_options_of(site, make_target())
    for option in CONTROL:
        assert options.count(option) == 1
    # The same socket from every site, so ssh, scp and rsync share it.
    assert control_path(options) == control_path(ssh_options_of("ssh", make_target()))
    assert control_path(options).startswith("/tmp/ddev-sync-%i/")


def test_the_jump_hosts_own_ssh_is_not_a_second_master() -> None:
    # The master's connection includes the hop; the hop itself runs once.
    hop = proxy_hop(ssh_options_of("ssh", with_jump()))
    assert not any(part.startswith("Control") for part in hop)


def test_targets_that_land_in_different_places_never_share_a_socket() -> None:
    # Two Docker stacks are both sync@127.0.0.1:2222; only the jump host tells
    # them apart, and ssh's own %C does not hash it.
    elsewhere = docker_target().model_copy(update={"jump_host": "other.example"})
    paths = {
        control_path(ssh_options_of("ssh", make()))
        for make in (
            direct,
            with_jump,
            with_jump_no_user,
            aliased,
            docker_target,
            lambda: elsewhere,
            lambda: direct().model_copy(update={"port": 2223}),
            lambda: direct().model_copy(update={"user": "other"}),
            lambda: docker_target().model_copy(update={"host_key_alias": "another-alias"}),
        )
    }
    assert len(paths) == 9


def test_the_socket_name_depends_only_on_where_the_connection_lands() -> None:
    assert control_path(ssh_options_of("ssh", direct())) == control_path(
        ssh_options_of(
            "ssh", direct().model_copy(update={"path": "/elsewhere/", "name": "staging"})
        )
    )


def test_the_control_directory_is_created_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ddev-sync-501"
    monkeypatch.setattr(ssh, "control_directory", lambda: directory)
    ssh.prepare_control_directory()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    # And again, as every later run will find it.
    ssh.prepare_control_directory()


def test_the_control_directory_expands_the_uid_like_ssh() -> None:
    assert ssh.control_directory() == Path(f"/tmp/ddev-sync-{os.getuid()}")


@pytest.mark.parametrize(
    "make",
    [
        lambda path: (path.mkdir(mode=0o755), path.chmod(0o755)),
        lambda path: path.write_text(""),
        lambda path: path.symlink_to(path.parent),
    ],
    ids=["shared", "a-file", "a-symlink"],
)
def test_a_control_directory_others_could_use_is_refused(
    make: Callable[[Path], object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ddev-sync-501"
    make(directory)
    monkeypatch.setattr(ssh, "control_directory", lambda: directory)
    with pytest.raises(DdevSyncError, match="only this user") as caught:
        ssh.prepare_control_directory()
    assert (doc_link(caught.value) or "").endswith("#shared-connection-directory")


def test_a_control_directory_owned_by_someone_else_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ddev-sync-501"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(ssh, "control_directory", lambda: directory)
    someone_else = os.getuid() + 1
    monkeypatch.setattr(os, "getuid", lambda: someone_else)
    with pytest.raises(DdevSyncError, match="only this user") as caught:
        ssh.prepare_control_directory()
    assert (doc_link(caught.value) or "").endswith("#shared-connection-directory")
