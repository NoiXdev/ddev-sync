from pathlib import Path

import pytest

from ddev_sync.config.loader import ConfigPaths, discover_config_paths
from ddev_sync.config.writer import (
    WriteRefused,
    _repeated_key,
    place_target,
    render_target,
    write_target,
)

MANUAL = {
    "host": "web01.example",
    "user": "deploy",
    "port": 22,
    "path": "/srv/app/",
    "stage": "production",
}


def test_a_rendered_target_is_indented_for_the_targets_mapping() -> None:
    block = render_target("production", MANUAL)
    assert block.startswith("  production:\n")
    assert "    host: web01.example\n" in block
    assert "    port: 22\n" in block


def test_a_rendered_target_keeps_the_order_it_was_given() -> None:
    # Field order is how a human reads the block; `sort_keys=False` is what
    # keeps it, and a default-sorted dump would read host, path, port, stage.
    block = render_target("production", MANUAL)
    assert [line.split(":")[0].strip() for line in block.splitlines()[1:]] == [
        "host",
        "user",
        "port",
        "path",
        "stage",
    ]


def test_a_nested_block_renders_as_a_mapping() -> None:
    block = render_target("production", {"deployer": {"host": "prod"}, "stage": "production"})
    assert "    deployer:\n      host: prod\n" in block


def test_a_value_that_needs_quoting_gets_it() -> None:
    # PyYAML's problem, not ours - which is the point of rendering through it.
    block = render_target("production", {"path": "/srv/app/", "stage": "production", "user": "yes"})
    assert "'yes'" in block or '"yes"' in block


def test_a_document_without_a_targets_mapping_gains_one() -> None:
    document = "version: 1\n\n# targets:\n#   production:\n#     host: x\n"
    result = place_target(document, "production", render_target("production", MANUAL))
    assert "# targets:" in result, "the commented template must survive"
    assert "\ntargets:\n  production:\n" in result


def test_a_second_target_joins_the_existing_mapping() -> None:
    document = "version: 1\ntargets:\n  staging:\n    host: web02\n    stage: staging\n"
    result = place_target(document, "production", render_target("production", MANUAL))
    assert result.index("staging:") < result.index("production:")
    assert result.count("targets:") == 1


def test_an_existing_target_is_replaced_in_place() -> None:
    document = (
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: old.example\n"
        "    stage: production\n"
        "  staging:\n"
        "    host: web02\n"
        "    stage: staging\n"
    )
    result = place_target(document, "production", render_target("production", MANUAL))
    assert "old.example" not in result
    assert "web01.example" in result
    assert "staging:\n    host: web02" in result, "the neighbour must be untouched"
    assert result.index("production:") < result.index("staging:"), "order is kept"


def test_comments_inside_the_file_survive_a_replacement() -> None:
    document = (
        "version: 1\n"
        "# why this project syncs at all\n"
        "targets:\n"
        "  production:\n"
        "    host: old.example\n"
        "    stage: production\n"
        "# a trailing note\n"
    )
    result = place_target(document, "production", render_target("production", MANUAL))
    assert "# why this project syncs at all" in result
    assert "# a trailing note" in result


def write_in(tmp_path: Path, document: str | None) -> tuple[Path, ConfigPaths]:
    project = tmp_path / "project"
    (project / ".ddev").mkdir(parents=True)
    (project / ".ddev" / "config.yaml").write_text("name: acme\ntype: php\n", encoding="utf-8")
    target_file = project / ".ddev" / "ddev-sync.yaml"
    if document is not None:
        target_file.write_text(document, encoding="utf-8")
    return target_file, discover_config_paths(project, tmp_path / "home")


def test_a_missing_file_is_created_with_a_version(tmp_path: Path) -> None:
    path, paths = write_in(tmp_path, None)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    text = path.read_text(encoding="utf-8")
    # Without `version` the loader rejects the whole layer, so a file this
    # function creates and then cannot read back would be a broken write.
    assert text.startswith("version: 1\n")
    assert "web01.example" in text


def test_a_write_that_the_loader_cannot_read_back_is_undone(tmp_path: Path) -> None:
    # The net: a document whose shape defeats the placement must leave the
    # file exactly as it was. A flow-style mapping is one such shape.
    document = "version: 1\ntargets: {production: {host: old.example, stage: production}}\n"
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused) as caught:
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document, "the file must be byte-for-byte as before"
    assert "production" in str(caught.value)


def test_a_neighbouring_target_survives_a_replacement(tmp_path: Path) -> None:
    document = (
        "version: 1\ntargets:\n  production:\n    host: old\n    stage: production\n"
        "  staging:\n    host: web02\n    stage: staging\n"
    )
    path, paths = write_in(tmp_path, document)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    text = path.read_text(encoding="utf-8")
    assert "web02" in text and "staging" in text
    assert "host: old" not in text


def test_a_note_above_the_next_target_survives_a_replacement() -> None:
    document = (
        "version: 1\n"
        "targets:\n"
        "  production:\n"
        "    host: old.example\n"
        "\n"
        "  # the box we test on\n"
        "  staging:\n"
        "    host: web02\n"
    )
    result = place_target(document, "production", render_target("production", MANUAL))
    assert "\n  # the box we test on\n  staging:\n" in result


def test_a_document_without_a_final_newline_is_not_run_together() -> None:
    result = place_target("version: 1\ntargets:", "production", render_target("production", MANUAL))
    assert "targets:\n  production:\n" in result
    appended = place_target("version: 1", "production", render_target("production", MANUAL))
    assert appended.startswith("version: 1\n\ntargets:\n")


def test_an_empty_file_is_given_a_version(tmp_path: Path) -> None:
    path, paths = write_in(tmp_path, "")
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    assert path.read_text(encoding="utf-8").startswith("version: 1\n")


def test_a_target_the_loader_rejects_is_undone(tmp_path: Path) -> None:
    document = "version: 1\n# kept\n"
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="rejected"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", {"x": 1})
        )
    assert path.read_text(encoding="utf-8") == document


def test_a_file_that_did_not_exist_is_gone_again_after_a_refused_write(tmp_path: Path) -> None:
    path, paths = write_in(tmp_path, None)
    with pytest.raises(WriteRefused):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", {"x": 1})
        )
    assert not path.exists()


def test_another_target_changing_is_refused(tmp_path: Path) -> None:
    # Nothing but the named target may differ afterwards. A flow-style
    # mapping holding two targets is placed beside as a second `targets:`
    # key, which PyYAML reads as the only one: `staging` silently vanishes.
    document = (
        "version: 1\n"
        "targets: {production: {host: old, stage: production}, staging: {host: web02}}\n"
    )
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="staging"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


def test_a_target_that_is_not_there_afterwards_is_refused(tmp_path: Path) -> None:
    # The `targets:` line sits inside a quoted scalar, so the block is placed
    # in the middle of a string and the loader reads the file without error.
    document = 'version: 1\nnote: "a\ntargets:\nb"\n'
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="not there"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("", None),
        ("a: 1\nb: 2\n", None),
        ("a: 1\na: 2\n", "a"),
        ("a:\n  b: 1\n  b: 2\n", "b"),
        ("a:\n  - x: 1\n  - y: 1\n    y: 2\n", "y"),
        ("a:\n  - 1\n  - 2\n", None),
        ("1: a\n'1': b\n", None),
        ("? [a, b]\n: 1\n? [a, b]\n: 2\n", None),
    ],
)
def test_a_key_repeated_in_one_mapping_is_found(document: str, expected: str | None) -> None:
    assert _repeated_key(document) == expected
