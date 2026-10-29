"""What the endpoint image promises, asserted against real containers.

The endpoint is the far side of `provider: docker`: an sshd a stack publishes
on the Docker host's loopback, which the engine reaches by ProxyJump. Every
promise here is one a configuration cannot restate - the image either carries
`mysqldump` or it does not, either refuses to start without a key or does not -
so each test starts a container and asks it.

Nothing here uses the suite's own stack. The endpoint under test is published
on a port of this module's own and removed in a `finally`, because a leftover
container would hold that port and fail the next run for a reason that has
nothing to do with the image.
"""

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# This module's own published port, so a test can connect while the rest of the
# suite's containers are up.
PORT = "22024"
# Port 22 in the form `/proc/net/tcp` prints it, with `0A` for LISTEN. Read
# from /proc rather than with `nc -z`, which busybox does not have: the image
# carries three binaries on purpose, and a healthcheck is no reason for a
# fourth.
LISTENING_ON_22 = r"grep -qiE ':0016 [0-9A-F]+:0000 0A' /proc/net/tcp"

# `BatchMode` so a misconfigured endpoint fails instead of asking for a
# password; the host key is deliberately not pinned - every container here is
# new, and what persists is the subject of one test rather than a premise of
# all of them.
SSH_OPTIONS = (
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "BatchMode=yes",
    "-o",
    "LogLevel=ERROR",
)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def keypair(directory: Path, name: str) -> Path:
    """A throwaway keypair; returns the private key, `.pub` beside it."""
    private = directory / name
    generated = _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)])
    if generated.returncode != 0:
        pytest.fail(f"could not generate a test key:\n{generated.stderr}")
    return private


