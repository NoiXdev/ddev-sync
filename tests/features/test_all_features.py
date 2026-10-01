"""Conformance checks over every shipped feature file.

Mirrors `tests/profiles/test_all_profiles.py` in shape and in spirit: every
check here enumerates from the filesystem (`FeatureRegistry.available()`,
or an independent `glob` where even that enumeration itself needs checking -
see `test_every_yaml_file_in_features_is_enumerated_by_the_registry` below),
never from a hand-typed list of names, so a ninth feature inherits every one
of these checks the moment its file lands in `features/`.

The `applies_to` check is the one worth dwelling on: a typo'd profile name
(`laravell`, `typo3s`) makes a feature silently never activate - detection
and explicit requests both filter candidates against `applies_to` before
doing anything else (`features/resolver.py::_detected`, `::_from_request`),
so a typo there produces no error, just a feature that never fires. This
project has shipped that failure mode under different disguises three times
(see the dropped-sanitize-rule and anonymise-or-reset ratchets next door in
`test_all_profiles.py`), which is why it gets its own explicit assertion
rather than relying on `validate_feature` to catch it - `validate_feature`
only checks that `applies_to` is a list of strings, not that the strings
name anything real.
"""

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ddev_sync.features.detect import PREDICATES
from ddev_sync.features.registry import FeatureRegistry
from ddev_sync.operations import placeholders as placeholders_module
from ddev_sync.operations.sanitize import SANITIZE_OPERATIONS
from ddev_sync.profiles.registry import ProfileRegistry
from tests.test_no_legacy_terms import BANNED

FEATURE_ROOT = Path(__file__).parent.parent.parent / "features"
PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = FeatureRegistry(FEATURE_ROOT)
FEATURE_NAMES = REGISTRY.available()
PROFILE_NAMES = frozenset(ProfileRegistry(PROFILE_ROOT).available_profiles())

_PLACEHOLDER = re.compile(r"\$\{[^}]*\}")


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_feature_loads_and_validates(name: str) -> None:
    feature = REGISTRY.load(name)
    assert feature.name == name


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_feature_declares_a_name_matching_its_filename(name: str) -> None:
    # `FeatureRegistry.load` already raises on a mismatch (a feature is
    # activated by its filename), so this restates the invariant as its own
    # assertion rather than relying on an exception from a differently-named
    # test to make the point - mirrors the profile suite's equivalent check.
    raw = yaml.safe_load((FEATURE_ROOT / f"{name}.yaml").read_text(encoding="utf-8"))
    assert raw["name"] == name


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_no_feature_mentions_an_agency_specific_term(name: str) -> None:
    # Reuses the repo-wide scanner's own pattern rather than spelling the
    # banned terms out a second time here, exactly like the profile suite.
    text = str(REGISTRY.load(name))
    assert BANNED.search(text) is None


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_applies_to_entry_names_a_profile_that_exists(name: str) -> None:
    feature = REGISTRY.load(name)
    unknown = [profile for profile in feature.applies_to if profile not in PROFILE_NAMES]
    assert unknown == [], (
        f"{name} declares applies_to entry(ies) {unknown} that name no profile under "
        f"{PROFILE_ROOT}. A typo here makes the feature silently never activate - "
        "neither detection nor an explicit request will ever select it, and nothing "
        "raises to say so."
    )


def _detect_predicate_keys(predicate: Any) -> list[str]:
    """Every predicate key used anywhere in a (possibly nested) detect block."""
    if not isinstance(predicate, dict):
        return []
    keys: list[str] = list(predicate.keys())
    for key, value in predicate.items():
        if key in ("any_of", "all_of") and isinstance(value, list):
            for entry in value:
                keys.extend(_detect_predicate_keys(entry))
    return keys


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_detect_predicate_uses_the_closed_vocabulary(name: str) -> None:
    feature = REGISTRY.load(name)
    if not feature.detect:
        return
    unknown = [key for key in _detect_predicate_keys(feature.detect) if key not in PREDICATES]
    assert unknown == [], (
        f"{name} uses detect predicate(s) {unknown} outside the closed vocabulary "
        f"{sorted(PREDICATES)} declared in features/detect.py."
    )


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_setting_has_a_non_empty_description(name: str) -> None:
    feature = REGISTRY.load(name)
    undocumented = [key for key, spec in feature.settings.items() if not spec.description]
    assert undocumented == [], (
        f"{name} declares setting(s) {undocumented} with no description; a setting "
        "without one is a knob a user cannot learn the meaning of short of reading "
        "the feature's own YAML."
    )


