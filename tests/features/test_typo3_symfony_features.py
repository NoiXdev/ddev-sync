import re
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.features.registry import FeatureRegistry
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.exec import ExecOperation
from ddev_sync.transport.fake import FakeCommandRunner

FEATURE_ROOT = Path(__file__).parent.parent.parent / "features"
REGISTRY = FeatureRegistry(FEATURE_ROOT)
VERIFIED_MD = Path(__file__).parent.parent.parent / "profiles" / "VERIFIED.md"

# Matches the table name following TRUNCATE TABLE / UPDATE / FROM in a raw
# sanitize rule's `sql`, plus the explicit `table:` key an operation-based
# rule (e.g. reset_passwords) carries instead.
_SQL_TABLE = re.compile(r"(?:TABLE|UPDATE|FROM)\s+`?(\w+)`?", re.IGNORECASE)


def _tables_touched(rule: dict[str, Any]) -> set[str]:
    tables: set[str] = set()
    table = rule.get("table")
    if table:
        tables.add(str(table))
    sql = rule.get("sql")
    if sql:
        tables.update(_SQL_TABLE.findall(str(sql)))
    return tables


def test_processedfiles_has_no_detect_block_and_is_explicit_only() -> None:
    assert REGISTRY.load("typo3-processedfiles").detect == {}


def test_processedfiles_is_scoped_to_typo3() -> None:
    assert REGISTRY.load("typo3-processedfiles").applies_to == ("typo3",)


def test_processedfiles_truncates_the_processed_file_table() -> None:
    rules = REGISTRY.load("typo3-processedfiles").data["sanitize"]["rules"]
    sql_by_name = {rule["name"]: rule["sql"] for rule in rules}
    assert sql_by_name["clear-processed-files"] == "TRUNCATE TABLE sys_file_processedfile"


def _processedfiles_entry() -> dict[str, Any]:
    return next(
        e
        for e in REGISTRY.load("typo3-processedfiles").data["setup"]["commands"]
        if "rm -rf" in str(e.get("run", ""))
    )


def _processedfiles_context(
    tmp_path: Path, runner: FakeCommandRunner, docroot: str
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot=docroot, root=tmp_path),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", {})]),
        runner=runner,
        downloads_dir=tmp_path / ".ddev" / ".downloads",
    )


def test_the_destructive_command_has_no_separate_workdir() -> None:
    assert "workdir" not in _processedfiles_entry()


def test_the_destructive_command_path_is_derived_from_the_docroot(tmp_path: Path) -> None:
    # A project whose docroot is not "public/" (the historical hardcoded
    # value) must still have its *actual* processed-files directory removed,
    # not some other project's directory that happens to share the name
    # "public". ${DDEV_DOCROOT} (operations/placeholders.py) is resolved
    # from the real `.ddev/config.yaml` at run time; this only pins that the
    # feature asks for it rather than a literal "public/...".
    entry = _processedfiles_entry()
    assert "${DDEV_DOCROOT}/fileadmin/_processed_" in str(entry["run"])
    assert "public/" not in str(entry["run"])


