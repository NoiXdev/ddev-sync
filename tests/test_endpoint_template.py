"""The endpoint template's security properties, as tests rather than comments.

The template is what an operator copies into a production stack, and most of
what the design argues about the endpoint is a property of *this file* rather
than of the image: the port is reachable only through an SSH forward because
of how it is published, the payload cannot be written because of how it is
mounted, and the host key survives a redeploy because a volume holds it.

A comment claiming each of those would be true until someone edited the file.
These read the file the add-on ships, like `tests/test_version_coherence.py`
and `tests/test_no_legacy_terms.py` - a fixture would agree with itself while
the shipped template drifted.
"""

import json
from typing import Any

from ddev_sync.yamlfile import load_yaml
from tools.set_version import REPO_ROOT

TEMPLATE = REPO_ROOT / "endpoint" / "docker-compose.ddev-sync-endpoint.yaml"
SERVICE = "ddev-sync"
# The one mount the endpoint writes: it generates a host key into it on first
# start. Everything else it only ever reads.
HOST_KEY_MOUNT = "/etc/ssh/keys"


def _template() -> dict[str, Any]:
    document: dict[str, Any] = load_yaml(TEMPLATE.read_text(encoding="utf-8"))
    return document


def _service() -> dict[str, Any]:
    service: dict[str, Any] = _template()["services"][SERVICE]
    return service


def test_the_endpoint_is_published_on_loopback_only() -> None:
    published = _service()["ports"]

    assert published, "the template publishes nothing, so no engine can reach it"
    # A bare "2222:22" would put a production endpoint on every interface the
    # host has. The keys are the authentication; this is what keeps the
    # endpoint reachable solely through an SSH forward.
    for mapping in published:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_every_payload_mount_is_read_only() -> None:
    mounts = _service()["volumes"]

    for mount in mounts:
        _, target, *mode = str(mount).split(":")
        if target == HOST_KEY_MOUNT:
            continue
        # The sync only ever pulls, and that is enforced by the mount rather
        # than left to the engine's good behaviour.
        assert mode == ["ro"], f"{mount} is writable by the endpoint"


def test_the_host_key_lives_in_a_named_volume() -> None:
    sources = {
        str(mount).split(":")[0]
        for mount in _service()["volumes"]
        if str(mount).split(":")[1] == HOST_KEY_MOUNT
    }

    assert len(sources) == 1, f"expected one host-key mount, found {sources}"
    source = sources.pop()
    # Declared as a volume, not a bind mount of a host path: with
    # `StrictHostKeyChecking=accept-new` a regenerated host key is *refused*,
    # so the first redeploy would otherwise break the sync for every developer
    # at once, with a message that reads like an attack.
    assert source in _template()["volumes"], f"{source} is not a declared volume"
    assert not source.startswith((".", "/")), f"{source} is a host path, not a volume"


def test_the_template_names_no_user() -> None:
    # Measured: a container started with a `user:` the image was not built for
    # exits 255 with "No user exists for uid ...", because an sshd cannot
    # serve a uid that has no passwd entry and only root inside the container
    # can write one. The session's uid arrives as configuration instead.
    assert "user" not in _service()
    assert "DDEV_SYNC_UID" in _service()["environment"]


def test_the_template_asks_for_authorized_keys() -> None:
    # Without this the endpoint refuses to start, which is the right failure -
    # but an operator should not meet it after copying the template.
    assert "DDEV_SYNC_AUTHORIZED_KEYS" in _service()["environment"]


def test_the_template_names_the_released_image_tag() -> None:
    released = json.loads((REPO_ROOT / "version.json").read_text(encoding="utf-8"))["version"]

    # A template naming a tag nobody published does not fail at sync time: the
    # stack does not start at all. `tools/set_version.py` writes this line,
    # and this is the test that notices when a hand edit has moved it.
    assert _service()["image"].endswith(f":{released}"), _service()["image"]
