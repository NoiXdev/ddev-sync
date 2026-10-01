from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Layout, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, PathEscapesProject
from ddev_sync.transport.fake import FakeCommandRunner


def context(*, layout: Layout = Layout.PLAIN, **config: Any) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="typo3", docroot="public", root=Path("/srv/local")),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
            layout=layout,
        ),
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=FakeCommandRunner(),
        downloads_dir=Path("/srv/local/.ddev/.downloads"),
    )


def test_config_reads_a_dotted_path() -> None:
    assert (
        context(database={"target_database": "acme"}).config(
            "database.target_database", default="db"
        )
        == "acme"
    )


def test_config_falls_back_when_any_segment_is_missing() -> None:
    assert context().config("database.target_database", default="db") == "db"


def test_config_falls_back_when_a_segment_is_not_a_mapping() -> None:
    assert context(database="a string").config("database.x", default="fallback") == "fallback"


def test_config_list_returns_only_mappings() -> None:
    ctx = context(files={"paths": [{"remote": "a/"}, "a bare string", {"remote": "b/"}]})
    assert ctx.config_list("files.paths") == [{"remote": "a/"}, {"remote": "b/"}]


def test_config_list_of_a_missing_path_is_empty() -> None:
    assert context().config_list("files.paths") == []


def test_config_list_returns_empty_when_the_final_value_is_not_a_list() -> None:
    assert context(files={"paths": "not a list"}).config_list("files.paths") == []


def test_remote_data_joins_onto_the_targets_data_root() -> None:
    assert context().remote_data("public/fileadmin/") == "/srv/app/public/fileadmin/"


def test_remote_data_does_not_double_the_separator() -> None:
    assert context().remote_data("/public/fileadmin/") == "/srv/app/public/fileadmin/"


def test_remote_code_joins_onto_the_targets_code_root() -> None:
    assert context().remote_code("bin/console") == "/srv/app/bin/console"


def test_remote_code_does_not_double_the_separator() -> None:
    assert context().remote_code("/bin/console") == "/srv/app/bin/console"


def test_remote_data_and_remote_code_agree_for_a_plain_target() -> None:
    ctx = context(layout=Layout.PLAIN)
    assert ctx.remote_data("x") == ctx.remote_code("x") == "/srv/app/x"


def test_remote_data_and_remote_code_diverge_for_a_deployer_target() -> None:
    ctx = context(layout=Layout.DEPLOYER)
    assert ctx.remote_data("public/fileadmin/") == "/srv/app/shared/public/fileadmin/"
    assert ctx.remote_code("bin/console") == "/srv/app/current/bin/console"


def test_local_joins_onto_the_project_root() -> None:
    assert context().local("public/fileadmin/") == Path("/srv/local/public/fileadmin")


@pytest.mark.parametrize(
    "escape",
    ["../outside", "../../etc/passwd", "public/../../outside", "/etc/passwd"],
)
def test_local_refuses_to_leave_the_project_root(escape: str) -> None:
    with pytest.raises(PathEscapesProject) as caught:
        context().local(escape)
    assert escape in str(caught.value)


def test_local_allows_a_path_that_only_looks_like_an_escape() -> None:
    assert context().local("public/../public/uploads") == Path("/srv/local/public/uploads")


def test_assume_yes_and_keep_dump_default_to_false() -> None:
    ctx = context()
    assert ctx.assume_yes is False
    assert ctx.keep_dump is False
