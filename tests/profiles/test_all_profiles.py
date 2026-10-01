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

# Sanitize rules whose table is optional - present only under some project
# configuration or optional package, not guaranteed by the framework's core -
# named here, by (profile, rule name), with why. `_run_sql` raises
# unconditionally and the executor stops at the first failed rule (spec §9),
# so a rule in this set MUST carry `tolerate_missing_table`: otherwise a
# project lacking that one optional table never reaches any rule after it,
# which is exactly how `clear-scheduler-tasks` (profiles/typo3.yaml),
# `horizon_jobs`/`horizon_supervisors` (features/horizon.yaml, removed) and
# this profile's own sessions/jobs/failed_jobs rules were each found to abort
# sanitize right after the database import, with real customer data and
# password hashes left on the laptop. This is the ratchet asked for by the
# final whole-branch review's Fix 4: it cannot mechanically prove a table is
# "optional" (that is a fact about the framework, not the YAML), but it DOES
# mechanically enforce that every table this review identified as optional
# carries the one flag that keeps a missing one from taking the rest of the
# rules down with it - a regression dropping the flag from any of these, or
# a profile adding a new optional-table rule without it, fails here.
_OPTIONAL_TABLE_RULES: dict[str, frozenset[str]] = {
    # `sessions`: only with the `database` session driver (this profile
    # defaults to `file`). `jobs`/`failed_jobs`: only if the queue:table /
    # queue:failed-table migrations were run - never true for a Redis- or
    # SQS-backed queue, e.g. a project using the `horizon` feature.
    "laravel": frozenset({"clear-sessions", "clear-jobs", "clear-failed-jobs"}),
    # EXT:scheduler (`typo3/cms-scheduler`) is a separate, non-default
    # Composer package; a project that never required it has no
    # `tx_scheduler_task` table. See the long comment in typo3.yaml.
    "typo3": frozenset({"clear-scheduler-tasks"}),
    # `watchdog` belongs to Database Logging (dblog), a core module that can
    # be disabled - and commonly is in production - in favour of `syslog`,
    # which needs no table at all. `sessions`: since Drupal 10.3, created
    # lazily by `Drupal\Core\Session\SessionHandler` on first write rather
    # than at install time (change record 3431286 / core issue #3428565); a
    # site using an alternative session handler (e.g. Redis) never gets the
    # table at all. See the comment in drupal.yaml.
    "drupal": frozenset({"clear-sessions", "clear-watchdog"}),
    # `messenger_messages` is created lazily on first use of the Doctrine
    # Messenger transport; a project using a different transport (AMQP,
    # Redis, sync) or `auto_setup: false` has no such table.
    "symfony": frozenset({"clear-messenger"}),
}


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
def test_no_variant_declares_a_duplicate_sanitize_rule_name(name: str, major: str | None) -> None:
    # Nothing rejects two rules sharing a name today - `_declares_anonymise_or_
    # reset_rule` and the dropped-rule check above both build a `set` of names,
    # which would silently absorb a duplicate rather than flag it. A caught
    # duplicate during this task (`clear-fe-sessions` appearing twice after an
    # edit mistake) was found only by a golden-file diff, not by any test - the
    # same family of silent failure this module exists to close everywhere
    # else, so it gets its own ratchet here too.
    rules = REGISTRY.resolve(name, major).data.get("sanitize", {}).get("rules", [])
    names = [str(rule.get("name", "")) for rule in rules]
    duplicates = sorted({entry for entry in names if names.count(entry) > 1})
    assert duplicates == [], (
        f"{name}@{major} declares sanitize rule name(s) {duplicates} more than once. "
        "A duplicate name is not a merge conflict and nothing raises - the rule simply "
        "runs twice, which hides a copy-paste mistake exactly like the one this check "
        "exists to catch."
    )


@pytest.mark.parametrize(("name", "major"), variants())
def test_rules_touching_an_optional_table_tolerate_its_absence(
    name: str, major: str | None
) -> None:
    # See `_OPTIONAL_TABLE_RULES` above for why this exists and what it can
    # and cannot prove.
    optional = _OPTIONAL_TABLE_RULES.get(name, frozenset())
    if not optional:
        return
    rules = {
        str(rule["name"]): rule
        for rule in REGISTRY.resolve(name, major).data.get("sanitize", {}).get("rules", [])
    }
    for rule_name in optional:
        rule = rules.get(rule_name)
        assert rule is not None, (
            f"{name}@{major}: _OPTIONAL_TABLE_RULES names a rule {rule_name!r} that this "
            "variant does not declare - update the allowlist or the profile."
        )
        assert rule.get("tolerate_missing_table") is True, (
            f"{name}@{major}: rule {rule_name!r} touches a table documented as optional "
            "(see _OPTIONAL_TABLE_RULES) but does not set tolerate_missing_table - a "
            "project missing that table would abort every sanitize rule after this one, "
            "with the database already imported."
        )


@pytest.mark.parametrize(("name", "major"), variants())
def test_every_sql_rule_declares_requires_table_or_tolerates_its_absence(
    name: str, major: str | None
) -> None:
    # Part 2 of the sanitize-table-safety fix: a `sql:` rule's table is a
    # fact only the author knows - this module cannot parse it out of raw
    # SQL, which is the whole point of declaring it instead. Forcing a choice
    # between the two flags here is what makes the preflight probe
    # (operations/preflight.py) able to check a table's existence before the
    # dump at all, and what stops a new sql rule from shipping with neither:
    # silently unverifiable by the probe, and silently intolerant of absence.
    rules = REGISTRY.resolve(name, major).data.get("sanitize", {}).get("rules", [])
    for rule in rules:
        if rule.get("sql") is None:
            continue
        declares = bool(rule.get("requires_table")) or bool(rule.get("tolerate_missing_table"))
        assert declares, (
            f"{name}@{major}: sql rule {rule.get('name')!r} declares neither "
            "'requires_table' nor 'tolerate_missing_table'. Add 'requires_table: "
            "<name>' if the engine must be able to verify the table exists, or "
            "'tolerate_missing_table: true' if it is allowed to be absent."
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
