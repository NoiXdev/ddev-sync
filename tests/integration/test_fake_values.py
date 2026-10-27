"""The SQL behind the `fake` sanitize operation, run on a real MariaDB.

The unit tests only see the generated text; whether MariaDB accepts it, keeps
it inside the column, and yields the same values twice is only knowable by
running it. Everything goes through the engine's own path - `FakeValues` with a
`SubprocessRunner` and `local_mysql_argv` against `ddev-acme-db` - so the
conftest's dockerised `mysql` wrapper is the connection, nothing new.

Scratch tables are named `fv7_*`, unique to this module, and dropped after.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.sanitize import SANITIZE_OPERATIONS, SanitizeRule, local_mysql_argv
from ddev_sync.transport.runner import CommandResult, SubprocessRunner

pytestmark = pytest.mark.integration

STRICT = "SET SESSION sql_mode='STRICT_ALL_TABLES'; "
TABLES = ("fv7_shapes", "fv7_unique", "fv7_strict", "fv7_binary", "fv7_uuid", "fv7_masked")
EMAIL = r"^[a-z]+\.[a-z]+\.[0-9a-f]{12}@example\.fake$"


class SessionModeRunner:
    """Runs everything as `SubprocessRunner` does, with a session `sql_mode` set first.

    `FakeValues` opens a fresh `mysql` connection per statement, so a mode set
    in a separate invocation would be gone. Prefixing the `--execute` text puts
    the SET in the same session as the UPDATE.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._inner = SubprocessRunner()

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        command = list(argv)
        command[-1] = self._prefix + command[-1]
        return self._inner.run(command, **kwargs)


def make_context(root: Path, runner: Any, **settings: Any) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {"sanitize": settings})]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
        assume_yes=True,
    )


class Db:
    """Plain SQL against the local database, through the same argv the engine builds."""

    def __init__(self, root: Path) -> None:
        self.context = make_context(root, SubprocessRunner())

    def execute(self, sql: str) -> str:
        argv = local_mysql_argv(self.context, "db", sql=sql)
        # `-N`: no header line, so a result is only its rows.
        argv.insert(-2, "-N")
        result = self.context.runner.run(argv)
        assert result.ok, result.stderr
        return result.stdout

    def rows(self, sql: str) -> list[list[str]]:
        return [line.split("\t") for line in self.execute(sql).splitlines()]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Db]:
    database = Db(tmp_path)
    for table in TABLES:
        database.execute(f"DROP TABLE IF EXISTS {table}")
    yield database
    for table in TABLES:
        database.execute(f"DROP TABLE IF EXISTS {table}")


def fake(
    root: Path,
    table: str,
    columns: Mapping[str, Any],
    *,
    key: str = "id",
    runner: Any = None,
    **settings: Any,
) -> None:
    """Run the real `fake` operation for `table`; a failure raises like in a sync."""
    context = make_context(root, runner or SubprocessRunner(), **settings)
    rule = SanitizeRule(
        name="fake", operation="fake", params={"table": table, "key": key, "columns": columns}
    )
    SANITIZE_OPERATIONS["fake"].run(context, rule)


KINDS: dict[str, Any] = {
    "name": "name",
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "txt": {"type": "text", "length": 120},
    "num": {"type": "integer", "min": 100, "max": 199},
    "zip": "zip",
    "city": "city",
    "phone": "phone",
    "ip4": "ip",
    "ip6": {"type": "ip", "version": 6},
}
COLUMNS = list(KINDS)
TEXT_COLUMNS = [column for column in COLUMNS if column != "num"]


def create_shapes(db: Db) -> None:
    columns = ", ".join(f"{column} VARCHAR(255) NULL" for column in TEXT_COLUMNS)
    db.execute(f"CREATE TABLE fv7_shapes (id INT PRIMARY KEY, {columns}, num INT NULL)")
    names = ", ".join(TEXT_COLUMNS)
    filled = ", ".join(f"'seed {column}'" for column in TEXT_COLUMNS)
    db.execute(
        f"INSERT INTO fv7_shapes (id, {names}, num) SELECT seq, {filled}, 7 FROM seq_1_to_200"
    )
    # Row 1000: every column NULL. Row 1001: every text column ''.
    db.execute("INSERT INTO fv7_shapes (id) VALUES (1000)")
    blanks = ", ".join("''" for _ in TEXT_COLUMNS)
    db.execute(f"INSERT INTO fv7_shapes (id, {names}) VALUES (1001, {blanks})")


