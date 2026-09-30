from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.env import (
    BLOCK_END,
    BLOCK_START,
    EnvRewriteOperation,
    append_block,
    confirm_env_change,
    expand_placeholders,
    patch_keys,
)
from ddev_sync.transport.fake import FakeCommandRunner


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
