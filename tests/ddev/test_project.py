from pathlib import Path

import pytest

from ddev_sync.ddev.project import DdevProject, read_ddev_project
from ddev_sync.errors import ConfigError


def write_ddev_config(root: Path, text: str) -> None:
    (root / ".ddev").mkdir(parents=True, exist_ok=True)
    (root / ".ddev" / "config.yaml").write_text(text, encoding="utf-8")


def test_reads_name_type_and_docroot(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "name: acme-website\ntype: typo3\ndocroot: public\n")
    project = read_ddev_project(tmp_path)
    assert project == DdevProject(
        name="acme-website", type="typo3", docroot="public", root=tmp_path
    )


def test_db_container_follows_ddev_naming() -> None:
    project = DdevProject(name="acme-website", type="typo3", docroot="public", root=Path("/x"))
    assert project.db_container == "ddev-acme-website-db"


def test_docroot_path_joins_the_project_root(tmp_path: Path) -> None:
    project = DdevProject(name="a", type="php", docroot="public", root=tmp_path)
    assert project.docroot_path == tmp_path / "public"


def test_an_empty_docroot_means_the_project_root_itself(tmp_path: Path) -> None:
    project = DdevProject(name="a", type="php", docroot="", root=tmp_path)
    assert project.docroot_path == tmp_path


def test_a_missing_docroot_key_defaults_to_empty(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "name: acme\ntype: php\n")
    assert read_ddev_project(tmp_path).docroot == ""


def test_a_missing_config_file_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    message = str(caught.value)
    assert ".ddev/config.yaml" in message
    assert "DDEV project" in message


@pytest.mark.parametrize("missing", ["name", "type"])
def test_a_config_without_name_or_type_is_rejected(tmp_path: Path, missing: str) -> None:
    fields = {"name": "acme", "type": "php"}
    del fields[missing]
    write_ddev_config(tmp_path, "".join(f"{k}: {v}\n" for k, v in fields.items()))
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    assert missing in str(caught.value)


def test_unparseable_ddev_config_is_reported(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "name: [unclosed\n")
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    assert ".ddev/config.yaml" in str(caught.value)


def test_a_non_mapping_ddev_config_is_rejected(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "- one\n")
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    assert "mapping" in str(caught.value)
