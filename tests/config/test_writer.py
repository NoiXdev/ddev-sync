import os
import stat
from pathlib import Path

import pytest

from ddev_sync.config import writer
from ddev_sync.config.loader import ConfigPaths, discover_config_paths
from ddev_sync.config.writer import (
    WriteRefused,
    place_target,
    render_target,
    write_target,
)
from ddev_sync.errors import ConfigError

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
    # keeps it, and a default-sorted dump would read host, path, port, stage, user.
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


def test_a_second_targets_key_beside_a_flow_mapping_is_undone(tmp_path: Path) -> None:
    # The scan matches only a line that is exactly `targets:`, so a flow-style
    # mapping is missed and the block is appended as a second `targets:` key.
    # `yamlfile.py` is what refuses that: plain `safe_load` would keep
    # the last of the two and report a clean file with the first one gone.
    document = "version: 1\ntargets: {production: {host: old.example, stage: production}}\n"
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="more than once"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document, "the file must be byte-for-byte as before"


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
    # A flow-style mapping holding two targets is missed by the scan and the
    # block is appended beside it, so the file ends up with `targets:` twice -
    # which the strict loader refuses before the comparison that would have
    # noticed `staging` vanishing. Either way nothing is written.
    document = (
        "version: 1\n"
        "targets: {production: {host: old, stage: production}, staging: {host: web02}}\n"
    )
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="more than once"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


NOTE_DOCUMENT = (
    'version: 1\nnote: "a\ntargets:\nb"\n'
    "targets:\n  production:\n    host: old.example\n    stage: production\n"
)


