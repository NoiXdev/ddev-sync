from pathlib import Path

import pytest

from tools.apply_verified_rows import apply_rows, emit_row, main


def test_emit_names_the_variant_the_image_and_the_run() -> None:
    row = emit_row("mxlaravel", image="ddev-sync:ci", run="https://example.invalid/run/1")
    assert row.startswith("| laravel ")
    assert "ddev-sync:ci" in row
    assert "https://example.invalid/run/1" in row


def test_emit_keys_the_row_by_the_profile_not_the_cell_name() -> None:
    # `mxtypo313` is the cell; `typo3@13` is the row in VERIFIED.md. The only
    # pair where the two strings differ, so the only test that can catch them
    # being swapped - and a swap would silently fill nothing.
    row = emit_row("mxtypo313", image="ddev-sync:ci", run="https://example.invalid/run/1")
    assert row.startswith("| typo3@13 ")


def test_emit_rejects_a_cell_that_is_not_in_the_registry() -> None:
    # The registry is the single list of what the matrix covers; a typo must
    # not invent a row for a variant nothing verified.
    with pytest.raises(KeyError, match="nosuchcell"):
        emit_row("nosuchcell", image="x", run="y")


def test_a_feature_cell_emits_one_row_per_feature_and_no_profile_row() -> None:
    # The silent failure this guards: `mxfeatlaravel` runs the laravel
    # profile, so a row keyed by the profile would be applied to the
    # *profile* laravel row - replacing a run that verified that profile's
    # credentials, env step, sanitize rules and setup commands with one that
    # asserts almost none of it.
    rows = emit_row("mxfeatlaravel", image="ddev-sync:ci", run="r").splitlines()
    keys = [row.strip().strip("|").split("|")[0].strip() for row in rows]
    assert keys == ["node-build", "filament", "livewire", "inertia"]
    assert "laravel" not in keys
    assert all("ddev-sync:ci on php:8.3-cli" in row for row in rows)


def test_a_profile_cell_still_emits_exactly_one_row() -> None:
    assert emit_row("mxlaravel", image="i", run="r").count("\n") == 0


def test_apply_fills_only_the_matching_rows_evidence_cells(tmp_path: Path) -> None:
    verified = tmp_path / "VERIFIED.md"
    verified.write_text(
        "| Profile | Verified against | Date | By | Notes |\n"
        "|---|---|---|---|---|\n"
        "| laravel | | | | not yet verified. **Optional tables:** none |\n"
        "| symfony | | | | not yet verified |\n",
        encoding="utf-8",
    )
    apply_rows(verified, [("laravel", "laravel 12 on php 8.3", "2026-10-05", "run 42")])
    text = verified.read_text(encoding="utf-8")
    assert "| laravel | laravel 12 on php 8.3 | 2026-10-05 | run 42 |" in text
    # The note survives: it carries findings the matrix does not replace.
    assert "**Optional tables:** none" in text
    # The untouched row keeps its empty cells rather than inheriting anything.
    assert "| symfony | | | | not yet verified |" in text


def test_apply_refuses_a_row_that_matches_nothing_and_writes_nothing(tmp_path: Path) -> None:
    # The failure this guards is the silent one: a row keyed by the wrong label
    # (a cell name instead of a profile) matches no line, and an applier that
    # shrugged would report success having recorded nothing.
    verified = tmp_path / "VERIFIED.md"
    original = "| laravel | | | | not yet verified |\n"
    verified.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="mxlaravel"):
        apply_rows(
            verified,
            [("laravel", "x", "2026-10-05", "r"), ("mxlaravel", "x", "2026-10-05", "r")],
        )
    assert verified.read_text(encoding="utf-8") == original


def test_main_emits_a_row_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--emit", "mxtypo312", "--image", "img", "--run", "run-7"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| typo3@12 | img on php:8.2-cli | ")
    assert out.rstrip().endswith("| run-7 |")


def test_main_applies_the_rows_a_workflow_collected(tmp_path: Path) -> None:
    verified = tmp_path / "VERIFIED.md"
    verified.write_text("| laravel | | | | note |\n| symfony | | | | note |\n", encoding="utf-8")
    rows = tmp_path / "rows.md"
    # Prose and blank lines are what a copied job summary contains besides rows.
    rows.write_text(
        "### Emitted\n\n| laravel | evidence | 2026-10-05 | run 1 |\n\n", encoding="utf-8"
    )
    assert main(["--apply", str(rows), "--verified", str(verified)]) == 0
    text = verified.read_text(encoding="utf-8")
    assert "| laravel | evidence | 2026-10-05 | run 1 | note |" in text
    assert "| symfony | | | | note |" in text


def test_main_requires_a_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main([])
    assert raised.value.code == 2
    assert "--emit or --apply" in capsys.readouterr().err
