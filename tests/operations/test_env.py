import dataclasses
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.docs import doc_link
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import HANDOVER_MARKER, HANDOVER_PATH
from ddev_sync.operations.env import (
    BLOCK_END,
    BLOCK_START,
    EnvRewriteOperation,
    append_block,
    confirm_env_change,
    declared_keys,
    expand_placeholders,
    mask_pulled,
    patch_keys,
    patch_pulled,
)
from ddev_sync.transport.fake import FakeCommandRunner
from tests.operations.phpdotenv_rules import load_like_phpdotenv


def test_an_existing_key_is_replaced_in_place() -> None:
    original = "APP_ENV=prod\nDB_HOST=10.0.0.1\nMAIL=smtp\n"
    result = patch_keys(original, {"DB_HOST": "ddev-acme-db"})
    assert result == "APP_ENV=prod\nDB_HOST=ddev-acme-db\nMAIL=smtp\n"


def test_patching_is_idempotent() -> None:
    once = patch_keys("DB_HOST=10.0.0.1\n", {"DB_HOST": "local"})
    twice = patch_keys(once, {"DB_HOST": "local"})
    assert once == twice


def test_a_new_key_is_appended_once() -> None:
    result = patch_keys("APP_ENV=prod\n", {"NEW_KEY": "value"})
    assert result == "APP_ENV=prod\nNEW_KEY=value\n"
    assert patch_keys(result, {"NEW_KEY": "value"}).count("NEW_KEY") == 1


def test_quoting_is_preserved_when_the_original_was_quoted() -> None:
    result = patch_keys('DB_HOST="10.0.0.1"\n', {"DB_HOST": "local"})
    assert result == 'DB_HOST="local"\n'


def test_single_quoting_is_preserved_too() -> None:
    assert patch_keys("DB_HOST='10.0.0.1'\n", {"DB_HOST": "local"}) == "DB_HOST='local'\n"


def test_comments_and_blank_lines_survive() -> None:
    original = "# database\n\nDB_HOST=10.0.0.1\n\n# mail\nMAIL=smtp\n"
    result = patch_keys(original, {"DB_HOST": "local"})
    assert result == "# database\n\nDB_HOST=local\n\n# mail\nMAIL=smtp\n"


def test_a_commented_out_key_is_not_treated_as_the_key() -> None:
    result = patch_keys("# DB_HOST=old\nDB_HOST=10.0.0.1\n", {"DB_HOST": "local"})
    assert result == "# DB_HOST=old\nDB_HOST=local\n"


def test_every_occurrence_of_a_duplicated_key_is_rewritten() -> None:
    # phpdotenv takes the last assignment in a file, so rewriting only the
    # first left the loader reading the value the sync had just decided was
    # wrong: a `.env` with the key twice beat the `DB_HOST` pin outright.
    original = "DB_HOST=prod.example.com\nDB_HOST=also-prod\n"
    assert patch_keys(original, {"DB_HOST": "ddev-acme-db"}) == (
        "DB_HOST=ddev-acme-db\nDB_HOST=ddev-acme-db\n"
    )


def test_each_occurrence_keeps_its_own_quoting() -> None:
    # `_requote` reads the line it is rewriting, so the second occurrence is
    # requoted from its own value rather than from the first one's.
    original = 'DB_URL=mysql://prod\nDB_URL="mysql://prod"\n'
    assert patch_keys(original, {"DB_URL": ""}) == 'DB_URL=\nDB_URL=""\n'


def test_a_duplicated_url_key_is_emptied_in_both_places(tmp_path: Path) -> None:
    # The Critical this blanking rests on, through the operation: an unblanked
    # URL is `array_merge`d over every discrete host, port, user and password
    # key beside it, and the loader would have read the surviving duplicate.
    (tmp_path / ".env").write_text(
        "DB_URL=mysql://app:secret@db.prod.example/app\n"
        "DB_HOST=db.prod.example\n"
        "DB_URL=mysql://app:secret@db.prod.example/app\n",
        encoding="utf-8",
    )
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "set": {"DB_URL": ""}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "DB_URL=\nDB_HOST=db.prod.example\nDB_URL=\n"
    )


def test_a_file_without_a_trailing_newline_gains_one() -> None:
    assert patch_keys("APP_ENV=prod", {"NEW": "x"}) == "APP_ENV=prod\nNEW=x\n"


def test_an_empty_file_gets_just_the_new_keys() -> None:
    assert patch_keys("", {"A": "1", "B": "2"}) == "A=1\nB=2\n"


def test_whitespace_around_the_equals_is_preserved() -> None:
    assert patch_keys("DB_HOST = old\n", {"DB_HOST": "new"}) == "DB_HOST = new\n"


def test_a_value_containing_an_equals_sign_is_written_whole() -> None:
    result = patch_keys("DSN=old\n", {"DSN": "mysql://u:p@h:3306/db?x=1"})
    assert result == "DSN=mysql://u:p@h:3306/db?x=1\n"


def test_an_empty_file_with_nothing_to_patch_stays_empty() -> None:
    # Neither branch of "lines to keep" nor "keys to append" fires here; this
    # is the one input that reaches patch_keys' otherwise-unexercised empty
    # result path rather than the "join and add a trailing newline" one.
    assert patch_keys("", {}) == ""


def test_the_block_is_appended_when_absent() -> None:
    result = append_block("APP_ENV=prod\n", {"DB_HOST": "local"})
    assert BLOCK_START in result
    assert "DB_HOST=local" in result
    assert result.startswith("APP_ENV=prod\n")


