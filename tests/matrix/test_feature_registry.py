"""What is worth knowing about the cell registry before any cell runs.

Unmarked on purpose, so these run in the ordinary unit suite: a mistyped
feature name would otherwise cost a three-minute cell run to discover, or
worse, silently assert about a feature that does not exist, and a name
breaking the rules stated above `Cell.name` would cost a CI run that
quietly covered two cells in one job. The `matrix` marker is applied by
`pytestmark` in `test_matrix.py` alone and `tests/matrix/conftest.py`
declares no autouse fixtures, so nothing here brings a stack up.
"""

import itertools
import re
from typing import Any

import yaml

from ddev_sync.features.registry import DEFAULT_FEATURE_ROOT, FeatureRegistry
from ddev_sync.profiles.registry import DEFAULT_PROFILE_ROOT, ProfileRegistry
from tests.matrix.cells import CELLS
from tests.matrix.paths import HERE, cell_dir

# The charset stated above `Cell.name`: the `mx` prefix, then letters and
# digits, which is what excludes the hyphen `-k` cannot match and the
# underscore a network alias cannot carry.
CELL_NAME = re.compile(r"mx[A-Za-z0-9]+")


def test_every_feature_check_names_a_feature_that_exists() -> None:
    known = set(FeatureRegistry(DEFAULT_FEATURE_ROOT).available())
    # The registry read must not be the thing that is empty: an empty `known`
    # would make the loop below assert nothing at all.
    assert "node-build" in known, f"the feature directory was not read: {sorted(known)}"
    for cell in CELLS:
        for check in cell.features:
            assert check.feature in known, (
                f"{cell.name} declares a check for feature {check.feature!r}, "
                f"which is not one of {sorted(known)}"
            )


# Each of the three loops below asserts nothing against a collection too small
# to iterate, so each states the size it needs first - the same reason the
# feature check above refuses an empty `known`. The comment used to say "both
# loops" and was left behind when a third was added, which is how the service
# guard came to be the one without a minimum.
def test_every_cell_name_is_mx_followed_by_letters_and_digits_only() -> None:
    assert CELLS, "the cell registry is empty, so the loop below checks nothing"
    for cell in CELLS:
        assert CELL_NAME.fullmatch(cell.name), (
            f"cell name {cell.name!r} is not the `mx` prefix followed by "
            "letters and digits; see the charset rule above `Cell.name` for "
            "what a bare name, a hyphen and an underscore each break"
        )


def test_no_cell_name_contains_another_so_a_k_filter_selects_one_cell() -> None:
    assert len(CELLS) > 1, "fewer than two cells, so there is no pair to compare"
    for cell, other in itertools.permutations(CELLS, 2):
        assert other.name not in cell.name, (
            f"cell name {cell.name!r} contains cell name {other.name!r}, so the "
            f"`-k {other.name}` job in .github/workflows/matrix.yaml would "
            f"run {cell.name}'s tests too and still emit only {other.name}'s row"
        )


def test_every_declared_service_is_a_compose_profile() -> None:
    """A mistyped service name must fail this test, not a cell run.

    `compose --profile nosuch up` starts the four default services and says
    nothing about the name it did not recognise, so the cell would come up
    without the service it asked for and fail somewhere downstream.

    This checks that the name appears as a list item in the file, not that it
    sits under the service of that name.
    """
    compose = (HERE / "docker-compose.yaml").read_text(encoding="utf-8")
    declared = {name for cell in CELLS for name in cell.services}
    assert declared, "no cell declares a service, so the loop below checks nothing"
    for name in sorted(declared):
        assert f"- {name}\n" in compose, (
            f"cell service {name!r} is not a compose profile in docker-compose.yaml; "
            "each service's `profiles:` entry carries the service's own name"
        )


def test_some_cell_declares_env_markers_of_each_kind() -> None:
    # Against a registry where every `env_markers` is empty, the assertion in
    # `test_matrix.py` skips for every cell and proves nothing. Each kind is
    # required separately because they are four different promises: imposed,
    # kept, defaulted, and overridden by the project.
    kinds = {marker.kind for cell in CELLS for marker in cell.env_markers}
    assert kinds == {"imposed", "kept", "defaulted", "overridden"}, (
        f"only {sorted(kinds)} are asserted anywhere; the env step's other promises "
        "have no cell behind them"
    )


