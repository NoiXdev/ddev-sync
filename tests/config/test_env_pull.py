"""`env.pull`: which names a project may bring along, decided at plan time."""

from typing import Any

import pytest

from ddev_sync.config.env_pull import (
    may_pull_all,
    names_to_read,
    optional_names,
    pull_all,
    pull_names,
    pulls_anything,
)
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


def test_a_refused_entry_is_named_by_its_position_and_its_value_stays_out() -> None:
    # What a misread `env.pull` holds may be the secret itself, pasted with its name.
    with pytest.raises(ConfigError, match=r"entry 2") as caught:
        pull_names(_data(["FINE", "APP_KEY=base64:topsecret"]))
    assert "topsecret" not in str(caught.value)
    assert "APP_KEY" not in str(caught.value)


@pytest.mark.parametrize("raw", ["APP_KEY=base64:topsecret", {"APP_KEY": "topsecret"}, 7])
def test_a_refused_block_does_not_print_what_it_holds(raw: Any) -> None:
    with pytest.raises(ConfigError, match=r"env\.pull must be a list") as caught:
        pull_names(_data(raw))
    assert "topsecret" not in str(caught.value)


def test_a_well_shaped_name_refused_for_another_reason_is_named() -> None:
    with pytest.raises(ConfigError, match="DDEV_SYNC_X"):
        pull_names(_data(["DDEV_SYNC_X"]))


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


def _env(**env: Any) -> dict[str, Any]:
    return {"env": env}


def test_the_profile_defaults_apply_when_the_project_lists_nothing() -> None:
    data = _env(pull_defaults=["APP_KEY"])
    assert pull_names(data) == ["APP_KEY"]
    assert optional_names(data) == {"APP_KEY"}


def test_a_list_of_the_projects_own_replaces_the_defaults() -> None:
    data = _env(pull_defaults=["APP_KEY"], pull=["OTHER"])
    assert pull_names(data) == ["OTHER"]
    assert optional_names(data) == set()


def test_an_empty_list_takes_the_defaults_away() -> None:
    assert pull_names(_env(pull_defaults=["APP_KEY"], pull=[])) == []


def test_extend_adds_the_projects_list_to_the_defaults() -> None:
    data = _env(pull_defaults=["APP_KEY"], pull=["OTHER"], extend=True)
    assert pull_names(data) == ["APP_KEY", "OTHER"]
    assert optional_names(data) == {"APP_KEY"}


def test_a_default_the_project_lists_too_is_required() -> None:
    data = _env(pull_defaults=["APP_KEY"], pull=["APP_KEY"], extend=True)
    assert pull_names(data) == ["APP_KEY"]
    assert optional_names(data) == set()


def test_optional_names_of_a_non_mapping_env_is_nothing() -> None:
    assert optional_names({"env": "nonsense"}) == set()


def test_extend_with_a_malformed_list_entry_is_still_refused() -> None:
    with pytest.raises(ConfigError, match="entry 2 is not one"):
        pull_names(_env(pull_defaults=["APP_KEY"], pull=[1], extend=True))


@pytest.mark.parametrize(
    "settings",
    [
        {"strategy": "append_block"},
        {"strategy": "none"},
        {"file": []},
        {"file": ["config/.env"]},
        {"file": None},
    ],
)
def test_defaults_are_dropped_where_the_env_settings_cannot_take_them(
    settings: dict[str, Any],
) -> None:
    assert pull_names(_env(pull_defaults=["APP_KEY"], **settings)) == []


def test_defaults_work_with_one_file_named_as_a_string() -> None:
    assert pull_names(_env(pull_defaults=["APP_KEY"], file=".env")) == ["APP_KEY"]


def test_malformed_defaults_are_refused() -> None:
    with pytest.raises(ConfigError, match=r"env\.pull_defaults must be a list"):
        pull_names(_env(pull_defaults="APP_KEY"))


def test_the_word_all_in_env_pull_points_at_pull_all() -> None:
    with pytest.raises(ConfigError, match=r"set env\.pull_all: true"):
        pull_names(_env(pull="all"))


@pytest.mark.parametrize("flag", ["extend", "pull_all"])
def test_a_flag_that_is_no_boolean_is_refused(flag: str) -> None:
    data = _env(pull=[], **{flag: "yes"})
    with pytest.raises(ConfigError, match=f"env.{flag} must be true or false"):
        pull_names(data) if flag == "extend" else pull_all(data)


def test_pull_all_is_off_by_default() -> None:
    assert pull_all({}) is False
    assert pull_all(_env()) is False
    assert pull_all(_env(pull_all=None)) is False
    assert pulls_anything(_env()) is False


def test_pull_all_alone_pulls_something() -> None:
    assert pull_all(_env(pull_all=True)) is True
    assert pulls_anything(_env(pull_all=True)) is True


@pytest.mark.parametrize(
    "name",
    ["DDEV_SYNC_SKIPPED", "PATH", "HOSTNAME", "DATABASE_HOST", "DB_URL", "KEPT", "not-a-name"],
)
def test_pull_all_leaves_out_what_must_stay_local(name: str) -> None:
    assert may_pull_all(name, _env(pull_all=True, set={"KEPT": "1"})) is False


def test_pull_all_leaves_out_a_renamed_database_setting() -> None:
    data = {"env": {"pull_all": True}, "database": {"env_map": {"host": "DB_HOST"}}}
    assert may_pull_all("DB_HOST", data) is False


@pytest.mark.parametrize("imposed", [{}, None, "oops"])
def test_pull_all_takes_any_other_name(imposed: Any) -> None:
    assert may_pull_all("STRIPE_SECRET", _env(pull_all=True, set=imposed)) is True