def test_appending_twice_leaves_exactly_one_block() -> None:
    once = append_block("APP_ENV=prod\n", {"DB_HOST": "local"})
    twice = append_block(once, {"DB_HOST": "other"})
    assert twice.count(BLOCK_START) == 1
    assert "DB_HOST=other" in twice
    assert "DB_HOST=local" not in twice


def test_content_after_the_block_is_preserved() -> None:
    original = append_block("A=1\n", {"B": "2"}) + "TRAILING=3\n"
    result = append_block(original, {"B": "9"})
    assert "TRAILING=3" in result
    assert result.count(BLOCK_START) == 1


def test_an_empty_file_gets_only_the_block() -> None:
    result = append_block("", {"A": "1"})
    assert result.startswith("\n" + BLOCK_START) or result.startswith(BLOCK_START)
    assert "A=1" in result


def test_a_missing_trailing_newline_gets_a_separator_before_the_block() -> None:
    # Covers the branch where the file is non-empty but does not already end
    # in "\n" - distinct from both the "ends in \n" and "file is empty" cases
    # above.
    result = append_block("APP_ENV=prod", {"DB_HOST": "local"})
    assert result == f"APP_ENV=prod\n\n{BLOCK_START}\nDB_HOST=local\n{BLOCK_END}\n"


def make_context(root: Path, **config: Any) -> ExecutionContext:
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
        runner=FakeCommandRunner(),
        downloads_dir=root / ".ddev" / ".downloads",
        assume_yes=True,
    )


