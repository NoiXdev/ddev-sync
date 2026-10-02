from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.placeholders import expand_placeholders, expand_value
from ddev_sync.transport.fake import FakeCommandRunner


def make_context(
    runner: FakeCommandRunner, root: Path, *, docroot: str = "public", **config: Any
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="wordpress", docroot=docroot, root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
        ),
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
    )


def test_ddev_sitename_is_expanded(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("ddev-${DDEV_SITENAME}-db", context) == "ddev-acme-db"


def test_sync_target_database_defaults_to_db(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("${SYNC_TARGET_DATABASE}", context) == "db"


def test_sync_target_database_honours_the_config_override(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(), tmp_path, database={"target_database": "acme_local"}
    )
    assert expand_value("${SYNC_TARGET_DATABASE}", context) == "acme_local"


def test_a_non_string_value_passes_through_unchanged(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value(7, context) == 7
    assert expand_value(None, context) is None
    assert expand_value(True, context) is True


def test_a_list_and_a_dict_are_expanded_recursively(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value(["${DDEV_SITENAME}", 1], context) == ["acme", 1]
    assert expand_value({"a": "${DDEV_SITENAME}"}, context) == {"a": "acme"}


def test_an_unknown_placeholder_is_left_alone(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("${NOT_A_PLACEHOLDER}", context) == "${NOT_A_PLACEHOLDER}"


def test_ddev_docroot_expands_to_the_configured_docroot(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path, docroot="httpdocs")
    assert expand_value("${DDEV_DOCROOT}/fileadmin", context) == "httpdocs/fileadmin"


def test_ddev_docroot_expands_to_a_dot_when_the_docroot_is_the_project_root(
    tmp_path: Path,
) -> None:
    # `DdevProject.docroot` is "" when `.ddev/config.yaml` has no docroot (the
    # project root itself is the web root). A naive `f"{docroot}/x"` would
    # then produce "/x" - an absolute path at the filesystem root, not a
    # relative one under the project - so the empty case must resolve to "."
    # instead.
    context = make_context(FakeCommandRunner(), tmp_path, docroot="")
    assert expand_value("${DDEV_DOCROOT}/fileadmin", context) == "./fileadmin"


def test_ddev_docroot_rejects_an_absolute_path(tmp_path: Path) -> None:
    # `DdevProject.docroot` is read verbatim from `.ddev/config.yaml` and
    # never normalised (`ddev/project.py::read_ddev_project`). ${DDEV_DOCROOT}
    # ends up directly in a `run:` string (features/typo3-processedfiles.yaml's
    # `rm -rf`) - unlike `files.paths`, nothing downstream runs a `run:`
    # string through `context.local()`'s containment check, so an absolute
    # docroot would otherwise reach the shell exactly as written.
    context = make_context(FakeCommandRunner(), tmp_path, docroot="/etc")
    with pytest.raises(OperationError) as caught:
        expand_value("${DDEV_DOCROOT}/fileadmin", context)
    message = str(caught.value)
    assert "docroot" in message
    assert "/etc" in message


def test_ddev_docroot_rejects_a_path_containing_dotdot(tmp_path: Path) -> None:
    # The dangerous case: "../../etc" turns `rm -rf ${DDEV_DOCROOT}/...` into
    # `rm -rf ../../etc/...`, executed with cwd at the project root.
    context = make_context(FakeCommandRunner(), tmp_path, docroot="../../etc")
    with pytest.raises(OperationError) as caught:
        expand_value("${DDEV_DOCROOT}/fileadmin", context)
    message = str(caught.value)
    assert "docroot" in message
    assert "../../etc" in message


@pytest.mark.parametrize(
    "docroot",
    [
        # A space word-splits: `rm -rf web root/fileadmin/_processed_` is two
        # operands to `sh -c`, and the first one deletes the project's whole
        # `web` directory.
        pytest.param("web root", id="space"),
        # `sh -c` substitutes, globs and chains all of these before `rm` ever
        # sees them.
        pytest.param("pub$(id)", id="command-substitution"),
        pytest.param("pub`id`", id="backticks"),
        pytest.param("pub;rm -rf x", id="semicolon"),
        pytest.param("pub*", id="glob"),
        pytest.param("~/pub", id="tilde"),
    ],
)
def test_ddev_docroot_rejects_a_value_the_shell_would_reinterpret(
    tmp_path: Path, docroot: str
) -> None:
    # Neither of the two guards above (absolute, `..`) covers these, yet the
    # resolved value is spliced unquoted into a `sh -c` string - so rejecting
    # anything outside a plain relative path is what actually makes
    # ${DDEV_DOCROOT} safe to use in a `run:` command.
    context = make_context(FakeCommandRunner(), tmp_path, docroot=docroot)
    with pytest.raises(OperationError) as caught:
        expand_value("${DDEV_DOCROOT}/fileadmin", context)
    assert "docroot" in str(caught.value)


@pytest.mark.parametrize(
    "docroot",
    [
        pytest.param("public", id="plain"),
        pytest.param("public/", id="trailing-slash"),
        pytest.param("web/public", id="nested"),
        pytest.param("public_html", id="underscore"),
        pytest.param("my-site.v2", id="dash-and-dot"),
    ],
)
def test_ddev_docroot_accepts_an_ordinary_relative_docroot(tmp_path: Path, docroot: str) -> None:
    # The guard must not narrow what DDEV itself accepts: every one of these
    # is a legitimate `docroot:` value and must still expand verbatim.
    context = make_context(FakeCommandRunner(), tmp_path, docroot=docroot)
    assert expand_value("${DDEV_DOCROOT}/fileadmin", context) == f"{docroot}/fileadmin"


# --- ${WP_TABLE_PREFIX} -----------------------------------------------------

WP_CLI_SOURCE: dict[str, Any] = {"database": {"remote_credentials": "wp_cli"}}


def wp_cli_runner(table_prefix: str = "wp_") -> FakeCommandRunner:
    """A runner that answers the single ssh round trip `_from_wp_cli` makes
    for DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/table_prefix, in that order.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"acme_prod\nacme\nsecret\ndb\n{table_prefix}\n")
    return runner


def test_wp_table_prefix_is_read_from_production_not_the_local_checkout(tmp_path: Path) -> None:
    # The Part 1 correction: the local wp-config.php (DDEV-regenerated, or
    # gitignored entirely) is the wrong source - sanitize runs against the
    # imported *production* dump, so the prefix read here must be
    # production's, over the same ssh channel `read_remote_credentials`
    # already uses for a `wp_cli` source.
    runner = wp_cli_runner("wptest_")
    context = make_context(runner, tmp_path, **WP_CLI_SOURCE)
    assert expand_value("${WP_TABLE_PREFIX}users", context) == "wptest_users"
    assert runner.calls[0][0] == "ssh"


def test_wp_table_prefix_is_not_queried_when_a_config_override_is_set(tmp_path: Path) -> None:
    # FakeCommandRunner has no scripted commands at all here - if the override
    # were ignored and credentials were read anyway, this would raise
    # UnscriptedCommand instead of returning the configured value.
    context = make_context(FakeCommandRunner(), tmp_path, database={"wp_table_prefix": "custom_"})
    assert expand_value("${WP_TABLE_PREFIX}users", context) == "custom_users"


def test_wp_table_prefix_propagates_the_credentials_read_failure(tmp_path: Path) -> None:
    # The common case for an env-based setup (e.g. Bedrock): wp-config.php
    # has no static literal for wp-cli to read back, so the shared
    # credentials read fails outright. The resulting message (from
    # `_from_wp_cli`) must still point at the override that matters here.
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=1, stderr="wp-config.php not found")
    context = make_context(runner, tmp_path, **WP_CLI_SOURCE)
    with pytest.raises(OperationError) as caught:
        expand_value("${WP_TABLE_PREFIX}users", context)
    message = str(caught.value)
    assert "database.wp_table_prefix" in message
    assert "wp-config.php not found" in message


def test_wp_table_prefix_without_a_wp_cli_source_names_the_source_and_override(
    tmp_path: Path,
) -> None:
    # Only the `wp_cli` credential source reads a table prefix at all - an
    # `explicit` or `env` source (valid for every other profile) has
    # nowhere to get one from, so this must fail with an actionable message
    # rather than crash on a `None` prefix.
    context = make_context(
        FakeCommandRunner(),
        tmp_path,
        database={
            "remote_credentials": "explicit",
            "credentials": {
                "name": "n",
                "user": "u",
                "password": "p",
                "host": "h",
                "port": "3306",
            },
        },
    )
    with pytest.raises(OperationError) as caught:
        expand_value("${WP_TABLE_PREFIX}users", context)
    message = str(caught.value)
    assert "explicit" in message
    assert "database.wp_table_prefix" in message


def test_wp_table_prefix_resolves_only_once_per_context_across_many_expansions(
    tmp_path: Path,
) -> None:
    runner = wp_cli_runner("wp_")
    context = make_context(runner, tmp_path, **WP_CLI_SOURCE)
    assert expand_value("${WP_TABLE_PREFIX}users", context) == "wp_users"
    assert expand_value("${WP_TABLE_PREFIX}options", context) == "wp_options"
    assert len(runner.calls) == 1


def test_wp_table_prefix_config_override_rejects_an_unsafe_identifier(tmp_path: Path) -> None:
    # The prefix is spliced directly into unquoted raw SQL (see
    # profiles/wordpress.yaml's `sql:` rules) - a backtick, quote or space
    # here is a config mistake, not something to accommodate.
    context = make_context(FakeCommandRunner(), tmp_path, database={"wp_table_prefix": "wp`_"})
    with pytest.raises(OperationError) as caught:
        expand_value("${WP_TABLE_PREFIX}users", context)
    message = str(caught.value)
    assert "database.wp_table_prefix" in message
    assert "wp`_" in message


def test_wp_table_prefix_from_production_rejects_an_unsafe_identifier(tmp_path: Path) -> None:
    # Production's wp-config.php is not config the user wrote by hand, but
    # it is still their own server - the same guard must still catch it,
    # since a bad prefix there is just as capable of producing a baffling
    # SQL error or worse.
    runner = wp_cli_runner("wp'_")
    context = make_context(runner, tmp_path, **WP_CLI_SOURCE)
    with pytest.raises(OperationError) as caught:
        expand_value("${WP_TABLE_PREFIX}users", context)
    message = str(caught.value)
    assert "wp'_" in message
    # The production case is the one a user can least easily diagnose - it
    # is not a value they wrote in config.yaml - so its error message must
    # still name the override, exactly like the config-override case above.
    assert "database.wp_table_prefix" in message


def test_symfony_user_table_rejects_an_unsafe_identifier(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(), tmp_path, database={"symfony_user_table": "user; DROP TABLE x"}
    )
    with pytest.raises(OperationError) as caught:
        expand_value("${SYMFONY_USER_TABLE}", context)
    message = str(caught.value)
    assert "database.symfony_user_table" in message


def test_symfony_user_table_rejects_an_empty_string(tmp_path: Path) -> None:
    # `context.config` returns an explicit "" verbatim rather than falling
    # back to the "user" default (only a missing key does that) - so
    # `database.symfony_user_table: ""` reaches the validator as a literal
    # empty string. `[A-Za-z0-9_]*` (zero-or-more) would have accepted it,
    # producing "UPDATE  SET ..." - a real SQL syntax error, not a safety
    # net. The pattern must require at least one character.
    context = make_context(FakeCommandRunner(), tmp_path, database={"symfony_user_table": ""})
    with pytest.raises(OperationError) as caught:
        expand_value("${SYMFONY_USER_TABLE}", context)
    assert "database.symfony_user_table" in str(caught.value)


def test_a_value_with_no_wp_placeholder_never_reads_remote_credentials(tmp_path: Path) -> None:
    # The laziness guarantee this whole module exists for: a TYPO3 (or any
    # other non-WordPress) profile's sql/params never mention
    # ${WP_TABLE_PREFIX}, so resolving them must never read remote
    # credentials at all - an eager resolver would cost an ssh round trip on
    # every sync, for every profile, regardless of whether anything actually
    # asked for it. FakeCommandRunner has no scripted commands, so any
    # attempted call raises UnscriptedCommand.
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("TRUNCATE TABLE be_sessions", context) == "TRUNCATE TABLE be_sessions"


# --- ${SYMFONY_USER_TABLE} ---------------------------------------------------


def test_symfony_user_table_defaults_to_user(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_value("${SYMFONY_USER_TABLE}", context) == "user"


def test_symfony_user_table_is_overridable(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(), tmp_path, database={"symfony_user_table": "app_user"}
    )
    assert expand_value("${SYMFONY_USER_TABLE}", context) == "app_user"


# --- expand_placeholders (flat mapping) -------------------------------------


def test_expand_placeholders_still_works_as_a_flat_mapping(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    assert expand_placeholders({"DB_HOST": "ddev-${DDEV_SITENAME}-db"}, context) == {
        "DB_HOST": "ddev-acme-db"
    }


def test_expand_placeholders_of_an_empty_mapping_is_empty(tmp_path: Path) -> None:
    assert expand_placeholders({}, make_context(FakeCommandRunner(), tmp_path)) == {}