def _rule_operations(data: dict[str, Any]) -> list[str]:
    rules = data.get("sanitize", {}).get("rules", [])
    return [str(rule["operation"]) for rule in rules if rule.get("operation") is not None]


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_sanitize_operation_is_in_the_closed_catalog(name: str) -> None:
    # `validate_feature` already enforces this at load time by delegating to
    # `validate_profile` (same catalog, same error), so this is a second,
    # independent assertion of the same property rather than the only one -
    # it is cheap and it means this fact stays checked even if that delegation
    # is ever refactored away.
    feature = REGISTRY.load(name)
    unknown = [op for op in _rule_operations(feature.data) if op not in SANITIZE_OPERATIONS]
    assert unknown == [], (
        f"{name} uses sanitize operation(s) {unknown} outside the closed catalog "
        f"{sorted(SANITIZE_OPERATIONS)}."
    )


def _placeholders_in(value: Any) -> set[str]:
    """Every `${...}` token appearing anywhere in a parsed YAML structure."""
    if isinstance(value, str):
        return set(_PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found |= _placeholders_in(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _placeholders_in(item)
        return found
    return set()


def _supported_operation_placeholders() -> frozenset[str]:
    """The `${...}` names `operations/placeholders.py` actually substitutes.

    Read directly from that module's own source (`_substitutions`'s literal
    keys) rather than retyped here, so a placeholder added there is picked
    up automatically instead of needing this file to be kept in sync by hand.
    """
    source = inspect.getsource(placeholders_module._substitutions)
    return frozenset(_PLACEHOLDER.findall(source))


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_placeholder_resolves_to_a_known_name(name: str) -> None:
    # Runtime behaviour is unchanged by this test: `operations/placeholders.py`
    # leaves an unknown `${...}` alone, which is correct for user config - a
    # project is free to write whatever it wants. But a shipped feature file
    # using `${scrpit}` instead of `${script}` would pass that silently all
    # the way through to a shell command, exactly like a typo that no part of
    # the system ever flags, so the *shipped* files get the stricter check
    # a user's own config does not.
    feature = REGISTRY.load(name)
    raw = yaml.safe_load((FEATURE_ROOT / f"{name}.yaml").read_text(encoding="utf-8"))
    known = (
        {f"${{{key}}}" for key in feature.settings}
        | {"${PM}", "${PM_RUN}"}
        | _supported_operation_placeholders()
    )
    used = _placeholders_in(raw)
    unknown = used - known
    assert unknown == set(), (
        f"{name} uses placeholder(s) {sorted(unknown)} that resolve to nothing: not a "
        f"setting it declares ({sorted(feature.settings)}), not ${{PM}}/${{PM_RUN}}, and "
        "not one operations/placeholders.py supports. This is exactly how a typo like "
        "${scrpit} would land as a literal string in a shell command."
    )


def test_every_yaml_file_in_features_is_enumerated_by_the_registry() -> None:
    """The structural ratchet, mirroring `tests/engine/test_reachability.py`.

    `FeatureRegistry.available()` filters every `*.yaml` file through a
    lowercase-kebab-case filename pattern before returning it - a file that
    fails that pattern (a stray capital letter, a space, a leftover `.bak`)
    is silently dropped, and every check above is parametrized from exactly
    that same `available()` list, so such a file would never be validated by
    anything in this module either. Listing the directory independently
    here, rather than trusting `available()` a second time, is what actually
    closes that gap - `available()` checking itself would not catch
    `available()` being wrong.
    """
    on_disk = {path.stem for path in FEATURE_ROOT.iterdir() if path.suffix == ".yaml"}
    enumerated = set(FEATURE_NAMES)
    missing = on_disk - enumerated
    assert missing == set(), (
        f"features/ holds file(s) {sorted(missing)} that FeatureRegistry.available() "
        "does not enumerate - likely a filename outside the lowercase-kebab-case "
        "pattern it expects - so no conformance test in this module ever runs "
        "against them."
    )


def test_every_registered_feature_has_a_file() -> None:
    """The other half of the reachability pair: trivially true by how
    `available()` is built (it only ever returns names it found a file for),
    but asserted anyway so the property is pinned rather than merely implied
    by reading the implementation - the same reasoning
    `test_reachability.py::test_allowed_unregistered_entries_are_still_real_classes`
    uses for guarding its own allowlist.
    """
    for name in FEATURE_NAMES:
        assert (FEATURE_ROOT / f"{name}.yaml").is_file()
