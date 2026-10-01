from pathlib import Path

from ddev_sync.features.registry import FeatureRegistry

FEATURE_ROOT = Path(__file__).parent.parent.parent / "features"
REGISTRY = FeatureRegistry(FEATURE_ROOT)


def test_processedfiles_has_no_detect_block_and_is_explicit_only() -> None:
    assert REGISTRY.load("typo3-processedfiles").detect == {}


def test_processedfiles_is_scoped_to_typo3() -> None:
    assert REGISTRY.load("typo3-processedfiles").applies_to == ("typo3",)


def test_processedfiles_truncates_the_processed_file_table() -> None:
    rules = REGISTRY.load("typo3-processedfiles").data["sanitize"]["rules"]
    sql_by_name = {rule["name"]: rule["sql"] for rule in rules}
    assert sql_by_name["clear-processed-files"] == "TRUNCATE TABLE sys_file_processedfile"


def test_the_only_destructive_filesystem_command_is_relative_and_unredirected() -> None:
    entry = next(
        e
        for e in REGISTRY.load("typo3-processedfiles").data["setup"]["commands"]
        if "rm -rf" in str(e.get("run", ""))
    )
    assert not str(entry["run"]).split("rm -rf ")[1].startswith("/")
    assert ".." not in str(entry["run"])
    assert "workdir" not in entry


def test_the_destructive_command_tolerates_failure() -> None:
    # The directory may already be absent (a fresh checkout before the first
    # import); that must not abort a sync whose database import succeeded.
    entry = next(
        e
        for e in REGISTRY.load("typo3-processedfiles").data["setup"]["commands"]
        if "rm -rf" in str(e.get("run", ""))
    )
    assert entry.get("allow_failure") is True


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
