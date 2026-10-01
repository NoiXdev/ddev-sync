from pathlib import Path
from typing import Any

import pytest

from ddev_sync.profiles.registry import ProfileRegistry
from tests.test_no_legacy_terms import BANNED

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"
REGISTRY = ProfileRegistry(PROFILE_ROOT)
PROFILE_NAMES = REGISTRY.available_profiles()

# Profiles that genuinely have no user table to protect, so declaring zero
# anonymise-or-reset sanitize rules is correct rather than an oversight.
# Adding an entry here is a deliberate act - say why.
_NO_USER_DATA: frozenset[str] = frozenset(
    {
        # The fallback profile for a DDEV type with no dedicated profile: it
        # assumes no framework-specific schema at all (empty sanitize.rules
        # by design), so there is no table to name here.
        "generic",
    }
)


def _declares_anonymise_or_reset_rule(rules: list[dict[str, Any]]) -> bool:
    for rule in rules:
        name = str(rule.get("name", ""))
        if "anonymize" in name or "anonymise" in name:
            return True
        if rule.get("operation") == "reset_passwords":
            return True
    return False


def variants() -> list[tuple[str, str | None]]:
    combinations: list[tuple[str, str | None]] = []
    for name in PROFILE_NAMES:
        combinations.append((name, None))
        combinations.extend((name, str(major)) for major in REGISTRY.available_majors(name))
    return combinations


@pytest.mark.parametrize(("name", "major"), variants())
def test_every_profile_variant_loads_and_validates(name: str, major: str | None) -> None:
    resolved = REGISTRY.resolve(name, major)
    assert resolved.data["name"] == name


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_every_profile_declares_a_name_matching_its_filename(name: str) -> None:
    assert REGISTRY.resolve(name, None).data["name"] == name


@pytest.mark.parametrize(("name", "major"), variants())
def test_no_profile_mentions_an_agency_specific_term(name: str, major: str | None) -> None:
    # Reuses the repo-wide scanner's own pattern rather than spelling the
    # banned terms out a second time here: a literal copy of them would trip
    # that very scanner, since it only exempts its own file by name.
    text = str(REGISTRY.resolve(name, major).data)
    assert BANNED.search(text) is None


@pytest.mark.parametrize(("name", "major"), variants())
def test_every_local_files_path_is_relative(name: str, major: str | None) -> None:
    paths = REGISTRY.resolve(name, major).data.get("files", {}).get("paths", [])
    for entry in paths:
        assert not str(entry["local"]).startswith("/")
        assert ".." not in str(entry["local"])


@pytest.mark.parametrize(("name", "major"), variants())
def test_a_variant_never_silently_drops_a_sanitize_rule_the_base_declares(
    name: str, major: str | None
) -> None:
    # A variant that uses `mode: replace` on `sanitize.rules` (spec §4.5)
    # fully discards the base list rather than merging into it - so a rule
    # the base declares (e.g. resetting real passwords before the dump ever
    # leaves the sanitize step) can vanish from a variant by omission alone,
    # with nothing raising and nothing in the plan hinting it is missing.
    # This caught exactly that for typo3@9/@10's `reset-fe-passwords` rule.
    base_rules = {
        rule["name"]
        for rule in REGISTRY.resolve(name, None).data.get("sanitize", {}).get("rules", [])
    }
    variant_rules = {
        rule["name"]
        for rule in REGISTRY.resolve(name, major).data.get("sanitize", {}).get("rules", [])
    }
    dropped = base_rules - variant_rules
    assert dropped == set(), (
        f"{name}@{major} drops sanitize rule(s) {sorted(dropped)} that {name} declares. "
        "If that is deliberate, the variant needs a comment saying why - a silently "
        "missing sanitize rule means production data stays on a laptop."
    )


@pytest.mark.parametrize(("name", "major"), variants())
def test_every_profile_declares_an_anonymise_or_reset_rule_unless_opted_out(
    name: str, major: str | None
) -> None:
    # Measured directly: typo3 anonymised fe_users but left be_users
    # untouched; symfony and drupal reset no passwords at all; shopware6
    # anonymised nothing - a sync landed every customer record and password
    # hash unmodified while reporting "sanitize OK". This does not check
    # *which* table or column - that is what VERIFIED.md and the instance
    # rules above are for - only that a profile cannot ship with zero such
    # rules by omission.
    if name in _NO_USER_DATA:
        return
    rules_list = REGISTRY.resolve(name, major).data.get("sanitize", {}).get("rules", [])
    assert _declares_anonymise_or_reset_rule(rules_list), (
        f"{name}@{major} declares no anonymise-or-reset sanitize rule, so a sync would "
        "land production user data (emails, password hashes) on a laptop unmodified. "
        "If this profile genuinely has no user table, add it to _NO_USER_DATA here with "
        "a comment saying why."
    )