def shape_rows(db: Db) -> list[list[str]]:
    return db.rows(f"SELECT id, {', '.join(COLUMNS)} FROM fv7_shapes ORDER BY id")


def test_every_kind_produces_its_shape(db: Db, tmp_path: Path) -> None:
    create_shapes(db)
    fake(tmp_path, "fv7_shapes", KINDS)
    patterns = {
        "name": r"^\S+ \S+$",
        "first_name": r"^\S+$",
        "last_name": r"^\S+$",
        "email": EMAIL,
        "txt": r"^[A-Z][a-z]*( [a-z]+){5,13}\.$",
        "zip": r"^\d{5}$",
        "city": r"^.+$",
        "phone": r"^\+1 555 01\d{2}$",
        "ip4": r"^192\.0\.2\.\d{1,3}$",
        "ip6": r"^2001:db8::[0-9a-f]{1,4}$",
    }
    rows = [row for row in shape_rows(db) if int(row[0]) <= 200]
    assert len(rows) == 200
    for row in rows:
        values = dict(zip(COLUMNS, row[1:], strict=True))
        for column, pattern in patterns.items():
            assert re.match(pattern, values[column]), (column, values[column])
        assert 100 <= int(values["num"]) <= 199
        assert len(values["txt"]) <= 120
        assert 1 <= int(values["ip4"].rsplit(".", 1)[1]) <= 254


def test_the_values_actually_vary(db: Db, tmp_path: Path) -> None:
    create_shapes(db)
    fake(tmp_path, "fv7_shapes", KINDS)
    rows = [row for row in shape_rows(db) if int(row[0]) <= 200]
    for index, column in enumerate(COLUMNS, start=1):
        assert len({row[index] for row in rows}) > 1, column


def test_null_and_empty_rows_are_unchanged(db: Db, tmp_path: Path) -> None:
    create_shapes(db)
    fake(tmp_path, "fv7_shapes", KINDS)
    by_id = {row[0]: row[1:] for row in shape_rows(db)}
    assert by_id["1000"] == ["NULL"] * len(COLUMNS)
    for column, value in zip(COLUMNS, by_id["1001"], strict=True):
        # `num` is an INT column, so the blank insert left it NULL.
        assert value == ("NULL" if column == "num" else ""), column


def test_running_twice_gives_identical_values(db: Db, tmp_path: Path) -> None:
    create_shapes(db)
    fake(tmp_path, "fv7_shapes", KINDS)
    first = shape_rows(db)
    fake(tmp_path, "fv7_shapes", KINDS)
    assert shape_rows(db) == first


def test_an_integer_column_holding_zero_is_faked_not_blanked(db: Db, tmp_path: Path) -> None:
    # `0 = ''` is true in MariaDB, so the empty-string guard must not swallow
    # a numeric zero (or fail strict mode by assigning '' to an INT).
    db.execute("CREATE TABLE fv7_shapes (id INT PRIMARY KEY, num INT NULL)")
    db.execute("INSERT INTO fv7_shapes VALUES (1, 0), (2, 5), (3, NULL)")
    fake(tmp_path, "fv7_shapes", {"num": {"type": "integer", "min": 100, "max": 199}})
    rows = db.rows("SELECT id, num FROM fv7_shapes ORDER BY id")
    assert 100 <= int(rows[0][1]) <= 199
    assert 100 <= int(rows[1][1]) <= 199
    assert rows[2][1] == "NULL"


def test_five_thousand_rows_with_a_unique_email_column(db: Db, tmp_path: Path) -> None:
    db.execute("CREATE TABLE fv7_unique (id INT PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE)")
    db.execute(
        "INSERT INTO fv7_unique SELECT seq, CONCAT('real', seq, '@corp.example') FROM seq_1_to_5000"
    )
    fake(tmp_path, "fv7_unique", {"email": "email"})
    assert db.rows("SELECT COUNT(*), COUNT(DISTINCT email) FROM fv7_unique") == [["5000", "5000"]]


def test_a_text_column_of_its_declared_length_updates_in_strict_mode(
    db: Db, tmp_path: Path
) -> None:
    db.execute("CREATE TABLE fv7_strict (id INT PRIMARY KEY, bio VARCHAR(40) NOT NULL)")
    db.execute("INSERT INTO fv7_strict SELECT seq, 'real biography' FROM seq_1_to_300")
    fake(
        tmp_path,
        "fv7_strict",
        {"bio": {"type": "text", "length": 40}},
        runner=SessionModeRunner(STRICT),
    )
    lengths = db.rows("SELECT MIN(CHAR_LENGTH(bio)), MAX(CHAR_LENGTH(bio)) FROM fv7_strict")
    assert int(lengths[0][0]) >= 1
    assert int(lengths[0][1]) <= 40
    assert db.rows("SELECT COUNT(*) FROM fv7_strict WHERE bio = 'real biography'") == [["0"]]