def start_in_foreground(
    image: str,
    *,
    environment: dict[str, str] | None = None,
    mounts: tuple[tuple[Path, str], ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run the endpoint and wait for it, for the cases where it must refuse.

    A refusal is an exit status and a message, so there is nothing to connect
    to and no reason to background the container.
    """
    argv = ["docker", "run", "--rm"]
    for key, value in (environment or {}).items():
        argv += ["-e", f"{key}={value}"]
    for source, target in mounts:
        argv += ["-v", f"{source}:{target}:ro"]
    return _run([*argv, image])


@contextmanager
def endpoint(
    image: str,
    *,
    environment: dict[str, str] | None = None,
    mounts: tuple[tuple[Path, str], ...] = (),
    volume: str | None = None,
    publish: bool = True,
) -> Iterator[str]:
    """A running endpoint, removed afterwards whatever happens.

    Waits for sshd to be listening rather than for the container to be
    running: the entrypoint generates a host key first, and a connection
    attempt that races it fails as "connection refused", which looks exactly
    like a broken image.
    """
    argv = ["docker", "run", "-d"]
    if publish:
        argv += ["-p", f"127.0.0.1:{PORT}:22"]
    for key, value in (environment or {}).items():
        argv += ["-e", f"{key}={value}"]
    for source, target in mounts:
        argv += ["-v", f"{source}:{target}:ro"]
    if volume is not None:
        argv += ["-v", f"{volume}:/etc/ssh/keys"]
    started = _run([*argv, image])
    if started.returncode != 0:
        pytest.fail(f"could not start the endpoint:\n{started.stderr}")
    container = started.stdout.strip()
    try:
        for _ in range(60):
            if _run(["docker", "exec", container, "sh", "-c", LISTENING_ON_22]).returncode == 0:
                break
            time.sleep(0.5)
        else:
            logs = _run(["docker", "logs", container])
            pytest.fail(f"sshd never listened:\n{logs.stdout}\n{logs.stderr}")
        yield container
    finally:
        _run(["docker", "rm", "-f", container])


def ssh(key: Path, *command: str) -> subprocess.CompletedProcess[str]:
    """One command in the endpoint, as the account the engine connects as."""
    return _run(
        [
            "ssh",
            "-i",
            str(key),
            "-p",
            PORT,
            *SSH_OPTIONS,
            "sync@127.0.0.1",
            *command,
        ]
    )


def host_key(image: str, volume: str) -> str:
    """The public host key sitting in `volume`, read without the endpoint.

    Through a throwaway container because the key is the question: reading it
    from inside a running endpoint would also prove the endpoint runs, and the
    test that compares two of them needs the second container to be gone.
    """
    read = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/etc/ssh/keys",
            "--entrypoint",
            "cat",
            image,
            "/etc/ssh/keys/ssh_host_ed25519_key.pub",
        ]
    )
    if read.returncode != 0:
        pytest.fail(f"could not read the host key:\n{read.stderr}")
    return read.stdout.split()[1]


def test_it_refuses_to_start_with_no_authorized_key(endpoint_image: str) -> None:
    result = start_in_foreground(endpoint_image)

    assert result.returncode != 0, result.stdout
    # Both sources, because a message naming one of them sends the reader to
    # configure the wrong thing.
    assert "DDEV_SYNC_AUTHORIZED_KEYS" in result.stderr
    assert "/etc/ddev-sync/authorized_keys" in result.stderr


def test_a_file_holding_only_comments_is_not_a_key(endpoint_image: str, tmp_path: Path) -> None:
    empty = tmp_path / "authorized_keys"
    empty.write_text("# nothing here\n\n", encoding="utf-8")

    result = start_in_foreground(
        endpoint_image, mounts=((empty, "/etc/ddev-sync/authorized_keys"),)
    )

    # The file exists, so a presence check would have let this endpoint start -
    # listening, reachable, and impossible to enter.
    assert result.returncode != 0, result.stdout
    assert "DDEV_SYNC_AUTHORIZED_KEYS" in result.stderr


def test_a_key_from_the_environment_and_a_key_from_the_file_both_get_in(
    endpoint_image: str, tmp_path: Path
) -> None:
    from_environment = keypair(tmp_path, "environment")
    from_file = keypair(tmp_path, "file")
    keys_file = tmp_path / "authorized_keys"
    keys_file.write_text(
        from_file.with_suffix(".pub").read_text(encoding="utf-8"), encoding="utf-8"
    )

    with endpoint(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": from_environment.with_suffix(".pub").read_text(
                encoding="utf-8"
            )
        },
        mounts=((keys_file, "/etc/ddev-sync/authorized_keys"),),
    ):
        # A union, not a choice: two places to keep the same list.
        assert ssh(from_environment, "true").returncode == 0
        assert ssh(from_file, "true").returncode == 0


def test_the_session_runs_as_the_configured_uid(endpoint_image: str, tmp_path: Path) -> None:
    key = keypair(tmp_path, "id")

    with endpoint(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8"),
            "DDEV_SYNC_UID": "82",
            "DDEV_SYNC_GID": "82",
        },
    ):
        result = ssh(key, "id -u; id -g")

    # 82 rather than the image's own 33: a uid the image was not built for is
    # the whole point of the variable, and the mounts a real stack hands the
    # endpoint are owned by the application, not by this image.
    assert result.stdout.split() == ["82", "82"], result.stderr


@pytest.mark.parametrize("value", ["0", "www-data"])
def test_a_uid_that_cannot_be_honoured_is_refused(
    endpoint_image: str, tmp_path: Path, value: str
) -> None:
    key = keypair(tmp_path, "id")

    result = start_in_foreground(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8"),
            "DDEV_SYNC_UID": value,
        },
    )

    # `0` would hand every session root inside the container; a name is a
    # value the account cannot be given at all. Both are configuration errors
    # that must stop the endpoint rather than be rounded to a default.
    assert result.returncode != 0, result.stdout
    assert "DDEV_SYNC_UID" in result.stderr


def test_the_host_key_survives_a_recreated_container(endpoint_image: str, tmp_path: Path) -> None:
    key = keypair(tmp_path, "id")
    environment = {"DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8")}
    volume = "ddev-sync-endpoint-test-hostkeys"
    _run(["docker", "volume", "rm", "-f", volume])
    try:
        with endpoint(endpoint_image, environment=environment, volume=volume):
            pass
        first = host_key(endpoint_image, volume)
        with endpoint(endpoint_image, environment=environment, volume=volume):
            pass
        second = host_key(endpoint_image, volume)
    finally:
        _run(["docker", "volume", "rm", "-f", volume])

    # Without this, the first redeploy of a stack breaks the sync for every
    # developer at once: the engine's `StrictHostKeyChecking=accept-new`
    # refuses a *changed* key, and the message reads like an attack.
    assert first == second


@pytest.mark.parametrize("binary", ["sh", "rsync", "mysqldump", "mysql"])
def test_it_carries_what_the_engine_invokes_on_the_far_side(
    endpoint_image: str, binary: str
) -> None:
    # One `command -v` per name, never a single call with four arguments:
    # busybox's builtin looks only at its first argument and exits 0 on it.
    found = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            endpoint_image,
            "-c",
            f"command -v {binary}",
        ]
    )

    assert found.returncode == 0, f"{binary} is missing from the endpoint image"


@pytest.mark.parametrize("binary", ["php", "docker", "rclone", "ssh"])
def test_it_carries_nothing_the_endpoint_has_no_business_with(
    endpoint_image: str, binary: str
) -> None:
    found = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            endpoint_image,
            "-c",
            f"command -v {binary}",
        ]
    )

    # `docker` would move the privilege this provider exists to remove;
    # `rclone` belongs to the developer's side; `php` is what WordPress's
    # credential source would need and is refused instead (spec decision 7);
    # `ssh` would let a compromised endpoint hop onwards.
    assert found.returncode != 0, f"{binary} has no business in the endpoint image"


# What the server must resolve these to, as `sshd -T` prints them. Asserted
# against sshd's own resolution rather than by reading the file, because that
# is what the running endpoint will do with it - and because a behavioural
# test cannot see most of them: a client in batch mode never offers a password
# whatever the server allows, so "the login failed" is not evidence that
# password authentication is off.
EFFECTIVE_SETTINGS = (
    "permitrootlogin no",
    "passwordauthentication no",
    "kbdinteractiveauthentication no",
    "permitemptypasswords no",
    "allowtcpforwarding no",
    "allowstreamlocalforwarding no",
    "allowagentforwarding no",
    "permituserrc no",
    # Every developer arrives through the same Docker host, so a per-source
    # penalty would lock the whole team out for one stale key's retries.
    "persourcepenalties no",
    "allowusers sync",
)


@pytest.mark.parametrize("setting", EFFECTIVE_SETTINGS)
def test_the_server_resolves_its_hardening_as_shipped(endpoint_image: str, setting: str) -> None:
    # `sshd -T` needs a host key to exist before it will print anything, which
    # is what the keygen is for; the config it then prints is the one the
    # endpoint runs with.
    resolved = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            endpoint_image,
            "-c",
            'ssh-keygen -q -t ed25519 -N "" -f /etc/ssh/keys/ssh_host_ed25519_key '
            "&& /usr/sbin/sshd -T",
        ]
    )

    assert resolved.returncode == 0, resolved.stderr
    assert setting in resolved.stdout.lower().splitlines(), (
        f"sshd does not resolve {setting!r}; what it printed:\n{resolved.stdout}"
    )


def test_the_server_offers_nothing_but_public_keys(endpoint_image: str, tmp_path: Path) -> None:
    key = keypair(tmp_path, "id")

    with endpoint(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8")
        },
    ):
        # Over the wire, with a key the server does not know, so the server
        # answers with the methods it would accept next. `-v` is what makes
        # that line visible; the test above pins the same property from the
        # server's side, and neither alone is enough: this one would pass
        # against a server that also offered a password, and that one proves
        # nothing about what reaches the client.
        stranger = keypair(tmp_path, "stranger")
        attempt = _run(
            ["ssh", "-v", "-i", str(stranger), "-p", PORT, *SSH_OPTIONS, "sync@127.0.0.1", "true"]
        )

    offered = [line for line in attempt.stderr.splitlines() if "can continue" in line]
    assert offered, f"the server offered no methods at all:\n{attempt.stderr}"
    for line in offered:
        assert "publickey" in line
        assert "password" not in line
        assert "keyboard-interactive" not in line


def test_password_authentication_is_refused(endpoint_image: str, tmp_path: Path) -> None:
    key = keypair(tmp_path, "id")

    with endpoint(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8")
        },
    ):
        result = _run(
            [
                "ssh",
                "-p",
                PORT,
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "PreferredAuthentications=password,keyboard-interactive",
                *SSH_OPTIONS,
                "sync@127.0.0.1",
                "true",
            ]
        )

    # There is no mode in which this endpoint accepts anyone: an image whose
    # authentication could be switched off would reach production by accident
    # and be invisible in `docker ps`.
    assert result.returncode != 0, result.stdout


def test_a_key_that_is_not_a_key_is_refused(endpoint_image: str, tmp_path: Path) -> None:
    truncated = tmp_path / "authorized_keys"
    # A line that is neither blank nor a comment, and not a key either - what
    # a CRLF paste or a mangled multi-line secret leaves behind.
    truncated.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1\r\n", encoding="utf-8")

    result = start_in_foreground(
        endpoint_image, mounts=((truncated, "/etc/ddev-sync/authorized_keys"),)
    )

    # Without this the endpoint listens, is reachable, and cannot be entered -
    # and nothing says why.
    assert result.returncode != 0, result.stdout
    assert "public keys" in result.stderr


def test_the_image_reports_its_own_health(endpoint_image: str, tmp_path: Path) -> None:
    key = keypair(tmp_path, "id")

    with endpoint(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8")
        },
    ) as container:
        # The healthcheck is in the image rather than in the compose template,
        # so every stack inherits it. An sshd that died otherwise leaves a
        # container that is still `Up` and a sync that fails days later.
        for _ in range(40):
            state = _run(["docker", "inspect", "--format", "{{.State.Health.Status}}", container])
            if state.stdout.strip() == "healthy":
                break
            time.sleep(0.5)
        else:
            pytest.fail(f"the endpoint never reported healthy: {state.stdout!r}")


def test_the_endpoint_does_not_forward_tcp(endpoint_image: str, tmp_path: Path) -> None:
    key = keypair(tmp_path, "id")

    with endpoint(
        endpoint_image,
        environment={
            "DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8")
        },
    ):
        # The same key, the same endpoint: a session is allowed. Asserted here
        # so the refusal below is known to be about forwarding and not about a
        # connection that never succeeded at all.
        session = ssh(key, "true")
        # `-W` is what ProxyJump itself uses: the endpoint is a destination,
        # and nothing should be able to treat it as a way onwards into the
        # stack's network.
        forwarded = _run(
            [
                "ssh",
                "-i",
                str(key),
                "-p",
                PORT,
                *SSH_OPTIONS,
                "-W",
                "127.0.0.1:22",
                "sync@127.0.0.1",
            ]
        )

    assert session.returncode == 0, session.stderr
    assert forwarded.returncode != 0
    # The server's own "administratively prohibited" is what sshd logs; the
    # client reports the consequence, and `LogLevel=ERROR` keeps the server's
    # line out of this stderr.
    assert "forwarding failed" in forwarded.stderr.lower(), forwarded.stderr


# Every character a quoting mistake would eat: quotes, `#`, `=`, `$`, and the
# spaces on both ends that a shell would trim.
TRICKY = " pa'ss\"#=$ word "
HANDOVER_FILE = "/run/ddev-sync/app.env"


def _keys(directory: Path) -> tuple[Path, dict[str, str]]:
    """A keypair and the environment that lets it into an endpoint."""
    key = keypair(directory, "id")
    return key, {"DDEV_SYNC_AUTHORIZED_KEYS": key.with_suffix(".pub").read_text(encoding="utf-8")}


def test_without_an_export_list_no_handover_file_is_written(
    endpoint_image: str, tmp_path: Path
) -> None:
    _, keys = _keys(tmp_path)

    with endpoint(endpoint_image, environment=keys) as container:
        result = _run(["docker", "exec", container, "test", "-e", HANDOVER_FILE])

    assert result.returncode != 0


def test_the_listed_variables_reach_the_session_verbatim(
    endpoint_image: str, tmp_path: Path
) -> None:
    key, keys = _keys(tmp_path)

    with endpoint(
        endpoint_image,
        environment={
            **keys,
            "DDEV_SYNC_EXPORT_ENV": "DB_HOST,DB_PASSWORD DB_HOST",
            "DB_HOST": "db",
            "DB_PASSWORD": TRICKY,
            "UNLISTED": "nope",
        },
    ):
        result = ssh(key, f"cat {HANDOVER_FILE}")

    # The duplicate is written once, and nothing that was not listed - the
    # authorised keys included - is in the file.
    expected = f"# ddev-sync endpoint handover v1\nDB_HOST=db\nDB_PASSWORD={TRICKY}\n"
    assert result.stdout == expected, result.stderr


def test_the_handover_file_belongs_to_the_session_and_nobody_else(
    endpoint_image: str, tmp_path: Path
) -> None:
    _, keys = _keys(tmp_path)

    with endpoint(
        endpoint_image,
        environment={
            **keys,
            "DDEV_SYNC_UID": "1234",
            "DDEV_SYNC_GID": "1234",
            "DDEV_SYNC_EXPORT_ENV": "DB_PASSWORD",
            "DB_PASSWORD": "secret",
        },
    ) as container:
        result = _run(["docker", "exec", container, "stat", "-c", "%u:%g %a", HANDOVER_FILE])

    assert result.stdout.strip() == "1234:1234 400", result.stderr


def test_an_empty_value_is_handed_over_as_empty(endpoint_image: str, tmp_path: Path) -> None:
    key, keys = _keys(tmp_path)

    with endpoint(
        endpoint_image,
        environment={**keys, "DDEV_SYNC_EXPORT_ENV": "DB_PASSWORD", "DB_PASSWORD": ""},
    ):
        result = ssh(key, f"cat {HANDOVER_FILE}")

    assert "DB_PASSWORD=\n" in result.stdout, result.stderr


@pytest.mark.parametrize(
    ("environment", "named", "slug"),
    [
        ({"DDEV_SYNC_EXPORT_ENV": "DB_HOST"}, "DB_HOST", "endpoint-export-env-variable-unset"),
        ({"DDEV_SYNC_EXPORT_ENV": "1BAD"}, "1BAD", "endpoint-export-env-name-invalid"),
        ({"DDEV_SYNC_EXPORT_ENV": "$(id)"}, "$(id)", "endpoint-export-env-name-invalid"),
        (
            {"DDEV_SYNC_EXPORT_ENV": "DDEV_SYNC_UID"},
            "DDEV_SYNC_UID",
            "endpoint-export-env-name-invalid",
        ),
        (
            {"DDEV_SYNC_EXPORT_ENV": "DB_PASSWORD", "DB_PASSWORD": "s3cret-first\ns3cret-second"},
            "DB_PASSWORD",
            "endpoint-export-env-value-has-a-newline",
        ),
        (
            {"DDEV_SYNC_EXPORT_ENV": "DB_PASSWORD", "DB_PASSWORD": "s3cret-first\rs3cret-second"},
            "DB_PASSWORD",
            "endpoint-export-env-value-has-a-newline",
        ),
    ],
)
def test_a_bad_export_list_stops_the_endpoint(
    endpoint_image: str,
    tmp_path: Path,
    environment: dict[str, str],
    named: str,
    slug: str,
) -> None:
    _, keys = _keys(tmp_path)

    result = start_in_foreground(endpoint_image, environment={**keys, **environment})

    assert result.returncode != 0, result.stdout
    assert named in result.stderr
    assert f"#{slug}" in result.stderr
    # The message names the variable; a secret in the log would defeat the
    # point of refusing.
    assert "s3cret-first" not in result.stderr
    assert "s3cret-second" not in result.stderr