def test_the_resolved_destructive_command_targets_a_non_default_docroot_safely(
    tmp_path: Path,
) -> None:
    """End-to-end: the feature's command, run through the same
    `run_command_entry` the executor uses, targets a project's real docroot
    - not "public/" - once `${DDEV_DOCROOT}` is expanded against an
    `ExecutionContext`. Proves the feature file and the placeholder's
    resolution agree, rather than asserting each in isolation.

    Also the only place that can actually guarantee the *resolved* command
    is relative and contains no `..`: an earlier version of this guarantee
    inspected the unexpanded "${DDEV_DOCROOT}/..." template, which is the
    same literal string regardless of what the placeholder resolves to and
    so proved nothing. See `test_a_hostile_docroot_is_rejected_rather_than_
    reaching_the_shell` for the case where resolution itself must refuse.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(["sh"], stdout="")
    context = _processedfiles_context(tmp_path, runner, "httpdocs")
    ExecOperation().execute(context, _processedfiles_entry())
    command = runner.calls[0][2]
    assert command == "rm -rf httpdocs/fileadmin/_processed_"
    assert not command.split("rm -rf ")[1].startswith("/")
    assert ".." not in command


def test_a_hostile_docroot_is_rejected_rather_than_reaching_the_shell(tmp_path: Path) -> None:
    # `DdevProject.docroot` is read verbatim from `.ddev/config.yaml` and
    # never normalised - a docroot of "../../etc" would otherwise turn this
    # command into "rm -rf ../../etc/fileadmin/_processed_", run with cwd at
    # the project root. ${DDEV_DOCROOT}'s own validation
    # (operations/placeholders.py::_validated_docroot) must catch this
    # before the runner ever sees it - asserting `runner.calls == []` is
    # what proves the rejection happens before dispatch, not after.
    runner = FakeCommandRunner()
    context = _processedfiles_context(tmp_path, runner, "../../etc")
    with pytest.raises(OperationError) as caught:
        ExecOperation().execute(context, _processedfiles_entry())
    assert "docroot" in str(caught.value)
    assert runner.calls == []


def test_a_word_splitting_docroot_is_rejected_rather_than_reaching_the_shell(
    tmp_path: Path,
) -> None:
    # The command runs through `sh -c`, where a docroot containing a space
    # splits into two operands: "rm -rf web root/fileadmin/_processed_" would
    # delete the project's entire `web` directory before touching anything
    # named `_processed_`. Path traversal is not the only way this value can
    # escape its intended target, so the rejection must cover the shell's own
    # re-interpretation too - again before the runner sees anything.
    runner = FakeCommandRunner()
    context = _processedfiles_context(tmp_path, runner, "web root")
    with pytest.raises(OperationError) as caught:
        ExecOperation().execute(context, _processedfiles_entry())
    assert "docroot" in str(caught.value)
    assert runner.calls == []


def test_the_destructive_command_does_not_set_allow_failure() -> None:
    # `rm -rf` already exits 0 on a missing path (the fresh-checkout case,
    # before the first import, that `allow_failure` used to be justified
    # by) - so `allow_failure` here was never protecting against a missing
    # directory, only silently swallowing whatever OTHER failure occurred
    # (permission denied, a read-only filesystem, ...) with nothing ever
    # reported. Removing it makes a genuine failure here visible again,
    # same as every other command this profile/feature set runs.
    assert "allow_failure" not in _processedfiles_entry()


def test_no_feature_other_than_processedfiles_removes_anything() -> None:
    for name in REGISTRY.available():
        for entry in REGISTRY.load(name).data.get("setup", {}).get("commands", []):
            run = str(entry.get("run", ""))
            if name == "typo3-processedfiles":
                continue
            assert "rm " not in run and "rmdir" not in run, f"{name}: {run}"


def test_typo3_solr_is_scoped_to_typo3_and_requeues_the_index() -> None:
    feature = REGISTRY.load("typo3-solr")
    assert feature.applies_to == ("typo3",)
    commands = [str(e["run"]) for e in feature.data["setup"]["commands"]]
    assert any("solr" in command for command in commands)


def test_symfony_search_is_scoped_to_symfony_and_repopulates_elastica() -> None:
    feature = REGISTRY.load("symfony-search")
    assert feature.applies_to == ("symfony",)
    commands = [str(e["run"]) for e in feature.data["setup"]["commands"]]
    assert any("fos:elastica:populate" in command for command in commands)


def test_typo3_and_symfony_optional_commands_tolerate_failure() -> None:
    # Both are post-import conveniences, not correctness requirements: a
    # Solr or Elastica reindex that fails must not abort a sync whose
    # database import already succeeded.
    for name in ("typo3-solr", "symfony-search"):
        for entry in REGISTRY.load(name).data["setup"]["commands"]:
            assert entry.get("allow_failure") is True, entry


def test_composer_package_detections_are_pinned() -> None:
    # A typo in a detect value fails silently: the feature simply never
    # activates, and nothing reports the mismatch. Pinning the exact string
    # here makes any future change to one a deliberate edit, not a slip.
    assert REGISTRY.load("typo3-solr").detect == {"composer_package": "apache-solr-for-typo3/solr"}
    assert REGISTRY.load("symfony-search").detect == {
        "composer_package": "friendsofsymfony/elastica-bundle"
    }


def test_features_with_sanitize_rules_name_their_tables_in_verified_md() -> None:
    # A TRUNCATE against a table that does not exist aborts the whole sync
    # (operations/sanitize.py::_run_sql raises unconditionally on failure,
    # with no allow_failure available on that path) - that is exactly how
    # horizon.yaml's now-removed sanitize block would have failed on a real
    # Horizon install. This cannot prove a table is real without running
    # against one, but it can force every claim about one into the one file
    # whose job is tracking exactly that, where it is visible to whoever
    # eventually verifies it.
    verified_text = VERIFIED_MD.read_text(encoding="utf-8")
    for name in REGISTRY.available():
        rules = REGISTRY.load(name).data.get("sanitize", {}).get("rules", [])
        if not rules:
            continue
        touched: set[str] = set()
        for rule in rules:
            touched |= _tables_touched(rule)
        assert touched, f"{name}: has sanitize rules but no table name could be extracted"
        row = next(
            (line for line in verified_text.splitlines() if line.startswith(f"| {name} |")),
            None,
        )
        assert row is not None, f"{name}: no profiles/VERIFIED.md row found"
        for table in touched:
            assert table in row, (
                f"{name}: profiles/VERIFIED.md row does not name the table {table!r} "
                f"it truncates:\n{row}"
            )