def test_ddev_sitename_is_expanded(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    assert expand_placeholders({"DB_HOST": "ddev-${DDEV_SITENAME}-db"}, context) == {
        "DB_HOST": "ddev-acme-db"
    }


def test_the_target_database_placeholder_is_expanded(tmp_path: Path) -> None:
    context = make_context(tmp_path, database={"target_database": "acme_local"})
    assert expand_placeholders({"DB_NAME": "${SYNC_TARGET_DATABASE}"}, context) == {
        "DB_NAME": "acme_local"
    }


def test_the_target_database_placeholder_defaults_to_db(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    assert expand_placeholders({"DB_NAME": "${SYNC_TARGET_DATABASE}"}, context) == {"DB_NAME": "db"}


def test_an_unknown_placeholder_is_left_alone(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    assert expand_placeholders({"X": "${NOT_A_PLACEHOLDER}"}, context) == {
        "X": "${NOT_A_PLACEHOLDER}"
    }


def test_expand_placeholders_of_an_empty_mapping_is_empty(tmp_path: Path) -> None:
    assert expand_placeholders({}, make_context(tmp_path)) == {}


PROFILE_PATCH: dict[str, Any] = {
    "env": {
        "file": [".env.local", ".env"],
        "strategy": "patch_keys",
        "set": {"DB_HOST": "ddev-${DDEV_SITENAME}-db", "DB_USER": "db"},
    }
}


def test_the_first_existing_candidate_is_rewritten(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_HOST=10.0.0.1\nDB_USER=prod\n", encoding="utf-8")
    context = make_context(tmp_path, **PROFILE_PATCH)
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DB_HOST=ddev-acme-db\nDB_USER=db\n"


def test_env_local_wins_over_env_when_both_exist(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_HOST=wrong\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("DB_HOST=10.0.0.1\n", encoding="utf-8")
    EnvRewriteOperation().execute(make_context(tmp_path, **PROFILE_PATCH), {})
    assert "ddev-acme-db" in (tmp_path / ".env.local").read_text(encoding="utf-8")
    assert "wrong" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_the_none_strategy_touches_nothing(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_HOST=10.0.0.1\n", encoding="utf-8")
    context = make_context(tmp_path, env={"strategy": "none", "set": {"DB_HOST": "x"}})
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DB_HOST=10.0.0.1\n"


def test_the_append_block_strategy_is_selectable(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=prod\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "append_block", "set": {"DB_HOST": "local"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert BLOCK_START in (tmp_path / ".env").read_text(encoding="utf-8")


def test_an_unknown_strategy_lists_the_valid_ones(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    context = make_context(tmp_path, env={"file": [".env"], "strategy": "telepathy"})
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    assert "patch_keys, append_block, none" in str(caught.value)


def test_nothing_to_set_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    context = make_context(tmp_path, env={"file": [".env"], "strategy": "patch_keys"})
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "A=1\n"


def test_a_missing_env_file_names_every_candidate_it_looked_at(tmp_path: Path) -> None:
    context = make_context(tmp_path, **PROFILE_PATCH)
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    message = str(caught.value)
    assert ".env.local" in message
    assert ".env" in message
    assert str(tmp_path) in message


def test_a_differently_cased_file_is_never_silently_treated_as_a_match(tmp_path: Path) -> None:
    """A candidate name must match a real directory entry exactly.

    The tool this replaces would have trusted `Path.is_file()`, which folds
    case on a case-insensitive filesystem (the macOS default): probing for
    ".env" would then find a file the developer actually named ".ENV" and
    rewrite it, without ever telling them which file moved. `_find_env_file`
    instead compares candidate names against the literal directory entries
    returned by `iterdir()`, which preserves the on-disk casing on every
    platform - so this is a single, non-disjunctive assertion that holds
    identically on macOS and Linux, not two different behaviours joined by
    "or".
    """
    (tmp_path / ".ENV").write_text("DB_HOST=10.0.0.1\n", encoding="utf-8")
    context = make_context(tmp_path, **PROFILE_PATCH)
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    message = str(caught.value)
    assert ".env.local" in message
    assert ".env" in message
    assert (tmp_path / ".ENV").read_text(encoding="utf-8") == "DB_HOST=10.0.0.1\n"


def test_a_directory_named_like_the_env_file_is_not_mistaken_for_it(tmp_path: Path) -> None:
    (tmp_path / ".env").mkdir()
    context = make_context(tmp_path, **PROFILE_PATCH)
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    message = str(caught.value)
    assert ".env.local" in message
    assert ".env" in message


def test_a_string_file_setting_is_treated_as_a_single_candidate(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_HOST=10.0.0.1\n", encoding="utf-8")
    context = make_context(
        tmp_path, env={"file": ".env", "strategy": "patch_keys", "set": {"DB_HOST": "local"}}
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DB_HOST=local\n"


def test_a_non_mapping_env_node_is_treated_as_no_configuration(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    context = make_context(tmp_path, env="oops")
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "A=1\n"


def test_no_write_when_the_computed_content_already_matches(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_HOST=ddev-acme-db\nDB_USER=db\n", encoding="utf-8")
    before = (tmp_path / ".env").stat().st_mtime_ns
    EnvRewriteOperation().execute(make_context(tmp_path, **PROFILE_PATCH), {})
    after = (tmp_path / ".env").stat().st_mtime_ns
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DB_HOST=ddev-acme-db\nDB_USER=db\n"
    assert before == after


def test_declining_the_env_change_leaves_the_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddev_sync.operations import env as env_module

    monkeypatch.setattr(env_module, "confirm_env_change", lambda _diff: False)
    (tmp_path / ".env").write_text("DB_HOST=10.0.0.1\n", encoding="utf-8")
    context = ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=tmp_path),
        target=ResolvedTarget(name="p", host="h", user="u", port=22, path="/p/", stage=Stage.DEV),
        merged=merge_layers([LayerDoc("profile", PROFILE_PATCH)]),
        runner=FakeCommandRunner(),
        downloads_dir=tmp_path / ".downloads",
        assume_yes=False,
    )
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    assert "cancelled" in str(caught.value)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DB_HOST=10.0.0.1\n"


def test_approving_the_env_change_writes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddev_sync.operations import env as env_module

    seen: list[str] = []

    def fake_confirm(diff: str) -> bool:
        seen.append(diff)
        return True

    monkeypatch.setattr(env_module, "confirm_env_change", fake_confirm)
    (tmp_path / ".env").write_text("DB_HOST=10.0.0.1\nDB_USER=prod\n", encoding="utf-8")
    context = ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=tmp_path),
        target=ResolvedTarget(name="p", host="h", user="u", port=22, path="/p/", stage=Stage.DEV),
        merged=merge_layers([LayerDoc("profile", PROFILE_PATCH)]),
        runner=FakeCommandRunner(),
        downloads_dir=tmp_path / ".downloads",
        assume_yes=False,
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DB_HOST=ddev-acme-db\nDB_USER=db\n"
    assert len(seen) == 1
    assert "DB_HOST" in seen[0]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("y", True), ("yes", True), ("Y", True), ("n", False), ("", False), ("maybe", False)],
)
def test_confirm_env_change_reads_the_answer_from_stdin(
    monkeypatch: pytest.MonkeyPatch, typed: str, expected: bool
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: typed)
    assert confirm_env_change("--- a\n+++ b\n") is expected


def test_confirm_env_change_raises_a_clean_error_when_stdin_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def closed_stdin(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed_stdin)
    with pytest.raises(OperationError) as caught:
        confirm_env_change("--- a\n+++ b\n")
    assert "--yes" in str(caught.value)


def test_declared_keys_finds_an_ordinary_assignment() -> None:
    assert declared_keys("APP_ENV=local\nDB_HOST=db\n") == {"APP_ENV", "DB_HOST"}


def test_declared_keys_accepts_the_spacing_patch_keys_accepts() -> None:
    # `_ASSIGNMENT` allows `\s*=\s*`, and `patch_keys` rewrites such a line in
    # place. A presence test that missed it would call the key absent, write a
    # default, and leave the file with two assignments for one key.
    text = "QUEUE_CONNECTION =redis\nCACHE_STORE\t=\tredis\n"
    assert declared_keys(text) == {"QUEUE_CONNECTION", "CACHE_STORE"}
    assert patch_keys(text, {"QUEUE_CONNECTION": "sync"}).count("QUEUE_CONNECTION") == 1


def test_declared_keys_ignores_a_commented_out_key() -> None:
    # A comment is not a declaration: `_ASSIGNMENT` anchors the key at line
    # start, so `patch_keys` would append rather than rewrite, and a default
    # must apply for the same reason.
    assert declared_keys("# QUEUE_CONNECTION=redis\n") == set()


def test_declared_keys_counts_an_empty_value_as_declared() -> None:
    # `REDIS_PASSWORD=` means "no password", not "unconfigured". A written line
    # is a decision.
    assert declared_keys("REDIS_PASSWORD=\n") == {"REDIS_PASSWORD"}


def test_declared_keys_sees_the_forms_a_dotenv_loader_accepts() -> None:
    # Presence is deliberately wider than `patch_keys`' rewriting rule. Each
    # form here is one phpdotenv's `EntryParser` reads as a declaration: a
    # leading `export`, an indented name (both sides of the name are trimmed),
    # and a name wrapped in matching quotes. Missing any of them lets a default
    # be appended beside the project's own line - and phpdotenv takes the last
    # assignment in a file, so the appended default would win.
    text = (
        "export QUEUE_CONNECTION=redis\n"
        "  SESSION_DRIVER=redis\n"
        '"CACHE_STORE"=redis\n'
        "'MAIL_MAILER'=log\n"
    )
    assert declared_keys(text) == {
        "QUEUE_CONNECTION",
        "SESSION_DRIVER",
        "CACHE_STORE",
        "MAIL_MAILER",
    }


def test_declared_keys_ignores_an_indented_comment() -> None:
    # `Lines::isCommentOrWhitespace` drops a line whose first non-blank
    # character is `#`, so widening the presence test for indentation must not
    # widen it to indented comments: such a key is undeclared and a default
    # applies.
    assert declared_keys("  # QUEUE_CONNECTION=redis\n") == set()


def test_a_default_is_not_written_beside_an_export_declaration(tmp_path: Path) -> None:
    # The end of the chain the regex exists for: the project declared the key
    # in a form `patch_keys` cannot rewrite, so the step must write nothing at
    # all rather than append a default that the loader would then prefer.
    (tmp_path / ".env").write_text("export QUEUE_CONNECTION=redis\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "export QUEUE_CONNECTION=redis\n"


def test_an_imposed_key_beside_an_export_declaration_is_appended_and_wins(
    tmp_path: Path,
) -> None:
    # The other half of the asymmetry, and why `_ASSIGNMENT` stays narrow: the
    # `export` line is left exactly as the developer wrote it, the pin is
    # appended at the end, and phpdotenv's last-wins rule within a file makes
    # the appended line the one that decides. The pin costs a duplicate line,
    # not its effect.
    (tmp_path / ".env").write_text("export DB_HOST=prod.example.com\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "set": {"DB_HOST": "ddev-acme-db"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "export DB_HOST=prod.example.com\nDB_HOST=ddev-acme-db\n"
    )


def test_a_default_is_written_when_the_key_is_absent(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(
        encoding="utf-8"
    ) == "APP_ENV=local\nQUEUE_CONNECTION=sync\n"


def test_a_default_leaves_a_declared_key_alone(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("QUEUE_CONNECTION=redis\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "QUEUE_CONNECTION=redis\n"


def test_a_default_is_absent_when_another_candidate_declares_it(tmp_path: Path) -> None:
    # The write goes to the first existing candidate, but the presence test
    # spans all of them: a key set in `.env` while `.env.local` is the written
    # file would otherwise be overridden through Dotenv's overlay.
    (tmp_path / ".env.local").write_text("APP_ENV=dev\n", encoding="utf-8")
    (tmp_path / ".env").write_text("QUEUE_CONNECTION=redis\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env.local", ".env"],
            "strategy": "patch_keys",
            "default": {"QUEUE_CONNECTION": "sync"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env.local").read_text(encoding="utf-8") == "APP_ENV=dev\n"


def test_a_profile_with_only_defaults_still_runs(tmp_path: Path) -> None:
    # The early return used to trip on an empty `set` and would have made a
    # default-only profile silently inert.
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"CACHE_STORE": "file"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert "CACHE_STORE=file" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_a_default_expands_placeholders(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env"],
            "strategy": "patch_keys",
            "default": {"REDIS_HOST": "ddev-${DDEV_SITENAME}-redis"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert "REDIS_HOST=ddev-acme-redis" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_a_key_in_both_mappings_takes_the_imposed_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env"],
            "strategy": "patch_keys",
            "set": {"QUEUE_CONNECTION": "sync"},
            "default": {"QUEUE_CONNECTION": "redis"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert "QUEUE_CONNECTION=sync" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_a_default_is_inert_under_strategy_none(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(tmp_path, env={"strategy": "none", "default": {"CACHE_STORE": "file"}})
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "APP_ENV=local\n"


def test_a_default_under_append_block_is_an_error(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "append_block", "default": {"CACHE_STORE": "file"}},
    )
    with pytest.raises(OperationError, match="append_block"):
        EnvRewriteOperation().execute(context, {})


def test_a_default_does_not_invent_an_env_file(tmp_path: Path) -> None:
    # No candidate exists, so `_find_env_file` raises. A default must not be
    # written anywhere else, and the message must still name what was looked at.
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"CACHE_STORE": "file"}},
    )
    with pytest.raises(OperationError, match="no environment file found") as caught:
        EnvRewriteOperation().execute(context, {})
    assert (doc_link(caught.value) or "").endswith("#no-environment-file-in-the-project")
    assert list(tmp_path.iterdir()) == []


def test_a_candidate_that_does_not_exist_is_skipped_in_the_presence_test(tmp_path: Path) -> None:
    # `.env.local` comes first in the default candidate list but is not on disk;
    # the presence test must pass over it and still read `.env`.
    (tmp_path / ".env").write_text("QUEUE_CONNECTION=redis\nAPP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "strategy": "patch_keys",
            "default": {"QUEUE_CONNECTION": "sync", "CACHE_STORE": "file"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "QUEUE_CONNECTION=redis\nAPP_ENV=local\nCACHE_STORE=file\n"
    )


def test_an_imposed_empty_value_beats_a_default_for_the_same_key(tmp_path: Path) -> None:
    # `set: {KEY: ""}` is a deliberate blank - `REDIS_PASSWORD=` means "no
    # password". The collision filter is membership, not truthiness, so a
    # default must not turn that blank back into a value.
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env"],
            "strategy": "patch_keys",
            "set": {"REDIS_PASSWORD": ""},
            "default": {"REDIS_PASSWORD": "secret"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "APP_ENV=local\nREDIS_PASSWORD=\n"


def test_nothing_to_write_leaves_a_file_without_a_trailing_newline_untouched(
    tmp_path: Path,
) -> None:
    # Every default is already declared and there is no `set`, so `values` is
    # empty. `patch_keys(text, {})` would still append the missing newline, and
    # the step would write a file it had no change for.
    (tmp_path / ".env").write_bytes(b"QUEUE_CONNECTION=redis")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_bytes() == b"QUEUE_CONNECTION=redis"


# ---- env.pull: the write path ----


def test_a_pulled_value_replaces_the_whole_right_hand_side_without_requoting() -> None:
    assert patch_pulled('A="old"\n', {"A": "'n w'"}) == "A='n w'\n"
    assert patch_pulled("A=old # a comment\n", {"A": "new"}) == "A=new\n"


def test_a_pulled_value_replaces_every_line_that_assigns_it() -> None:
    assert patch_pulled("A=1\nB=2\nA=3\n", {"A": "x"}) == "A=x\nB=2\nA=x\n"


def test_a_pulled_value_keeps_the_gap() -> None:
    assert patch_pulled("A = old\n", {"A": "new"}) == "A = new\n"


def test_a_pulled_name_that_is_absent_is_appended() -> None:
    assert patch_pulled("B=2\n", {"A": "x"}) == "B=2\nA=x\n"
    assert patch_pulled("", {"A": "x"}) == "A=x\n"


@pytest.mark.parametrize("head", ["export A=", "  A=", '"A"=', "'A'=", "export  A = "])
def test_a_pulled_value_replaces_the_forms_a_loader_reads_as_assigning(head: str) -> None:
    # Spec decision 18 counts these as assigning the name. Left alone, one below a
    # plain line would go on deciding what the loader reads, the last one winning.
    assert patch_pulled(f"{head}old\n", {"A": "new"}) == f"{head}new\n"


def test_a_comment_is_not_an_assignment_of_a_pulled_name() -> None:
    assert patch_pulled("# A=old\n", {"A": "new"}) == "# A=old\nA=new\n"


def test_pulling_nothing_new_leaves_the_text_as_it_was() -> None:
    # A value that did not change must not prompt, and normalising a missing
    # trailing newline would be a change with no line to show in the diff.
    assert patch_pulled("A=1", {"A": "1"}) == "A=1"
    assert patch_pulled("B=2", {}) == "B=2"
    assert patch_pulled("", {}) == ""


@pytest.mark.parametrize("separator", ["\f", "\v", "\x1c", "\x85", "\N{LINE SEPARATOR}"])
def test_a_character_python_calls_a_line_break_stays_inside_its_value(separator: str) -> None:
    # phpdotenv only ends a line at CR or LF and `raw_assignments` copies these as
    # part of the value. `str.splitlines` would cut the line there, and every later
    # run would then corrupt it further and show the second half in the diff.
    text = f"A='x{separator}B=y'\n"
    assert patch_pulled(text, {"A": f"'x{separator}B=y'"}) == text
    assert patch_keys(text, {"C": "1"}) == f"{text}C=1\n"
    assert declared_keys(text) == {"A"}
    masked = mask_pulled(".env", "", text, {"A": "new"})
    assert masked.endswith("\n+A=***")
    assert "y'" not in masked


def test_masking_hides_both_sides_of_a_pulled_name() -> None:
    masked = mask_pulled(".env", "A=before\nB=keep\n", "A=after\nB=keep\n", {"A": "new"})
    assert "-A=***" in masked
    assert "+A=***" in masked
    assert "before" not in masked
    assert "after" not in masked


def test_masking_leaves_the_header_lines_and_other_names_alone() -> None:
    masked = mask_pulled(".env", "DB_HOST=a\nA=old\n", "DB_HOST=b\nA=new\n", {"A": "new"})
    assert masked.splitlines()[:2] == ["--- .env", "+++ .env (new)"]
    assert "-DB_HOST=a" in masked
    assert "+DB_HOST=b" in masked


def test_masking_hides_a_pulled_name_on_an_unchanged_context_line() -> None:
    # The diff prints three lines around a change, so a pulled value that did
    # not change still reaches the screen beside an imposed key that did. As a
    # context line and not a change: the mask must not hide which of the two it is.
    masked = mask_pulled(".env", "A=secret\nB=1\n", "A=secret\nB=2\n", {"A": "new"})
    assert " A=***" in masked.splitlines()
    assert "-A=***" not in masked
    assert "+A=***" not in masked
    assert "secret" not in masked


@pytest.mark.parametrize("line", ["export A=secret", "  A=secret", '"A"=secret', "A = secret"])
def test_masking_hides_every_form_of_assigning_a_pulled_name(line: str) -> None:
    masked = mask_pulled(".env", f"{line}\nB=1\n", f"{line}\nB=2\n", {"A": "new"})
    assert " A=***" in masked.splitlines()
    assert "secret" not in masked


def test_masking_nothing_is_the_plain_diff() -> None:
    assert mask_pulled(".env", "A=1\n", "A=2\n", {}).splitlines() == [
        "--- .env",
        "+++ .env (new)",
        "@@ -1 +1 @@",
        "-A=1",
        "+A=2",
    ]


@pytest.mark.parametrize("quote", ["'", '"'])
def test_masking_hides_a_value_appended_behind_a_quote_never_closed(quote: str) -> None:
    # The scanner takes the appended line as the closing line of the open quote,
    # because its value holds that quote. So the line is no assignment to it, and
    # would print in clear without being masked by what was appended.
    value = f"{quote}p w{quote}"
    before = f"NOTE={quote}unclosed\nX=1\n"
    after = patch_pulled(before, {"SECRET": value})
    assert after.endswith(f"SECRET={value}\n")
    masked = mask_pulled(".env", before, after, {"SECRET": value})
    assert "+SECRET=***" in masked.splitlines()
    assert "p w" not in masked


def test_the_phpdotenv_rules_helper_refuses_what_phpdotenv_refuses() -> None:
    # Both forms were seen to throw `InvalidFileException` in phpdotenv 5.7.
    with pytest.raises(AssertionError, match="InvalidFileException"):
        load_like_phpdotenv('A=new\ny"\nB=1\n')
    with pytest.raises(AssertionError, match="unexpected whitespace"):
        load_like_phpdotenv('A=\'x="y\'\nB=2\nC="three"\n')
    assert load_like_phpdotenv('# c\n\nA="x\ny"\nB=1 # c\n') == {"A": "x\ny", "B": "1"}


@pytest.mark.parametrize(
    "start",
    [
        'A="x\ny"\nB=1\n',
        'A="-----BEGIN\nabc=def\n-----END"\nB=1\n',
        "A='x\nsecret2\n'\nB=1\n",
        'export A="x\ny" # a comment\nB=1\n',
    ],
)
def test_a_multi_line_local_value_is_replaced_whole(start: str) -> None:
    written = patch_pulled(start, {"A": "new"})
    head = "export " if start.startswith("export") else ""
    assert written == f"{head}A=new\nB=1\n"
    assert load_like_phpdotenv(written) == {"A": "new", "B": "1"}
    assert patch_pulled(written, {"A": "new"}) == written


def test_a_line_inside_another_names_value_assigns_nothing() -> None:
    start = 'PEM="l1\nA=inner\nl3"\nB=1\n'
    written = patch_pulled(start, {"A": "new"})
    assert written == 'PEM="l1\nA=inner\nl3"\nB=1\nA=new\n'
    assert load_like_phpdotenv(written) == {"PEM": "l1\nA=inner\nl3", "B": "1", "A": "new"}


def test_a_name_assigned_beside_another_names_value_is_replaced_only_where_it_assigns() -> None:
    start = 'A=old\nPEM="l1\nA=inner\nl3"\n'
    assert patch_pulled(start, {"A": "new"}) == 'A=new\nPEM="l1\nA=inner\nl3"\n'


def test_a_multi_line_value_of_another_name_is_kept_as_it_is_when_nothing_changes() -> None:
    start = 'PEM="l1\nA=inner\nl3"\nA=same\n'
    assert patch_pulled(start, {"A": "same"}) == start


def test_an_unclosed_quote_takes_only_its_own_line() -> None:
    # The file was unreadable already; the lines behind it stay the developer's.
    assert patch_pulled('A="x\nB=1\n', {"A": "new"}) == "A=new\nB=1\n"
    assert patch_pulled('A="x\nB=1\n', {"B": "new"}) == 'A="x\nB=new\n'


@pytest.mark.parametrize(
    "start",
    ['A="x\ny"\nB=1\n', 'A="-----BEGIN\nabc=def\n-----END"\nB=1\n', "A='x\nsecret2\n'\nB=1\n"],
)
def test_the_mask_takes_a_whole_multi_line_value(start: str) -> None:
    masked = mask_pulled(".env", start, "A=new\nB=1\n", {"A": "new"})
    assert masked.splitlines() == [
        "--- .env",
        "+++ .env (new)",
        masked.splitlines()[2],
        "-A=***",
        "+A=***",
        " B=1",
    ]


def test_the_mask_says_when_a_multi_line_value_did_not_change() -> None:
    masked = mask_pulled(".env", 'A="x\ny"\nB=1\n', 'A="x\ny"\nB=2\n', {"A": "new"})
    assert " A=***" in masked.splitlines()
    assert "-A=***" not in masked
    assert "x" not in masked.replace(".env", "")


def test_the_mask_leaves_a_line_inside_another_names_value_alone() -> None:
    masked = mask_pulled(
        ".env", 'PEM="l1\nA=inner\nl3"\n', 'PEM="l1\nA=inner\nl3"\nA=new\n', {"A": "new"}
    )
    assert " A=inner" in masked.splitlines()
    assert "+A=***" in masked.splitlines()


REMOTE_ENV = "/srv/app/.env"


def pull_context(
    root: Path,
    remote: str,
    *,
    assume_yes: bool = True,
    notices: list[str] | None = None,
    **env: Any,
) -> ExecutionContext:
    """A project whose remote `.env` holds `remote`, with `env` as its `env:` block."""
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"{REMOTE_ENV}\n{remote}")
    context = dataclasses.replace(make_context(root, env=env), runner=runner, assume_yes=assume_yes)
    if notices is not None:
        return dataclasses.replace(context, notice=notices.append)
    return context


def seen_diffs(monkeypatch: pytest.MonkeyPatch, *, answer: bool = True) -> list[str]:
    """Replace the prompt; what it was asked with lands in the returned list."""
    diffs: list[str] = []

    def fake_confirm(diff: str) -> bool:
        diffs.append(diff)
        return answer

    monkeypatch.setattr("ddev_sync.operations.env.confirm_env_change", fake_confirm)
    return diffs


def write_env(root: Path, text: str, name: str = ".env") -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_pulled_text_lands_in_the_file(tmp_path: Path) -> None:
    env = write_env(tmp_path, 'APP_KEY="old"\nKEEP=1\n')
    context = pull_context(
        tmp_path, "APP_KEY='base64:x y'\nOTHER=no\nNEW=\"a b\"\n", pull=["APP_KEY", "NEW"]
    )
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "APP_KEY='base64:x y'\nKEEP=1\nNEW=\"a b\"\n"


def test_a_pulled_value_from_the_handover_is_written_encoded(tmp_path: Path) -> None:
    env = write_env(tmp_path, "A=old\n")
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"{HANDOVER_PATH}\n{HANDOVER_MARKER}\nA=p#ss $x\n")
    context = dataclasses.replace(pull_context(tmp_path, "", pull=["A"]), runner=runner)
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "A='p#ss $x'\n"


def test_a_placeholder_in_a_pulled_value_is_not_expanded(tmp_path: Path) -> None:
    # `${DDEV_SITENAME}` is one the context really resolves: in a production
    # password it is the password.
    env = write_env(tmp_path, "HOST=old\n")
    context = pull_context(
        tmp_path,
        "A='${DDEV_SITENAME}-${SECRET}'\n",
        pull=["A"],
        set={"HOST": "${DDEV_SITENAME}"},
    )
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "HOST=acme\nA='${DDEV_SITENAME}-${SECRET}'\n"


def test_a_name_in_env_set_keeps_the_imposed_value(tmp_path: Path) -> None:
    env = write_env(tmp_path, "A=old\n")
    context = pull_context(tmp_path, "A=remote\nB=2\n", pull=["A", "B"], set={"A": "imposed"})
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "A=imposed\nB=2\n"


def test_a_file_that_started_multi_line_is_healed_and_stays_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = write_env(tmp_path, "A='old\nsecret2\n'\nB=1\n")
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    context = pull_context(tmp_path, "A=new\n", assume_yes=False, pull=["A"])
    EnvRewriteOperation().execute(context, {})
    printed = capsys.readouterr().out
    assert "-A=***" in printed
    assert "+A=***" in printed
    assert "secret2" not in printed
    assert env.read_text(encoding="utf-8") == "A=new\nB=1\n"
    assert load_like_phpdotenv(env.read_text(encoding="utf-8")) == {"A": "new", "B": "1"}

    diffs = seen_diffs(monkeypatch)
    EnvRewriteOperation().execute(
        pull_context(tmp_path, "A=new\n", assume_yes=False, pull=["A"]), {}
    )
    assert diffs == []


def test_only_names_in_env_set_make_no_remote_read(tmp_path: Path) -> None:
    env = write_env(tmp_path, "A=old\n")
    runner = FakeCommandRunner()
    context = dataclasses.replace(
        pull_context(tmp_path, "", pull=["A"], set={"A": "imposed"}), runner=runner
    )
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "A=imposed\n"
    assert runner.calls == []


def test_a_pulled_name_in_env_default_keeps_the_pulled_value(tmp_path: Path) -> None:
    env = write_env(tmp_path, "OTHER=1\n")
    context = pull_context(
        tmp_path, "A=remote\n", pull=["A"], default={"A": "fallback", "B": "written"}
    )
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "OTHER=1\nA=remote\nB=written\n"


def test_the_printed_diff_holds_no_pulled_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_env(tmp_path, "A=oldsecret\nB=unchangedsecret\nDB_HOST=prod\n")
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    context = pull_context(
        tmp_path,
        "A=newsecret\nB=unchangedsecret\n",
        assume_yes=False,
        pull=["A", "B"],
        set={"DB_HOST": "local"},
    )
    EnvRewriteOperation().execute(context, {})
    printed = capsys.readouterr().out
    assert "-A=***" in printed
    assert "+A=***" in printed
    assert " B=***" in printed
    assert "secret" not in printed
    assert "-DB_HOST=prod" in printed
    assert "+DB_HOST=local" in printed


def test_a_reference_is_named_in_a_notice(tmp_path: Path) -> None:
    env = write_env(tmp_path, "")
    seen: list[str] = []
    context = pull_context(
        tmp_path,
        'VITE_NAME="${APP_NAME}"\nPLAIN=topsecret\n',
        notices=seen,
        pull=["VITE_NAME", "PLAIN"],
    )
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == 'VITE_NAME="${APP_NAME}"\nPLAIN=topsecret\n'
    assert len(seen) == 1
    assert "VITE_NAME" in seen[0]
    assert "PLAIN" not in seen[0]
    assert "topsecret" not in seen[0]
    assert "APP_NAME" not in seen[0]


def test_no_reference_means_no_notice(tmp_path: Path) -> None:
    write_env(tmp_path, "")
    seen: list[str] = []
    EnvRewriteOperation().execute(pull_context(tmp_path, "A=1\n", notices=seen, pull=["A"]), {})
    assert seen == []


def test_a_name_in_env_set_is_not_named_as_a_reference(tmp_path: Path) -> None:
    write_env(tmp_path, "")
    seen: list[str] = []
    context = pull_context(
        tmp_path, 'A="$B"\nC=1\n', notices=seen, pull=["A", "C"], set={"A": "imposed"}
    )
    EnvRewriteOperation().execute(context, {})
    assert seen == []


def test_a_second_run_changes_nothing_and_does_not_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = write_env(tmp_path, 'A="old"\nDB_HOST=prod\n')
    remote = "A=' tricky\f$x '\nB=\"$A\"\n"
    settings: dict[str, Any] = {"pull": ["A", "B"], "set": {"DB_HOST": "local"}}
    EnvRewriteOperation().execute(pull_context(tmp_path, remote, **settings), {})
    written = env.read_bytes()

    diffs = seen_diffs(monkeypatch)
    EnvRewriteOperation().execute(pull_context(tmp_path, remote, assume_yes=False, **settings), {})
    assert diffs == []
    assert env.read_bytes() == written


def test_a_pulled_value_that_did_not_change_does_not_prompt_or_normalise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_bytes(b"A=1")
    diffs = seen_diffs(monkeypatch)
    EnvRewriteOperation().execute(pull_context(tmp_path, "A=1\n", assume_yes=False, pull=["A"]), {})
    assert diffs == []
    assert env.read_bytes() == b"A=1"


def test_a_missing_file_is_created_from_the_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = "APP_ENV=local\nDECLARED=mine\nA=\n"
    write_env(tmp_path, example, ".env.example")
    diffs = seen_diffs(monkeypatch)
    context = pull_context(
        tmp_path,
        "A=secret\n",
        assume_yes=False,
        pull=["A"],
        file=[".env.app", ".env.other"],
        set={"APP_ENV": "local"},
        default={"DECLARED": "theirs", "ABSENT": "written"},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env.app").read_text(encoding="utf-8") == (
        "APP_ENV=local\nDECLARED=mine\nA=secret\nABSENT=written\n"
    )
    assert not (tmp_path / ".env.other").exists()
    assert (tmp_path / ".env.example").read_text(encoding="utf-8") == example
    # New in the diff: every line added, none taken away, and no value in it.
    assert "+APP_ENV=local" in diffs[0]
    assert "+A=***" in diffs[0]
    assert "secret" not in diffs[0]
    assert [line for line in diffs[0].splitlines()[1:] if line.startswith("-")] == []


def test_a_file_the_values_would_not_change_is_still_created_from_the_example(
    tmp_path: Path,
) -> None:
    write_env(tmp_path, "APP_ENV=local\nA=1\n", ".env.example")
    context = pull_context(tmp_path, "A=1\n", pull=["A"], set={"APP_ENV": "local"})
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env.local").read_text(encoding="utf-8") == "APP_ENV=local\nA=1\n"


def test_an_empty_env_local_is_written_rather_than_the_tracked_env(tmp_path: Path) -> None:
    # What the docs tell a Symfony or Shopware project to do, whose `.env` is committed.
    tracked = write_env(tmp_path, "APP_ENV=dev\n")
    local = write_env(tmp_path, "", ".env.local")
    context = pull_context(tmp_path, "A=secret\n", pull=["A"], file=[".env.local", ".env"])
    EnvRewriteOperation().execute(context, {})
    assert local.read_text(encoding="utf-8") == "A=secret\n"
    assert tracked.read_text(encoding="utf-8") == "APP_ENV=dev\n"


def test_an_existing_file_is_used_rather_than_the_example(tmp_path: Path) -> None:
    write_env(tmp_path, "FROM_EXAMPLE=1\n", ".env.example")
    env = write_env(tmp_path, "MINE=1\n")
    EnvRewriteOperation().execute(pull_context(tmp_path, "A=1\n", pull=["A"]), {})
    assert env.read_text(encoding="utf-8") == "MINE=1\nA=1\n"
    assert not (tmp_path / ".env.local").exists()


def test_declining_creates_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(tmp_path, "A=\n", ".env.example")
    seen_diffs(monkeypatch, answer=False)
    context = pull_context(tmp_path, "A=1\n", assume_yes=False, pull=["A"])
    with pytest.raises(OperationError, match="cancelled"):
        EnvRewriteOperation().execute(context, {})
    assert [entry.name for entry in tmp_path.iterdir()] == [".env.example"]


def test_declining_leaves_the_existing_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = write_env(tmp_path, "A=old\n")
    seen_diffs(monkeypatch, answer=False)
    context = pull_context(tmp_path, "A=new\n", assume_yes=False, pull=["A"])
    with pytest.raises(OperationError, match="cancelled"):
        EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "A=old\n"


def test_creating_never_overwrites_what_the_probe_did_not_recognise(tmp_path: Path) -> None:
    # A dangling link stands in for the `.ENV` a case-insensitive filesystem would
    # hand back for `.env`: the probe compares exact names and takes this for no
    # file, so the create has to be the thing that refuses, in the sync's own
    # words. Opening it to write would go through the link and create the target.
    write_env(tmp_path, "A=\n", ".env.example")
    target = tmp_path / "elsewhere"
    (tmp_path / ".env.local").symlink_to(target)
    context = pull_context(tmp_path, "A=1\n", pull=["A"])
    with pytest.raises(OperationError, match=r"could not create .*\.env\.local"):
        EnvRewriteOperation().execute(context, {})
    assert not target.exists()


def test_a_created_file_can_be_read_by_its_owner_alone(tmp_path: Path) -> None:
    # It holds production values, and umask is what would decide otherwise.
    write_env(tmp_path, "A=\n", ".env.example")
    EnvRewriteOperation().execute(pull_context(tmp_path, "A=1\n", pull=["A"]), {})
    assert (tmp_path / ".env.local").stat().st_mode & 0o777 == 0o600


def test_an_existing_file_keeps_its_mode(tmp_path: Path) -> None:
    env = write_env(tmp_path, "A=old\n")
    env.chmod(0o640)
    EnvRewriteOperation().execute(pull_context(tmp_path, "A=1\n", pull=["A"]), {})
    assert env.read_text(encoding="utf-8") == "A=1\n"
    assert env.stat().st_mode & 0o777 == 0o640


def test_a_project_without_env_pull_makes_no_remote_read(tmp_path: Path) -> None:
    env = write_env(tmp_path, "A=old\n")
    runner = FakeCommandRunner()
    context = dataclasses.replace(pull_context(tmp_path, "", set={"A": "imposed"}), runner=runner)
    EnvRewriteOperation().execute(context, {})
    assert env.read_text(encoding="utf-8") == "A=imposed\n"
    assert runner.calls == []


def test_a_project_without_env_pull_does_not_create_the_file(tmp_path: Path) -> None:
    write_env(tmp_path, "A=\n", ".env.example")
    context = pull_context(tmp_path, "", set={"A": "imposed"})
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    assert (doc_link(caught.value) or "").endswith("#no-environment-file-in-the-project")
    assert not (tmp_path / ".env.local").exists()


def test_with_env_pull_and_no_example_the_existing_refusal_still_stands(tmp_path: Path) -> None:
    # Preflight refuses this first; the step does not make up a file if it ever
    # got this far.
    context = pull_context(tmp_path, "A=1\n", pull=["A"])
    with pytest.raises(OperationError) as caught:
        EnvRewriteOperation().execute(context, {})
    assert (doc_link(caught.value) or "").endswith("#no-environment-file-in-the-project")
