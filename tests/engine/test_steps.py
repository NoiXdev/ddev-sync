import pytest

from ddev_sync.engine.steps import (
    ALWAYS,
    SELECTION_GROUPS,
    STEP_ORDER,
    StepKind,
    hook_name,
    steps_for_selection,
)


def test_the_canonical_order_matches_the_spec() -> None:
    assert [kind.value for kind in STEP_ORDER] == [
        "preflight",
        "db.dump",
        "db.download",
        "db.import",
        "env",
        "files.sync",
        "s3.sync",
        "sanitize",
        "setup",
    ]


def test_preflight_always_runs() -> None:
    assert frozenset({StepKind.PREFLIGHT}) == ALWAYS


@pytest.mark.parametrize(
    ("kind", "phase", "expected"),
    [
        (StepKind.DB_DUMP, "pre", "pre_db_dump"),
        (StepKind.DB_IMPORT, "post", "post_db_import"),
        (StepKind.FILES_SYNC, "pre", "pre_files_sync"),
        (StepKind.SANITIZE, "post", "post_sanitize"),
        (StepKind.PREFLIGHT, "pre", "pre_preflight"),
    ],
)
def test_hook_names_replace_dots_with_underscores(
    kind: StepKind, phase: str, expected: str
) -> None:
    assert hook_name(kind, phase) == expected


def test_selecting_db_pulls_in_dump_download_import_and_env() -> None:
    assert steps_for_selection(["db"]) == (
        StepKind.PREFLIGHT,
        StepKind.DB_DUMP,
        StepKind.DB_DOWNLOAD,
        StepKind.DB_IMPORT,
        StepKind.ENV,
    )


def test_selection_is_returned_in_canonical_order_not_selection_order() -> None:
    assert steps_for_selection(["setup", "files", "db"]) == (
        StepKind.PREFLIGHT,
        StepKind.DB_DUMP,
        StepKind.DB_DOWNLOAD,
        StepKind.DB_IMPORT,
        StepKind.ENV,
        StepKind.FILES_SYNC,
        StepKind.SETUP,
    )


def test_selecting_s3_pulls_in_only_s3_sync() -> None:
    assert steps_for_selection(["s3"]) == (StepKind.PREFLIGHT, StepKind.S3_SYNC)


def test_an_empty_selection_still_runs_preflight() -> None:
    assert steps_for_selection([]) == (StepKind.PREFLIGHT,)


def test_a_repeated_selection_does_not_duplicate_steps() -> None:
    assert steps_for_selection(["db", "db"]) == steps_for_selection(["db"])


def test_an_unknown_selection_lists_the_valid_ones() -> None:
    with pytest.raises(ValueError) as caught:
        steps_for_selection(["database"])
    message = str(caught.value)
    assert "database" in message
    assert "db, files, s3, sanitize, setup" in message


def test_the_selection_groups_cover_every_non_preflight_step() -> None:
    covered = {kind for group in SELECTION_GROUPS.values() for kind in group}
    assert covered == set(STEP_ORDER) - ALWAYS
