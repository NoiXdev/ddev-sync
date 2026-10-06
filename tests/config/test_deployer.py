"""The pure half of the Deployer integration: parsing, selecting, deriving.

The fixture is real `dep config --format=json` output from a TYPO3 13 project
deployed with Deployer 7.5. Three things in it are why it is real and not
invented: `port` is JSON null rather than absent, `release_path` resolved to an
error object while the command itself exited zero, and `shared_path` is null
even though the remote plainly has a shared directory.
"""

import json
from pathlib import Path

import pytest

from ddev_sync.config.deployer import _QUOTED_OUTPUT_LIMIT, parse_dep_config
from ddev_sync.errors import DeployerError

FIXTURE = Path(__file__).parent / "fixtures" / "dep-config-t3-13-ssg.json"


def _fixture() -> str:
    return FIXTURE.read_text()


def _payload(**overrides: object) -> str:
    """The fixture's single host with some keys replaced."""
    data = json.loads(_fixture())
    host, cfg = next(iter(data.items()))
    return json.dumps({host: {**cfg, **overrides}})


def _failure(payload: str, alias: str | None = None) -> str:
    with pytest.raises(DeployerError) as error:
        parse_dep_config(payload, alias)
    return str(error.value)


def test_it_reads_the_four_connection_values_from_real_output() -> None:
    connection = parse_dep_config(_fixture(), None)
    assert connection.host == "web-node-1.noidee.network"
    assert connection.user == "ssg-kevelaer"
    assert connection.path == "/home/ssg-kevelaer/htdocs/deployer"


def test_a_null_port_becomes_none_and_never_the_string_none() -> None:
    assert parse_dep_config(_fixture(), None).port is None


def test_an_explicit_port_is_read_as_an_integer() -> None:
    assert parse_dep_config(_payload(port=2222), None).port == 2222


def test_a_port_that_is_not_an_integer_fails_naming_the_key() -> None:
    message = _failure(_payload(port="ssh"))
    assert "port" in message
    assert "ssh" in message


def test_a_boolean_port_is_not_an_integer() -> None:
    # `True` is an `int` in Python, so a loose isinstance check would let a
    # JSON `true` silently become port 1.
    assert "port" in _failure(_payload(port=True))


def test_a_port_written_as_a_digit_string_is_read_as_an_integer() -> None:
    # `->set('port', getenv('SSH_PORT'))` in a deploy.php, or `port: "2222"` in
    # YAML, serialises as a string. That is a legitimate project.
    assert parse_dep_config(_payload(port="2222"), None).port == 2222


@pytest.mark.parametrize("port", ["", "None", "ssh", " 22", "22 ", "-5", "2.5", "\uff12\uff12"])
def test_a_port_string_that_is_not_plain_ascii_digits_fails_quoting_it(port: str) -> None:
    message = _failure(_payload(port=port))
    assert "port" in message
    assert repr(port) in message


@pytest.mark.parametrize("port", [0, -5, 65536, 99999, "0", "99999"])
def test_a_port_outside_1_to_65535_fails_quoting_it(port: object) -> None:
    message = _failure(_payload(port=port))
    assert "port" in message
    assert repr(port) in message


@pytest.mark.parametrize("port", [1, 65535, "65535"])
def test_the_ends_of_the_port_range_are_accepted(port: object) -> None:
    assert parse_dep_config(_payload(port=port), None).port == int(str(port))


def test_the_layout_is_derived_from_current_path() -> None:
    assert parse_dep_config(_fixture(), None).layout == "deployer"


def test_a_trailing_slash_on_deploy_path_does_not_change_the_layout() -> None:
    payload = _payload(deploy_path="/home/ssg-kevelaer/htdocs/deployer/")
    assert parse_dep_config(payload, None).layout == "deployer"


def test_a_current_path_that_is_not_deploy_path_plus_current_is_plain() -> None:
    payload = _payload(current_path="/home/ssg-kevelaer/htdocs/deployer")
    assert parse_dep_config(payload, None).layout == "plain"


def test_an_unusable_current_path_fails_rather_than_guessing_a_layout() -> None:
    # Guessing wrong sends every file path to the wrong place, so this is one
    # of the cases that must not be tolerated.
    message = _failure(_payload(current_path=None))
    assert "layout" in message
    assert "current_path" in message


