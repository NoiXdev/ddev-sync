from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    DUMP_FILENAME,
    DbImportOperation,
    confirm_destructive,
    strip_definers,
)
from ddev_sync.transport.fake import FakeCommandRunner

COMPLETE_DUMP = "CREATE TABLE a (id int);\n-- Dump completed on 2026-09-30\n"


def make_context(
    runner: FakeCommandRunner, root: Path, *, assume_yes: bool = True, **config: Any
) -> ExecutionContext:
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
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
        assume_yes=assume_yes,
    )


def with_dump(context: ExecutionContext, body: str = COMPLETE_DUMP) -> Path:
    context.downloads_dir.mkdir(parents=True, exist_ok=True)
    path = context.downloads_dir / DUMP_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


def import_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysql"], stdout="")
    return runner


def test_the_database_is_dropped_created_and_imported_in_that_order(tmp_path: Path) -> None:
    runner = import_runner()
    context = make_context(runner, tmp_path)
    with_dump(context)
    DbImportOperation().execute(context, {})
    verbs = [call[0] for call in runner.calls]
    assert verbs == ["mysqladmin", "mysqladmin", "mysql"]
    assert "drop" in runner.calls[0]
    assert "create" in runner.calls[1]


def test_the_import_targets_the_ddev_database_container(tmp_path: Path) -> None:
    runner = import_runner()
    context = make_context(runner, tmp_path)
    with_dump(context)
    DbImportOperation().execute(context, {})
    assert "--host=ddev-acme-db" in runner.calls[2]


def test_the_target_database_name_is_configurable(tmp_path: Path) -> None:
    runner = import_runner()
    context = make_context(runner, tmp_path, database={"target_database": "acme"})
    with_dump(context)
    DbImportOperation().execute(context, {})
    assert "acme" in runner.calls[0]
    assert "acme" in runner.calls[2]


def test_the_default_target_database_is_db(tmp_path: Path) -> None:
    runner = import_runner()
    context = make_context(runner, tmp_path)
    with_dump(context)
    DbImportOperation().execute(context, {})
    assert "db" in runner.calls[0]


def test_the_dump_is_removed_after_a_successful_import(tmp_path: Path) -> None:
    runner = import_runner()
    context = make_context(runner, tmp_path)
    path = with_dump(context)
    DbImportOperation().execute(context, {})
    assert not path.exists()


def test_keep_dump_leaves_the_file_in_place(tmp_path: Path) -> None:
    runner = import_runner()
    context = ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {})]),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
        assume_yes=True,
        keep_dump=True,
    )
    path = with_dump(context)
    DbImportOperation().execute(context, {})
    assert path.exists()


def test_a_missing_dump_says_which_step_should_have_produced_it(tmp_path: Path) -> None:
    context = make_context(import_runner(), tmp_path)
    with pytest.raises(OperationError) as caught:
        DbImportOperation().execute(context, {})
    message = str(caught.value)
    assert DUMP_FILENAME in message
    assert "db.dump" in message


def test_an_incomplete_dump_is_refused_at_import_time_too(tmp_path: Path) -> None:
    context = make_context(import_runner(), tmp_path)
    with_dump(context, "CREATE TABLE a (id int);\n")
    with pytest.raises(OperationError) as caught:
        DbImportOperation().execute(context, {})
    assert "incomplete" in str(caught.value)


def test_a_failing_import_reports_the_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysql"], returncode=1, stderr="ERROR 1064 at line 12")
    context = make_context(runner, tmp_path)
    with_dump(context)
    with pytest.raises(OperationError) as caught:
        DbImportOperation().execute(context, {})
    assert "ERROR 1064" in str(caught.value)


def test_definer_clauses_are_removed(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text(
        "CREATE DEFINER=`root`@`localhost` PROCEDURE p() BEGIN END;\n", encoding="utf-8"
    )
    strip_definers(path)
    assert path.read_text(encoding="utf-8") == "CREATE PROCEDURE p() BEGIN END;\n"


def test_a_dump_without_definers_is_unchanged(tmp_path: Path) -> None:
    original = "CREATE TABLE a (id int);\n-- Dump completed\n"
    path = tmp_path / "dump.sql"
    path.write_text(original, encoding="utf-8")
    strip_definers(path)
    assert path.read_text(encoding="utf-8") == original


def test_several_definers_on_separate_lines_are_all_removed(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text(
        "CREATE DEFINER=`a`@`h` VIEW v AS SELECT 1;\nCREATE DEFINER=`b`@`h` VIEW w AS SELECT 2;\n",
        encoding="utf-8",
    )
    strip_definers(path)
    assert "DEFINER" not in path.read_text(encoding="utf-8")


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text("SELECT 1;\n", encoding="utf-8")
    strip_definers(path)
    assert list(tmp_path.iterdir()) == [path]


def test_binary_content_does_not_break_the_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_bytes(b"INSERT INTO blobs VALUES (0x\xff\xfe\x00);\n")
    strip_definers(path)
    assert path.read_bytes() == b"INSERT INTO blobs VALUES (0x\xff\xfe\x00);\n"


def test_without_assume_yes_the_drop_asks_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    from ddev_sync.operations import database as database_module

    def fake_confirm(question: str) -> bool:
        asked.append(question)
        return True

    monkeypatch.setattr(database_module, "confirm_destructive", fake_confirm)
    runner = import_runner()
    context = make_context(runner, tmp_path, assume_yes=False)
    with_dump(context)
    DbImportOperation().execute(context, {})
    assert len(asked) == 1
    assert "drop" in asked[0].lower()
    assert "db" in asked[0]


def test_declining_the_confirmation_stops_before_any_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddev_sync.operations import database as database_module

    monkeypatch.setattr(database_module, "confirm_destructive", lambda _question: False)
    runner = import_runner()
    context = make_context(runner, tmp_path, assume_yes=False)
    with_dump(context)
    with pytest.raises(OperationError) as caught:
        DbImportOperation().execute(context, {})
    assert "cancelled" in str(caught.value)
    assert runner.calls == []


def test_assume_yes_skips_the_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ddev_sync.operations import database as database_module

    def explode(_question: str) -> bool:
        raise AssertionError("should not have asked")

    monkeypatch.setattr(database_module, "confirm_destructive", explode)
    context = make_context(import_runner(), tmp_path, assume_yes=True)
    with_dump(context)
    DbImportOperation().execute(context, {})


def test_dropping_a_database_that_does_not_exist_is_not_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysqladmin"], returncode=1, stderr="database doesn't exist")
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysql"], stdout="")
    context = make_context(runner, tmp_path)
    with_dump(context)
    DbImportOperation().execute(context, {})


def test_a_failing_create_is_an_error(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysqladmin"], returncode=1, stderr="access denied")
    context = make_context(runner, tmp_path)
    with_dump(context)
    with pytest.raises(OperationError) as caught:
        DbImportOperation().execute(context, {})
    assert "access denied" in str(caught.value)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("y", True), ("yes", True), ("Y", True), ("n", False), ("", False), ("maybe", False)],
)
def test_confirm_destructive_reads_the_answer_from_stdin(
    monkeypatch: pytest.MonkeyPatch, typed: str, expected: bool
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: typed)
    assert confirm_destructive("Drop the local database?") is expected