def test_a_block_that_landed_inside_a_quoted_scalar_is_refused(tmp_path: Path) -> None:
    # The `targets:` line inside the string is taken for the real one, so the
    # block is placed into `note`. The loader reads the result fine and the
    # name is present, but the real target still has its old host.
    path, paths = write_in(tmp_path, NOTE_DOCUMENT)
    with pytest.raises(WriteRefused, match="not the one that was written"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == NOTE_DOCUMENT


def test_a_target_defined_in_another_layer_does_not_vouch_for_the_write(tmp_path: Path) -> None:
    document = 'version: 1\nnote: "a\ntargets:\nb"\n'
    path, paths = write_in(tmp_path, document)
    paths.global_config.parent.mkdir(parents=True)
    paths.global_config.write_text(
        "version: 1\ntargets:\n  production:\n    host: elsewhere\n", encoding="utf-8"
    )
    with pytest.raises(WriteRefused, match="not the one that was written"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


def test_a_change_outside_targets_is_refused(tmp_path: Path) -> None:
    # The real `production` already equals the block, so the target looks
    # right; the damage is the string that now holds a copy of it.
    document = (
        'version: 1\nnote: "a\ntargets:\nb"\n' + "targets:\n" + render_target("production", MANUAL)
    )
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="'note' outside"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


def test_a_comment_inside_a_replaced_block_is_deleted(tmp_path: Path) -> None:
    # Deliberate: the block is replaced as a whole, and the template tells
    # people to comment their targets, so this is pinned rather than found out.
    document = (
        "version: 1\ntargets:\n  production:\n    # the old box\n    host: old.example\n"
        "    stage: production\n"
    )
    path, paths = write_in(tmp_path, document)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    assert "the old box" not in path.read_text(encoding="utf-8")


def test_a_self_referencing_alias_elsewhere_in_the_file_is_survived(tmp_path: Path) -> None:
    document = "version: 1\nx: &x [*x]\n"
    path, paths = write_in(tmp_path, document)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith(document)
    assert "web01.example" in text


def test_a_file_that_already_holds_a_key_twice_is_not_written_to(tmp_path: Path) -> None:
    # Since `yamlfile.py` refuses such a file, the read *before* the
    # write fails and nothing is attempted. That is the right order: the
    # engine would misread that configuration, so adding a target to it would
    # be building on a file whose contents it cannot agree on.
    document = "version: 1\nfoo: 1\nfoo: 2\n"
    path, paths = write_in(tmp_path, document)
    with pytest.raises(ConfigError, match="more than once"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


def test_a_block_that_repeats_a_key_is_refused_with_its_name(tmp_path: Path) -> None:
    # A target whose own block repeats a key: the placement is right, the file
    # is not, and the loader names the key and the line it recurs on.
    document = "version: 1\ntargets:\n  staging:\n    host: web02\n"
    path, paths = write_in(tmp_path, document)
    block = "  production:\n    host: a\n    host: b\n"
    with pytest.raises(WriteRefused, match="'host' more than once"):
        write_target(path=path, paths=paths, name="production", block=block)
    assert path.read_text(encoding="utf-8") == document


def test_a_neighbouring_target_that_changed_is_refused(tmp_path: Path) -> None:
    # A quoted target key the scan cannot match, plus a multi-line quoted
    # scalar for the block to land in: the file loads, no key appears twice,
    # the written target matches its block and nothing outside `targets`
    # moved - so this check is the only one that notices `staging` losing its
    # path. Compared within the file, so a stronger layer cannot hide it.
    document = (
        "version: 1\n"
        "targets:\n"
        '  "production":\n'
        "    host: web01.example\n"
        "    user: deploy\n"
        "    port: 22\n"
        "    path: /srv/app/\n"
        "    stage: production\n"
        "  staging:\n"
        "    host: s.example\n"
        "    stage: staging\n"
        '    path: "abc\n'
        'def"\n'
    )
    path, paths = write_in(tmp_path, document)
    with pytest.raises(WriteRefused, match="staging"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


def test_crlf_line_endings_survive_a_write(tmp_path: Path) -> None:
    document = "version: 1\r\n# note\r\ntargets:\r\n  staging:\r\n    host: web02\r\n"
    path, paths = write_in(tmp_path, document)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    data = path.read_bytes()
    assert data.startswith(document.encode())
    assert data.count(b"\n") == data.count(b"\r\n"), "no bare newline may be mixed in"
    assert b"web01.example" in data


def test_crlf_replacement_keeps_the_neighbours(tmp_path: Path) -> None:
    document = (
        "version: 1\r\ntargets:\r\n  production:\r\n    host: old\r\n"
        "  staging:\r\n    host: web02\r\n"
    )
    path, paths = write_in(tmp_path, document)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    data = path.read_bytes()
    assert b"host: old" not in data
    assert data.endswith(b"  staging:\r\n    host: web02\r\n")
    assert data.count(b"\n") == data.count(b"\r\n")


def test_a_refused_write_to_a_crlf_file_is_byte_for_byte(tmp_path: Path) -> None:
    document = "version: 1\r\ntargets: {production: {host: old, stage: production}}\r\n"
    path, paths = write_in(tmp_path, document)
    path.write_bytes(document.encode())
    with pytest.raises(WriteRefused):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_bytes() == document.encode()


def test_a_failing_rename_leaves_the_file_whole_and_no_temporary_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = "version: 1\n# kept\n"
    path, paths = write_in(tmp_path, document)

    def refuse(source: object, destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    before = sorted(p.name for p in path.parent.iterdir())
    with pytest.raises(OSError, match="disk full"):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document
    assert sorted(p.name for p in path.parent.iterdir()) == before


def test_an_unforeseen_error_after_the_write_restores_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = "version: 1\n# kept\n"
    path, paths = write_in(tmp_path, document)

    def explode(_document: str) -> object:
        raise RuntimeError("not a DdevSyncError")

    monkeypatch.setattr(writer, "_outside_targets", explode)
    with pytest.raises(RuntimeError):
        write_target(
            path=path, paths=paths, name="production", block=render_target("production", MANUAL)
        )
    assert path.read_text(encoding="utf-8") == document


def test_a_new_file_is_world_readable_and_an_existing_mode_is_kept(tmp_path: Path) -> None:
    path, paths = write_in(tmp_path, None)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    path.chmod(0o600)
    write_target(path=path, paths=paths, name="staging", block=render_target("staging", MANUAL))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_symlinked_file_stays_a_symlink(tmp_path: Path) -> None:
    path, paths = write_in(tmp_path, None)
    real = tmp_path / "shared.yaml"
    real.write_text("version: 1\n", encoding="utf-8")
    path.symlink_to(real)
    write_target(
        path=path, paths=paths, name="production", block=render_target("production", MANUAL)
    )
    assert path.is_symlink()
    assert "web01.example" in real.read_text(encoding="utf-8")