def _recipe_values(cell_name: str, key: str) -> list[str]:
    """Every value the cell's `install.sh` assigns to `key` at the start of a line."""
    text = (cell_dir(cell_name) / "install.sh").read_text(encoding="utf-8")
    return re.findall(rf"^{re.escape(key)}=(.*)$", text, flags=re.MULTILINE)


def test_every_env_marker_is_consistent_with_its_profile_and_its_recipe() -> None:
    # The run cannot show some of what a marker claims, so this checks it
    # statically. For one key in one run a `defaulted` key and an `imposed`
    # key write the same line, so only the profile can say which list the key
    # is in; and an `imposed` marker is a measured overwrite only while the
    # recipe still declares the contrary value, which deleting one line from
    # `install.sh` would otherwise undo with every test still green.
    profiles = ProfileRegistry(DEFAULT_PROFILE_ROOT)
    checked = 0
    for cell in CELLS:
        if not cell.env_markers:
            continue
        env = profiles.resolve(cell.profile, None).data["env"]
        imposed, defaulted = env.get("set", {}), env.get("default", {})
        project = yaml.safe_load((cell_dir(cell.name) / "ddev-sync.yaml").read_text("utf-8")) or {}
        project_set: dict[str, Any] = (project.get("env") or {}).get("set") or {}
        for marker in cell.env_markers:
            checked += 1
            where = f"{cell.name}: {marker.kind} marker {marker.key}"
            written = _recipe_values(cell.name, marker.key)
            if marker.kind == "imposed":
                assert marker.key in imposed, f"{where} is not in {cell.profile}'s env.set"
                assert marker.key not in project_set, (
                    f"{where} is also in the project's env.set, so its value is the project's: "
                    "that is an `overridden` marker"
                )
            if marker.kind == "defaulted":
                assert marker.key in defaulted, f"{where} is not in {cell.profile}'s env.default"
                assert marker.key not in imposed, (
                    f"{where} is in env.set too, so the run cannot tell the two apart"
                )
                assert not written, f"{where} is declared by the recipe, so it is not defaulted"
            if marker.kind == "kept":
                assert marker.key in defaulted and marker.key not in imposed, (
                    f"{where} must be in env.default and not in env.set, or keeping proves nothing"
                )
                assert written == [marker.expected], (
                    f"{where}: the recipe must write {marker.expected!r}, found {written}"
                )
            if marker.kind == "overridden":
                # A merge test, not a spelling test: the key has to be in both
                # layers with different values, or the project's value
                # winning shows nothing about how the layers combine.
                assert marker.key in imposed, f"{where} is not in {cell.profile}'s env.set"
                assert marker.key in project_set, (
                    f"{where} is not in the cell's own ddev-sync.yaml env.set"
                )
                assert str(project_set[marker.key]) == marker.expected, (
                    f"{where}: the project writes {project_set[marker.key]!r}, "
                    f"the marker expects {marker.expected!r}"
                )
                assert str(imposed[marker.key]) != marker.expected, (
                    f"{where}: the profile imposes the same value, so nothing is overridden"
                )
                # Alone, an overridden key also passes when the profile has
                # stopped writing it: the project's would be the only value
                # left and would still read back. The other half of the
                # evidence is an imposed key that landed in the same file -
                # and it has to be one the recipe does not declare. An imposed
                # key the recipe writes with the same value is no control at
                # all, which `cells.py` says of `DB_HOST` and `REDIS_HOST` in
                # as many words: a step that never ran would leave those
                # reading correctly, so would a step that kept running and
                # stopped writing the overridden key.
                assert any(
                    other.kind == "imposed"
                    and other.key in imposed
                    and not _recipe_values(cell.name, other.key)
                    for other in cell.env_markers
                ), (
                    f"{cell.name} declares an overridden marker with no imposed marker beside it "
                    "whose key the recipe leaves undeclared; an imposed key the recipe writes too "
                    "would read correctly even from a step that had stopped writing this one"
                )
            if marker.contrary is not None:
                assert marker.kind == "imposed", f"{where} has a contrary value but is not imposed"
                assert marker.contrary != marker.expected, f"{where}: contrary equals expected"
                assert written == [marker.contrary], (
                    f"{where}: install.sh must write {marker.contrary!r} before the sync, "
                    f"found {written}; without it the marker no longer measures an overwrite"
                )
    assert checked, "no cell declares an env marker, so the loop above checked nothing"
