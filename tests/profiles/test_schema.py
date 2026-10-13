from typing import Any

import pytest

from ddev_sync.errors import ProfileError
from ddev_sync.profiles.schema import validate_profile

MINIMAL: dict[str, Any] = {"name": "x"}


def test_a_minimal_profile_is_valid() -> None:
    validate_profile(MINIMAL)


def test_an_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "sinc": {}})
    assert "sinc" in str(caught.value)


def test_a_valid_env_strategy_passes() -> None:
    validate_profile({**MINIMAL, "env": {"strategy": "patch_keys", "set": {"A": "1"}}})


def test_an_unknown_env_strategy_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "env": {"strategy": "telepathy"}})
    assert "telepathy" in str(caught.value)


def test_a_valid_env_default_passes() -> None:
    validate_profile(
        {**MINIMAL, "env": {"strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}}}
    )


def test_an_env_default_that_is_not_a_mapping_is_rejected() -> None:
    with pytest.raises(ProfileError, match=r"env\.default"):
        validate_profile({**MINIMAL, "env": {"default": "QUEUE_CONNECTION=sync"}})


def test_an_env_default_with_append_block_is_rejected() -> None:
    # `append_block` writes a marked block rather than reconciling keys, so
    # "only if absent" cannot be expressed through it. Rejected at load, where
    # the message names the key path (`env.default`) and the strategy.
    with pytest.raises(ProfileError, match="append_block"):
        validate_profile(
            {**MINIMAL, "env": {"strategy": "append_block", "default": {"CACHE_STORE": "file"}}}
        )


def test_an_empty_env_default_with_append_block_passes() -> None:
    validate_profile({**MINIMAL, "env": {"strategy": "append_block", "default": {}}})


def test_an_unknown_credential_source_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "database": {"remote_credentials": "vibes"}})
    assert "vibes" in str(caught.value)


@pytest.mark.parametrize("missing", ["remote", "local"])
def test_a_files_path_missing_a_side_is_rejected(missing: str) -> None:
    entry = {"remote": "a/", "local": "b/"}
    del entry[missing]
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "files": {"paths": [entry]}})
    assert missing in str(caught.value)


def test_a_sanitize_rule_naming_an_unregistered_operation_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile(
            {**MINIMAL, "sanitize": {"rules": [{"name": "x", "operation": "teleport"}]}}
        )
    assert "teleport" in str(caught.value)


def test_a_sanitize_rule_naming_a_registered_operation_passes() -> None:
    validate_profile(
        {
            **MINIMAL,
            "sanitize": {
                "rules": [{"name": "cache", "operation": "flush_cache", "command": "true"}]
            },
        }
    )


def test_a_setup_command_with_neither_run_nor_operation_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "setup": {"commands": [{"workdir": "."}]}})
    assert "run" in str(caught.value)


def test_a_profile_without_a_name_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({"env": {"strategy": "none"}})
    assert "name" in str(caught.value)


# The tests above are the brief's verbatim fixture set. They exercise every
# *rejection reason* but not every *malformed shape* a merged profile could
# take (e.g. 'env' being a string instead of a mapping) - those shapes are
# additional branches this repository's 100% branch coverage gate requires,
# so they are covered here too.


def test_env_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "env": "nope"})
    assert "mapping" in str(caught.value)


def test_database_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "database": "nope"})
    assert "mapping" in str(caught.value)


def test_a_valid_database_config_passes() -> None:
    validate_profile({**MINIMAL, "database": {"remote_credentials": "env"}})


def test_database_remote_docroot_is_rejected_and_names_layout() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "database": {"remote_docroot": "current"}})
    message = str(caught.value)
    assert "remote_docroot" in message
    assert "layout" in message


def test_a_non_list_env_file_is_rejected() -> None:
    with pytest.raises(ProfileError) as error:
        validate_profile({"name": "x", "database": {"env_file": 7}})
    assert "database.env_file" in str(error.value)


def test_a_string_env_file_is_accepted() -> None:
    validate_profile({"name": "x", "database": {"env_file": ".env"}})


def test_a_list_env_file_is_accepted() -> None:
    validate_profile({"name": "x", "database": {"env_file": [".env.local", ".env"]}})


def test_files_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "files": "nope"})
    assert "mapping" in str(caught.value)


def test_files_paths_must_be_a_list() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "files": {"paths": "nope"}})
    assert "list" in str(caught.value)


def test_a_files_path_entry_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "files": {"paths": ["nope"]}})
    assert "mapping" in str(caught.value)


def test_a_fully_specified_files_path_passes() -> None:
    validate_profile({**MINIMAL, "files": {"paths": [{"remote": "a/", "local": "b/"}]}})


def test_sanitize_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "sanitize": "nope"})
    assert "mapping" in str(caught.value)


def test_sanitize_rules_must_be_a_list() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "sanitize": {"rules": "nope"}})
    assert "list" in str(caught.value)


def test_a_sanitize_rule_entry_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "sanitize": {"rules": ["nope"]}})
    assert "mapping" in str(caught.value)


def test_a_sanitize_rule_missing_a_name_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "sanitize": {"rules": [{"sql": "SELECT 1"}]}})
    assert "name" in str(caught.value)


def test_a_sanitize_rule_with_neither_sql_nor_operation_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "sanitize": {"rules": [{"name": "x"}]}})
    assert "neither" in str(caught.value)


def test_a_sanitize_rule_with_a_boolean_tolerate_missing_table_passes() -> None:
    validate_profile(
        {
            **MINIMAL,
            "sanitize": {
                "rules": [{"name": "x", "sql": "TRUNCATE TABLE t", "tolerate_missing_table": True}]
            },
        }
    )


def test_a_sanitize_rule_with_a_non_boolean_tolerate_missing_table_is_rejected() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile(
            {
                **MINIMAL,
                "sanitize": {
                    "rules": [
                        {"name": "x", "sql": "TRUNCATE TABLE t", "tolerate_missing_table": "yes"}
                    ]
                },
            }
        )
    assert "tolerate_missing_table" in str(caught.value)


def test_setup_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "setup": "nope"})
    assert "mapping" in str(caught.value)


def test_setup_commands_must_be_a_list() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "setup": {"commands": "nope"}})
    assert "list" in str(caught.value)


def test_a_setup_command_entry_must_be_a_mapping() -> None:
    with pytest.raises(ProfileError) as caught:
        validate_profile({**MINIMAL, "setup": {"commands": ["nope"]}})
    assert "mapping" in str(caught.value)


def test_a_setup_command_with_run_passes() -> None:
    validate_profile({**MINIMAL, "setup": {"commands": [{"run": "true"}]}})
