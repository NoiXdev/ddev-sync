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
        name="acme-website", type="typo3", docroot="public", root=tmp_path.resolve()
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


# `docroot:` with nothing after it parses to None, and `str(None)` is the
# string "None": the project would have looked for its web root in a
# directory called None. The same shape in `name:` was worse - the database
# container is named after it, so every local database call would have gone
# to `ddev-None-db`.
@pytest.mark.parametrize("key", ["name", "type"])
def test_a_key_written_with_no_value_counts_as_missing(tmp_path: Path, key: str) -> None:
    fields = {"name": "acme", "type": "php"}
    fields[key] = ""
    write_ddev_config(tmp_path, "".join(f"{k}: {v}\n" for k, v in fields.items()))
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    assert key in str(caught.value)


def test_a_docroot_written_with_no_value_is_the_project_root(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "name: acme\ntype: php\ndocroot:\n")
    project = read_ddev_project(tmp_path)
    assert project.docroot == ""
    assert project.docroot_path == tmp_path.resolve()


# An absolute docroot and one that climbs out with `..` are the two ways
# `root / docroot` stops being inside `root`. Both reach further than the web
# root itself: `transport/container.py` maps a workdir onto /var/www/html with
# `relative_to`, and a docroot outside the project has no place there at all.
@pytest.mark.parametrize("docroot", ["/etc", "../sibling", "public/../../etc"])
def test_a_docroot_that_leaves_the_project_is_rejected(tmp_path: Path, docroot: str) -> None:
    write_ddev_config(tmp_path, f"name: acme\ntype: php\ndocroot: {docroot}\n")
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    message = str(caught.value)
    assert docroot in message
    assert ".ddev/config.yaml" in message


# `..` inside a docroot that still lands in the project is not the dangerous
# shape, but it is refused too: the rule a reader can hold is "a relative path
# with no `..`", and a containment check alone would accept
# `public/../public` while `placeholders.py` still refused it.
def test_a_docroot_that_climbs_and_comes_back_is_also_rejected(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "name: acme\ntype: php\ndocroot: public/../public\n")
    with pytest.raises(ConfigError):
        read_ddev_project(tmp_path)


@pytest.mark.parametrize("docroot", ["", "public", "web/html", "./public"])
def test_a_docroot_inside_the_project_is_kept_verbatim(tmp_path: Path, docroot: str) -> None:
    write_ddev_config(tmp_path, f"name: acme\ntype: php\ndocroot: {docroot}\n")
    assert read_ddev_project(tmp_path).docroot == docroot


def test_a_non_mapping_ddev_config_is_rejected(tmp_path: Path) -> None:
    write_ddev_config(tmp_path, "- one\n")
    with pytest.raises(ConfigError) as caught:
        read_ddev_project(tmp_path)
    assert "mapping" in str(caught.value)


def test_the_web_container_name_follows_ddevs_convention() -> None:
    project = DdevProject(name="acme", type="typo3", docroot="public", root=Path("/var/www/html"))
    assert project.web_container == "ddev-acme-web"


def test_the_web_and_db_container_names_differ_only_in_their_suffix() -> None:
    # Both are derived from the same `name`, so a project renamed in
    # .ddev/config.yaml cannot end up with one of them pointing at the old
    # project's container.
    project = DdevProject(
        name="shop",
        type="shopware6",
        docroot="public",
        root=Path("/var/www/html"),
    )
    assert project.db_container == "ddev-shop-db"
    assert project.web_container == "ddev-shop-web"


def test_canonicalises_a_relative_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # resolve_local_path returns canonicalised paths; _container_workdir uses
    # Path.relative_to which is lexical and fails if the roots have different
    # canonical forms. read_ddev_project must canonicalise so that relative_to
    # works, even when the engine is run with --project-root . or a symlinked
    # path.
    write_ddev_config(tmp_path, "name: acme\ntype: php\n")
    monkeypatch.chdir(tmp_path)
    assert read_ddev_project(Path(".")).root == tmp_path.resolve()


def test_canonicalises_a_symlinked_root(tmp_path: Path) -> None:
    # If the project is reached through a symlink, project.root must resolve
    # it so that relative_to works correctly. resolve_local_path returns real
    # paths, so a symlinked root would make a file inside the project look
    # like it is outside it.
    real_project = tmp_path / "real"
    symlink_project = tmp_path / "link"
    real_project.mkdir()
    write_ddev_config(real_project, "name: acme\ntype: php\n")
    symlink_project.symlink_to(real_project)
    project = read_ddev_project(symlink_project)
    assert project.root == real_project.resolve()
