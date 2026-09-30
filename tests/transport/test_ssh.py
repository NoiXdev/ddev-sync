import shlex

import pytest

from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.transport.ssh import (
    quote_remote,
    remote_endpoint,
    rsync_pull_command,
    scp_download_command,
    ssh_command,
)


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
        "-p",
        "2222",
        "deploy@web01.example",
        "echo ok",
    ]


def test_a_jump_host_adds_the_J_flag_with_user_and_port() -> None:
    assert ssh_command(with_jump(), ["true"]) == [
        "ssh",
        "-J",
        "tim@bastion.example:2022",
        "-p",
        "22",
        "deploy@web01.example",
        "true",
    ]


def test_an_alias_target_passes_only_the_alias() -> None:
    assert ssh_command(aliased(), ["true"]) == ["ssh", "app-prod", "true"]


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
        "-P",
        "2222",
        "deploy@web01.example:/srv/app/dump.sql",
        "/var/www/html/dump.sql",
    ]


def test_scp_carries_the_jump_host() -> None:
    command = scp_download_command(with_jump(), "/srv/app/dump.sql", "/tmp/dump.sql")
    assert command[:3] == ["scp", "-J", "tim@bastion.example:2022"]


def test_scp_carries_jump_host_without_user() -> None:
    command = scp_download_command(with_jump_no_user(), "/srv/app/dump.sql", "/tmp/dump.sql")
    assert command[:3] == ["scp", "-J", "bastion.example:2022"]


def test_scp_for_an_alias_target_has_no_port_flag() -> None:
    assert scp_download_command(aliased(), "/srv/a", "/tmp/a") == [
        "scp",
        "app-prod:/srv/a",
        "/tmp/a",
    ]


def test_rsync_pulls_with_archive_and_progress() -> None:
    assert rsync_pull_command(direct(), "/srv/app/public/", "/var/www/html/public/") == [
        "rsync",
        "-a",
        "--info=progress2",
        "-e",
        "ssh -p 2222",
        "deploy@web01.example:/srv/app/public/",
        "/var/www/html/public/",
    ]


def test_rsync_puts_the_jump_host_inside_the_e_option() -> None:
    command = rsync_pull_command(with_jump(), "/srv/a/", "/tmp/a/")
    assert "-e" in command
    assert command[command.index("-e") + 1] == "ssh -J tim@bastion.example:2022 -p 22"


def test_rsync_for_an_alias_target_still_passes_e_ssh() -> None:
    command = rsync_pull_command(aliased(), "/srv/a/", "/tmp/a/")
    assert command[command.index("-e") + 1] == "ssh"


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