def test_an_error_object_in_a_read_key_fails_with_deployers_own_text() -> None:
    message = _failure(_payload(deploy_path={"error": 'The "deploy_path" is not set.'}))
    assert "deploy_path" in message
    assert "is not set" in message


def test_an_error_object_without_text_still_fails_naming_the_key() -> None:
    message = _failure(_payload(remote_user={"unexpected": 1}))
    assert "remote_user" in message


def test_an_error_object_in_current_path_fails_with_deployers_own_text() -> None:
    message = _failure(_payload(current_path={"error": "current_path is broken"}))
    assert "current_path" in message
    assert "is broken" in message


def test_an_error_object_in_an_unread_key_is_ignored() -> None:
    # `release_path` in the fixture is exactly this, and it must not stop a
    # resolution that does not need it.
    assert parse_dep_config(_fixture(), None).host == "web-node-1.noidee.network"


def test_a_missing_deploy_path_fails_naming_the_key() -> None:
    assert "deploy_path" in _failure(_payload(deploy_path=None))


def test_a_named_alias_that_is_present_is_used() -> None:
    connection = parse_dep_config(_fixture(), "web-node-1.noidee.network")
    assert connection.host == "web-node-1.noidee.network"


def test_a_named_alias_that_is_absent_lists_what_was_found() -> None:
    message = _failure(_fixture(), "staging")
    assert "staging" in message
    assert "web-node-1.noidee.network" in message


def test_several_hosts_without_a_named_alias_lists_them_all() -> None:
    data = json.loads(_fixture())
    host, cfg = next(iter(data.items()))
    message = _failure(json.dumps({host: cfg, "staging.example": cfg}))
    assert "staging.example" in message
    assert host in message


def test_no_hosts_at_all_says_so() -> None:
    assert "no hosts" in _failure("{}").lower()


def test_output_that_is_not_json_fails_with_what_was_received() -> None:
    assert "Usage: dep" in _failure("Deployer 7.5.12\nUsage: dep [options]")


def test_what_was_received_is_truncated_to_something_a_terminal_can_hold() -> None:
    # The quoted part is pinned to exactly the limit, not to a round number
    # above it: `len(message) < 1000` passes just as happily with no
    # truncation at 900 characters, which is already a message nobody reads
    # in a terminal. The surrounding sentence is counted too, so the limit
    # itself stays the only thing this test allows to change.
    message = _failure("x" * 5000)
    sentence = "`dep config --format=json` did not print JSON; it printed: "
    assert message == sentence + "x" * _QUOTED_OUTPUT_LIMIT
    assert _QUOTED_OUTPUT_LIMIT == 200


def test_json_that_is_not_a_mapping_of_hosts_fails() -> None:
    assert "mapping" in _failure("[1, 2, 3]")


def test_a_host_whose_configuration_is_not_a_mapping_fails_naming_the_host() -> None:
    message = _failure('{"web-1": "oops"}')
    assert "web-1" in message
    assert "mapping" in message


def test_a_missing_hostname_falls_back_to_the_alias_key() -> None:
    # Whether Deployer ever leaves `hostname` out is not something the fixture
    # shows - there `alias` and `hostname` are both present and equal. This pins
    # the fallback for the case: the key the output is indexed by is the only
    # name left to connect to.
    assert parse_dep_config(_payload(hostname=None), None).host == "web-node-1.noidee.network"


def test_a_missing_remote_user_fails_naming_the_key() -> None:
    # A direct connection without a user is rejected by `ResolvedTarget` two
    # layers later, with advice about `ssh_alias` that means nothing for a
    # target whose connection comes from Deployer. Failing here puts the error
    # next to its cause.
    assert "remote_user" in _failure(_payload(remote_user=None))


def test_a_value_of_the_wrong_type_fails_naming_the_key() -> None:
    assert "deploy_path" in _failure(_payload(deploy_path=42))


@pytest.mark.parametrize("output", ["", "  \n"])
def test_output_that_is_empty_says_it_printed_nothing(output: str) -> None:
    assert "printed nothing" in _failure(output)


@pytest.mark.parametrize("key", ["deploy_path", "remote_user"])
def test_a_whitespace_only_required_value_counts_as_missing(key: str) -> None:
    assert key in _failure(_payload(**{key: "   "}))