def test_strict_mode_really_refuses_a_value_that_is_too_long(db: Db, tmp_path: Path) -> None:
    # The converse: proves the session above is strict, so the passing test
    # is not passing for want of strictness.
    db.execute("CREATE TABLE fv7_strict (id INT PRIMARY KEY, bio VARCHAR(10) NOT NULL)")
    db.execute("INSERT INTO fv7_strict VALUES (1, 'real')")
    with pytest.raises(OperationError):
        fake(
            tmp_path,
            "fv7_strict",
            {"bio": {"type": "text", "length": 40}},
            runner=SessionModeRunner(STRICT),
        )


def test_a_binary_key_works_and_is_stable(db: Db, tmp_path: Path) -> None:
    db.execute("CREATE TABLE fv7_binary (id BINARY(16) PRIMARY KEY, email VARCHAR(255) NOT NULL)")
    db.execute(
        "INSERT INTO fv7_binary SELECT UNHEX(LPAD(HEX(seq), 32, '0')), 'real@corp.example' "
        "FROM seq_1_to_300"
    )
    fake(tmp_path, "fv7_binary", {"email": "email"})
    first = db.rows("SELECT HEX(id), email FROM fv7_binary ORDER BY id")
    assert len({row[1] for row in first}) == 300
    for _, email in first:
        assert re.match(EMAIL, email)
    fake(tmp_path, "fv7_binary", {"email": "email"})
    assert db.rows("SELECT HEX(id), email FROM fv7_binary ORDER BY id") == first


def test_a_uuid_key_works_and_is_stable(db: Db, tmp_path: Path) -> None:
    db.execute("CREATE TABLE fv7_uuid (uuid CHAR(36) PRIMARY KEY, email VARCHAR(255) NOT NULL)")
    db.execute("INSERT INTO fv7_uuid SELECT UUID(), 'real@corp.example' FROM seq_1_to_300")
    fake(tmp_path, "fv7_uuid", {"email": "email"}, key="uuid")
    first = db.rows("SELECT uuid, email FROM fv7_uuid ORDER BY uuid")
    assert len({row[1] for row in first}) == 300
    for _, email in first:
        assert re.match(EMAIL, email)
    fake(tmp_path, "fv7_uuid", {"email": "email"}, key="uuid")
    assert db.rows("SELECT uuid, email FROM fv7_uuid ORDER BY uuid") == first


def test_masked_mode_writes_the_masked_constants(db: Db, tmp_path: Path) -> None:
    create_shapes(db)
    fake(tmp_path, "fv7_shapes", KINDS, disable_seeding=True)
    rows = [row for row in shape_rows(db) if int(row[0]) <= 200]
    for row in rows:
        values = dict(zip(COLUMNS, row[1:], strict=True))
        assert re.match(r"^Name [0-9a-f]{8}$", values["name"])
        assert re.match(r"^First [0-9a-f]{8}$", values["first_name"])
        assert re.match(r"^Last [0-9a-f]{8}$", values["last_name"])
        assert re.match(r"^user\.[0-9a-f]{12}@example\.fake$", values["email"])
        assert values["txt"] == "Lorem ipsum."
        assert values["num"] == "100"
        assert values["zip"] == "00000"
        assert values["city"] == "City"
        assert values["phone"] == "+1 555 0100"
        assert values["ip4"] == "192.0.2.0"
        assert values["ip6"] == "2001:db8::"
    by_id = {row[0]: row[1:] for row in shape_rows(db)}
    assert by_id["1000"] == ["NULL"] * len(COLUMNS)


def test_a_custom_email_domain_is_used(db: Db, tmp_path: Path) -> None:
    db.execute("CREATE TABLE fv7_masked (id INT PRIMARY KEY, email VARCHAR(255) NULL)")
    db.execute("INSERT INTO fv7_masked VALUES (1, 'a@b.c')")
    fake(tmp_path, "fv7_masked", {"email": "email"}, email_domain="acme.test")
    email = db.rows("SELECT email FROM fv7_masked")[0][0]
    assert re.match(r"^[a-z]+\.[a-z]+\.[0-9a-f]{12}@acme\.test$", email)
