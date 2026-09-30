from pathlib import Path

import pytest

from ddev_sync.config.loader import (
    ConfigPaths,
    discover_config_paths,
    load_user_layers,
)
from ddev_sync.errors import ConfigError


def test_discovery_points_at_the_three_user_layers(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    home = tmp_path / "home"
    paths = discover_config_paths(project_root, home)
    assert paths == ConfigPaths(
        global_config=home / ".ddev" / "ddev-sync" / "config.yaml",
        project_config=project_root / ".ddev" / "ddev-sync.yaml",
        local_config=project_root / ".ddev" / "ddev-sync.local.yaml",
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def paths_in(tmp_path: Path) -> ConfigPaths:
    return discover_config_paths(tmp_path / "project", tmp_path / "home")


def test_only_existing_files_become_layers_and_order_is_weakest_first(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    write(paths.global_config, "version: 1\ndefaults:\n  keep_dump: false\n")
    write(paths.project_config, "version: 1\nproject:\n  type: typo3\n")
    layers = load_user_layers(paths)
    assert [entry.label for entry in layers] == [
        "~/.ddev/ddev-sync/config.yaml",
        ".ddev/ddev-sync.yaml",
    ]
    assert layers[0].data == {"defaults": {"keep_dump": False}}
    assert layers[1].data == {"project": {"type": "typo3"}}


def test_no_files_at_all_is_not_an_error(tmp_path: Path) -> None:
    assert load_user_layers(paths_in(tmp_path)) == []


def test_the_version_key_is_stripped_so_it_never_reaches_the_merge(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    write(paths.project_config, "version: 1\nproject:\n  type: laravel\n")
    assert "version" not in load_user_layers(paths)[0].data


def test_a_missing_version_key_is_rejected_by_name(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    write(paths.project_config, "project:\n  type: laravel\n")
    with pytest.raises(ConfigError) as caught:
        load_user_layers(paths)
    assert ".ddev/ddev-sync.yaml" in str(caught.value)
    assert "version: 1" in str(caught.value)


def test_an_unsupported_version_is_rejected(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    write(paths.project_config, "version: 7\n")
    with pytest.raises(ConfigError) as caught:
        load_user_layers(paths)
    assert "version 7" in str(caught.value)


def test_unparseable_yaml_names_the_file(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    write(paths.project_config, "version: 1\nproject: [unclosed\n")
    with pytest.raises(ConfigError) as caught:
        load_user_layers(paths)
    assert ".ddev/ddev-sync.yaml" in str(caught.value)


def test_a_top_level_list_is_rejected(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    write(paths.project_config, "- one\n- two\n")
    with pytest.raises(ConfigError) as caught:
        load_user_layers(paths)
    assert "must contain a mapping" in str(caught.value)


@pytest.mark.parametrize("content", ["", "---\n", "# only a comment\n"])
def test_an_empty_file_is_an_empty_layer_not_a_crash(tmp_path: Path, content: str) -> None:
    paths = paths_in(tmp_path)
    write(paths.project_config, content)
    layers = load_user_layers(paths)
    assert len(layers) == 1
    assert layers[0].data == {}
