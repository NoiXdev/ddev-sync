"""`env.pull`: which names a project may bring along, decided at plan time."""

from typing import Any

import pytest

from ddev_sync.config.env_pull import names_to_read, pull_names
from ddev_sync.errors import ConfigError


def test_names_to_read_leaves_out_what_env_set_keeps() -> None:
    data = {"env": {"pull": ["A", "B"], "set": {"A": "1"}}}
    assert names_to_read(data) == ["B"]


@pytest.mark.parametrize("imposed", [None, "oops"])
def test_names_to_read_reads_a_set_that_is_no_mapping_as_no_block(imposed: Any) -> None:
    assert names_to_read({"env": {"pull": ["A"], "set": imposed}}) == ["A"]


def test_names_to_read_of_nothing_listed_is_nothing() -> None:
    assert names_to_read({"env": "oops"}) == []


def _data(pull: Any, **database: Any) -> dict[str, Any]:
    return {"env": {"pull": pull}, "database": database}


def test_nothing_set_is_an_empty_list() -> None:
    assert pull_names({}) == []
    assert pull_names({"env": {"set": {"A": "1"}}}) == []


def test_a_bare_pull_key_is_nothing_written_rather_than_something_malformed() -> None:
    assert pull_names({"env": {"pull": None}}) == []


def test_an_env_block_that_is_no_mapping_has_nothing_to_pull() -> None:
    # The planner and the step both read a non-mapping `env` as no block.
    assert pull_names({"env": "nonsense"}) == []


def test_names_come_back_deduplicated_and_in_order() -> None:
    assert pull_names(_data(["APP_KEY", "B_2", "APP_KEY", "_c"])) == ["APP_KEY", "B_2", "_c"]


def test_a_value_that_is_not_a_list_is_refused() -> None:
    with pytest.raises(ConfigError, match=r"env\.pull must be a list"):
        pull_names(_data("APP_KEY"))


@pytest.mark.parametrize("name", ["1BAD", "A-B", "A B", "", "A\n", 7, None])
def test_a_name_that_is_no_variable_name_is_refused(name: Any) -> None:
    with pytest.raises(ConfigError, match=r"env\.pull"):
        pull_names(_data([name]))


def test_the_endpoints_own_names_are_refused() -> None:
    with pytest.raises(ConfigError, match="DDEV_SYNC_X"):
        pull_names(_data(["DDEV_SYNC_X"]))


@pytest.mark.parametrize(
    "name",
    [
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DB_URL",
        "DATABASE_URL",
    ],
)
def test_what_says_where_the_database_is_is_refused(name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        pull_names(_data([name]))


def test_env_map_decides_which_names_those_are() -> None:
    with pytest.raises(ConfigError, match="DB_HOST"):
        pull_names(_data(["DB_HOST"], env_map={"host": "DB_HOST"}))


def test_a_field_env_map_leaves_out_keeps_its_default_name() -> None:
    with pytest.raises(ConfigError, match="DATABASE_PORT"):
        pull_names(_data(["DATABASE_PORT"], env_map={"host": "DB_HOST"}))


def test_a_name_env_map_moved_away_from_is_an_ordinary_one() -> None:
    assert pull_names(_data(["DATABASE_HOST"], env_map={"host": "DB_HOST"})) == ["DATABASE_HOST"]


def test_db_host_is_an_ordinary_name_where_nothing_maps_to_it() -> None:
    # A project whose application reads DB_HOST for something else entirely is
    # not this tool's business unless `env_map` says that is the database.
    assert pull_names(_data(["DB_HOST"])) == ["DB_HOST"]


@pytest.mark.parametrize("database", ["nonsense", None])
def test_a_database_block_that_is_no_mapping_leaves_the_default_names(database: Any) -> None:
    with pytest.raises(ConfigError, match="DATABASE_NAME"):
        pull_names({"env": {"pull": ["DATABASE_NAME"]}, "database": database})


def test_an_env_map_that_is_no_mapping_leaves_the_default_names() -> None:
    with pytest.raises(ConfigError, match="DATABASE_NAME"):
        pull_names(_data(["DATABASE_NAME"], env_map="nonsense"))


def test_the_refusal_says_why_the_database_names_cannot_come_along() -> None:
    with pytest.raises(ConfigError, match="production"):
        pull_names(_data(["DB_URL"]))
