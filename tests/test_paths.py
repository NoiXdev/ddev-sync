from pathlib import Path

from ddev_sync.paths import existing_or_fallback


def test_the_primary_path_wins_when_it_exists(tmp_path: Path) -> None:
    primary = tmp_path / "installed"
    primary.mkdir()
    fallback = tmp_path / "checkout"
    fallback.mkdir()
    assert existing_or_fallback(primary, fallback) == primary


def test_the_fallback_is_used_when_the_primary_is_missing(tmp_path: Path) -> None:
    primary = tmp_path / "installed"
    fallback = tmp_path / "checkout"
    fallback.mkdir()
    assert not primary.exists()
    assert existing_or_fallback(primary, fallback) == fallback


def test_a_file_at_the_primary_path_does_not_count_as_a_directory(tmp_path: Path) -> None:
    # A stray file - not a directory - at the primary path must not be
    # treated as the installed root; the fallback is still correct.
    primary = tmp_path / "installed"
    primary.write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "checkout"
    fallback.mkdir()
    assert existing_or_fallback(primary, fallback) == fallback


def test_the_fallback_is_returned_even_when_it_also_does_not_exist(tmp_path: Path) -> None:
    # Nothing here claims the fallback is guaranteed to exist - callers that
    # care (e.g. ProfileRegistry.resolve) already raise their own readable
    # error when neither root has anything in it.
    primary = tmp_path / "installed"
    fallback = tmp_path / "checkout"
    assert existing_or_fallback(primary, fallback) == fallback
