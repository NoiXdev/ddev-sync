from typing import Any

import pytest

from ddev_sync.operations.base import OperationError
from ddev_sync.operations.fake import (
    DEFAULT_EMAIL_DOMAIN,
    KINDS,
    FakeColumn,
    parse_columns,
    value_sql,
)
from ddev_sync.operations.fake_seeds import CITIES, FIRST_NAMES, LAST_NAMES, LOREM_WORDS


def _sql(kind: str, *, masked: bool = False, column: str = "email", **settings: Any) -> str:
    return value_sql(
        "reservations",
        FakeColumn(column, kind, settings),
        "id",
        email_domain="example.test",
        masked=masked,
    )


def test_every_kind_is_known() -> None:
    assert set(KINDS) == {
        "name", "first_name", "last_name", "email", "text",
        "integer", "zip", "city", "phone", "ip",
    }  # fmt: skip


def test_default_domain() -> None:
    assert DEFAULT_EMAIL_DOMAIN == "example.fake"


def test_short_and_long_forms_parse() -> None:
    columns = parse_columns("r", {"email": "email", "n": {"type": "integer", "min": 1, "max": 4}})
    assert columns == [
        FakeColumn("email", "email", {}),
        FakeColumn("n", "integer", {"min": 1, "max": 4}),
    ]


def test_valid_settings_parse() -> None:
    columns = parse_columns(
        "r", {"a": {"type": "ip", "version": 6}, "b": {"type": "text", "length": 40}}
    )
    assert [c.settings for c in columns] == [{"version": 6}, {"length": 40}]


@pytest.mark.parametrize(
    "raw",
    [
        {"c": "nonsense"},
        {"c": {"type": "integer"}},
        {"c": {"type": "integer", "min": 5, "max": 1}},
        {"c": {"type": "integer", "min": "a", "max": 1}},
        {"c": {"type": "ip", "version": 5}},
        {"c": {"type": "email", "colour": "x"}},
        {"c": {"type": "text", "length": 0}},
        {"c": {"length": 3}},
        {},
        ["email"],
    ],
)
def test_bad_columns_are_refused(raw: Any) -> None:
    with pytest.raises(OperationError, match="'myrule'"):
        parse_columns("myrule", raw)


def test_null_and_empty_stay_as_they_are() -> None:
    assert _sql("email").startswith(
        "CASE WHEN `email` IS NULL THEN NULL WHEN CAST(`email` AS CHAR) = '' THEN '' ELSE"
    )


def test_the_empty_check_compares_as_text_so_a_numeric_column_survives() -> None:
    # A bare `col = ''` casts '' to a number on an INT column: strict mode
    # refuses it (error 1292) and a stored 0 would count as empty.
    assert "`email` = ''" not in _sql("email")


def test_the_hash_input_names_table_column_and_key() -> None:
    assert "CONCAT('reservations.email.', LOWER(HEX(COALESCE(`id`, ''))))" in _sql("email")


def test_email_uses_the_domain_and_a_twelve_hex_suffix() -> None:
    sql = _sql("email")
    assert "'@example.test'" in sql
    assert "LEFT(SHA2(" in sql
    assert ", 256), 12)" in sql


def test_identifiers_and_domain_are_escaped() -> None:
    column = FakeColumn("we`ird", "email", {})
    sql = value_sql("t", column, "k`ey", email_domain="a'b", masked=False)
    assert "`we``ird`" in sql
    assert "`k``ey`" in sql
    assert "a''b" in sql


@pytest.mark.parametrize(
    ("kind", "settings", "expected"),
    [
        ("name", {}, "'Name '"),
        ("first_name", {}, "'First '"),
        ("last_name", {}, "'Last '"),
        ("email", {}, "'user.'"),
        ("text", {}, "'Lorem ipsum.'"),
        ("integer", {"min": 7, "max": 9}, "ELSE 7 END"),
        ("zip", {}, "'00000'"),
        ("city", {}, "'City'"),
        ("phone", {}, "'+1 555 0100'"),
        ("ip", {}, "'192.0.2.0'"),
        ("ip", {"version": 6}, "'2001:db8::'"),
    ],
)
def test_masked_values_are_the_documented_constants(
    kind: str, settings: dict[str, Any], expected: str
) -> None:
    sql = _sql(kind, masked=True, **settings)
    assert expected in sql
    assert "ELT(" not in sql


def test_masked_email_keeps_the_twelve_hex_suffix() -> None:
    assert ", 256), 12)" in _sql("email", masked=True)


@pytest.mark.parametrize(
    ("kind", "settings"),
    [
        ("name", {}),
        ("first_name", {}),
        ("last_name", {}),
        ("email", {}),
        ("text", {}),
        ("integer", {"min": 1, "max": 5}),
        ("zip", {}),
        ("city", {}),
        ("phone", {}),
        ("ip", {}),
        ("ip", {"version": 6}),
    ],
)
def test_every_realistic_kind_builds(kind: str, settings: dict[str, Any]) -> None:
    sql = _sql(kind, **settings)
    assert sql.endswith("END")
    assert "#0" in sql


def test_text_without_length_stays_under_255() -> None:
    assert "LEFT(CONCAT(" in _sql("text")
    assert ", 255) END" in _sql("text")
    assert ", 40) END" in _sql("text", length=40)


def test_seed_lists_are_large_enough() -> None:
    assert len(FIRST_NAMES) >= 200
    assert len(LAST_NAMES) >= 200
    assert len(CITIES) >= 40
    assert len(LOREM_WORDS) >= 60
    for seeds in (FIRST_NAMES, LAST_NAMES, CITIES, LOREM_WORDS):
        assert not any("'" in seed or "\\" in seed for seed in seeds)
