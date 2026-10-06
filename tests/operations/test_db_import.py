from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.errors import InterruptedAtPrompt
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    DUMP_FILENAME,
    LOCAL_DB_TLS_OPTION,
    DbImportOperation,
    confirm_destructive,
    strip_definers,
)
from ddev_sync.transport.fake import FakeCommandRunner
from ddev_sync.transport.runner import CommandResult

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


DEFINER_DUMP = "CREATE DEFINER=`root`@`localhost` VIEW v AS SELECT 1;\n-- Dump completed\n"


class DefinerWatchingRunner(FakeCommandRunner):
    """Records, per command, whether the dump still carried its DEFINER clause.

    The only way to observe *when* the rewrite happened relative to the drop
    from outside the operation: `strip_definers` touches no runner, so the
    file itself is the evidence.
    """

    def __init__(self, dump: Path) -> None:
        super().__init__()
        self._dump = dump
        self.definer_present: list[tuple[str, bool]] = []

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        self.definer_present.append((argv[0], b"DEFINER" in self._dump.read_bytes()))
        return super().run(argv, **kwargs)


def test_the_dump_is_rewritten_after_the_drop_and_before_the_import(tmp_path: Path) -> None:
    """`strip_definers` used to rewrite the whole dump *before* `_recreate`,
    which is where the "this will drop the local database" confirmation
    lives. A Ctrl-C in that window is not an `InterruptedAtPrompt`, so it was
    reported as the database possibly being part-way replaced with production
    data while nothing had been dropped or imported - and under `--yes` there
    was no prompt in front of it at all, making it the longest unguarded
    window in the step. The documentation's condition for that sentence is
    "during the import, once the drop has begun"; this ordering is what makes
    it true.
    """
    runner = FakeCommandRunner()
    context = make_context(runner, tmp_path)
    dump = with_dump(context, DEFINER_DUMP)
    watching = DefinerWatchingRunner(dump)
    watching.script_prefix(["mysqladmin"], stdout="")
    watching.script_prefix(["mysqladmin"], stdout="")
    watching.script_prefix(["mysql"], stdout="")
    DbImportOperation().execute(replace(context, runner=watching), {})
    assert watching.definer_present == [
        ("mysqladmin", True),
        ("mysqladmin", True),
        ("mysql", False),
    ]


def test_an_interrupt_at_the_drop_prompt_leaves_the_dump_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same ordering seen from the user's side: changing your mind at the
    prompt must leave the downloaded dump exactly as it was downloaded. It
    did not - the rewrite had already replaced the file - which is a write
    the "that step had not changed anything" message denies happened.

    Ctrl-C is delivered through the real prompt (`input` raising
    `KeyboardInterrupt`), so what escapes is the real `InterruptedAtPrompt`
    the executor reads, not a stand-in for it.
    """

    def interrupted(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    runner = import_runner()
    context = make_context(runner, tmp_path, assume_yes=False)
    dump = with_dump(context, DEFINER_DUMP)
    with pytest.raises(InterruptedAtPrompt):
        DbImportOperation().execute(context, {})
    assert dump.read_text(encoding="utf-8") == DEFINER_DUMP
    assert runner.calls == []


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


def test_confirm_destructive_raises_a_clean_error_when_stdin_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def closed_stdin(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed_stdin)
    with pytest.raises(OperationError) as caught:
        confirm_destructive("Drop the local database?")
    assert "--yes" in str(caught.value)


def test_a_failed_dump_rewrite_says_the_local_database_is_empty(
    tmp_path: Path,
) -> None:
    # The drop runs before the rewrite (see `execute`'s own comment), so an
    # OSError here leaves an empty database behind - the one thing the user
    # has to know, and the one thing a bare traceback did not say.
    runner = FakeCommandRunner()
    runner.script_prefix(["mysqladmin"], stdout="")
    runner.script_prefix(["mysqladmin"], stdout="")
    context = make_context(runner, tmp_path)
    dump = with_dump(context)
    (context.downloads_dir / f"{DUMP_FILENAME}.tmp").mkdir()

    with pytest.raises(OperationError) as error:
        DbImportOperation().execute(context, {})

    message = str(error.value)
    assert "is empty" in message
    assert str(dump) in message
    # The message promises both of these, so they are the test's business.
    assert dump.read_text(encoding="utf-8") == COMPLETE_DUMP
    assert repr("db") in message
    # The drop really did run first, which is what makes that claim true -
    # and `mysql` never ran, so nothing was imported.
    assert [call[0] for call in runner.calls] == ["mysqladmin", "mysqladmin"]
    assert "drop" in runner.calls[0]


def test_every_local_client_call_relaxes_tls_verification(
    tmp_path: Path,
) -> None:
    # This image's client is 11.8, which requires a verified TLS connection by
    # default. DDEV's MariaDB 10.x images configure no certificate at all, so
    # the client refuses outright and every local call fails - measured on a
    # real project running 10.8, where it took out the drop, the create and the
    # import. See `LOCAL_DB_TLS_OPTION` for what the option costs on the
    # versions that do have one. Asserted through the constant, so it keeps
    # holding if the spelling changes.
    runner = import_runner()
    context = make_context(runner, tmp_path)
    with_dump(context)
    DbImportOperation().execute(context, {})
    assert [call[0] for call in runner.calls] == ["mysqladmin", "mysqladmin", "mysql"]
    for call in runner.calls:
        assert LOCAL_DB_TLS_OPTION in call, call
