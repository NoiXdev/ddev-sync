"""Turn a green matrix cell into a `profiles/VERIFIED.md` row.

Two modes, because the evidence and the file live in different places: a
workflow `--emit`s a row into its job summary, and a human `--apply`s the
collected rows locally and commits. CI does not write this file - that would
need push access to `main` and would break the fast-forward-only convention
for no gain.

Run it as a module from the repository root (`python -m
tools.apply_verified_rows`), not as `python tools/apply_verified_rows.py`: the
registry lives under `tests/`, and a script run by path puts `tools/` on the
import path instead of the repository root, so the import below would fail.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from tests.matrix.cells import CELLS

_BY_NAME = {cell.name: cell for cell in CELLS}


def emit_row(name: str, *, image: str, run: str) -> str:
    """The Markdown row or rows recording that `name`'s cell passed."""
    if name not in _BY_NAME:
        raise KeyError(f"{name} is not a cell in the matrix registry")
    cell = _BY_NAME[name]
    today = datetime.now(UTC).date().isoformat()
    # A profile cell fills one row, keyed by its **profile** label, not its
    # own name (`typo3@13`, while the cell is `mxtypo313`): the cell name is
    # the input, the profile is the output, and swapping them fills no rows
    # and fails silently - hence the test using the one pair where the two
    # strings differ.
    #
    # A feature cell fills one row per feature it checks, keyed by the
    # feature name, and *no* profile row. `mxfeatlaravel` runs the laravel
    # profile, so a profile-keyed row would be applied to the profile
    # `laravel` row and replace a run that verified that profile's
    # credentials, env step, sanitize rules and setup commands with one that
    # asserts almost none of it.
    #
    # A cell that verifies a *transport* fills nothing. It runs some profile's
    # commands to have something real to sync and asks nothing about them, so
    # a profile-keyed row would overwrite the evidence of the cell that did
    # ask - the same trap as above, one step further. Said out loud rather
    # than returned empty, because a workflow puts this straight into a job
    # summary and silence there reads as a broken step.
    if not cell.verified_row:
        return (
            f"# {cell.name} verifies the {cell.profile} profile's commands only "
            "incidentally; no profiles/VERIFIED.md row"
        )
    keys = [check.feature for check in cell.features] or [cell.profile]
    return "\n".join(f"| {key} | {image} on {cell.php_image} | {today} | {run} |" for key in keys)


def apply_rows(verified: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Fill the evidence cells of the rows named in `rows`.

    Only *Verified against*, *Date* and *By* are replaced. The *Notes* column
    carries reasoning the matrix does not produce - optional tables, upstream
    quirks, findings - and overwriting it would discard what this file exists
    for.

    A row that matches no line is an error, and nothing is written: the
    alternative is a swapped key or a mistyped profile recording nothing while
    the command reports success.
    """
    lines = verified.read_text(encoding="utf-8").splitlines()
    wanted = {profile: (against, date, by) for profile, against, date, by in rows}
    matched: set[str] = set()
    for index, line in enumerate(lines):
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] not in wanted:
            continue
        against, date, by = wanted[cells[0]]
        lines[index] = f"| {cells[0]} | {against} | {date} | {by} | {cells[4]} |"
        matched.add(cells[0])
    unmatched = sorted(set(wanted) - matched)
    if unmatched:
        raise ValueError(f"no row in {verified} is named {', '.join(unmatched)}")
    verified.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Emit one row, or apply a file of rows."""
    parser = argparse.ArgumentParser(description="Record what the matrix verified.")
    parser.add_argument("--emit", metavar="CELL")
    parser.add_argument("--image", default="")
    parser.add_argument("--run", default="")
    parser.add_argument("--apply", metavar="ROWS_FILE")
    parser.add_argument("--verified", default="profiles/VERIFIED.md")
    args = parser.parse_args(argv)
    if args.emit:
        print(emit_row(args.emit, image=args.image, run=args.run))
        return 0
    if args.apply:
        rows: list[tuple[str, str, str, str]] = []
        for line in Path(args.apply).read_text(encoding="utf-8").splitlines():
            if not line.startswith("| "):
                continue
            parts = [part.strip() for part in line.strip().strip("|").split("|")]
            if len(parts) == 4:
                rows.append((parts[0], parts[1], parts[2], parts[3]))
        apply_rows(Path(args.verified), rows)
        return 0
    parser.error("one of --emit or --apply is required")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
