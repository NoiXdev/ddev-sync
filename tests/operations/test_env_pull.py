"""Reading what `env.pull` names from the remote, and refusing what cannot come."""

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.config.merge import LayerDoc, merge_layers
from ddev_sync.config.models import Provider, ResolvedTarget, Stage
from ddev_sync.ddev.project import DdevProject
from ddev_sync.docs import doc_link
from ddev_sync.errors import ConfigError
from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import (
    HANDOVER_MARKER,
    HANDOVER_PATH,
    fetch_remote_env_file,
    read_remote_credentials,
)
from ddev_sync.operations.env_pull import (
    RemoteValues,
    check_pull,
    encode_for_env_file,
    logical_assignments,
    raw_assignments,
    read_remote_values,
)
from ddev_sync.transport.fake import FakeCommandRunner
from tests.operations.phpdotenv_rules import load_like_phpdotenv

ENV_PATH = "/srv/app/.env"
# The same value `tests/integration/test_endpoint_image.py` hands through a real
# endpoint: every character a quoting mistake would eat.
TRICKY = " pa'ss\"#=$ word "


def make_context(
    runner: FakeCommandRunner,
    root: Path,
    *,
    provider: Provider = Provider.SERVER,
    **config: Any,
) -> ExecutionContext:
    return ExecutionContext(
        project=DdevProject(name="acme", type="laravel", docroot="public", root=root),
        target=ResolvedTarget(
            name="production",
            host="web01.example",
            user="deploy",
            port=22,
            path="/srv/app/",
            stage=Stage.PRODUCTION,
            provider=provider,
        ),
        merged=merge_layers([LayerDoc("profile", config)]),
        runner=runner,
        downloads_dir=root / ".ddev" / ".downloads",
    )


def remote_file(path: str, contents: str) -> FakeCommandRunner:
    """A remote whose probe finds `path`: its name on one line, then the file."""
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout=f"{path}\n{contents}")
    return runner


def handover(*lines: str) -> str:
    return "\n".join((HANDOVER_MARKER, *lines)) + "\n"


def read_like_phpdotenv(text: str) -> str:
    """What phpdotenv makes of a right-hand side `encode_for_env_file` wrote.

    Only the three forms the encoder produces, by `EntryParser::processToken`:
    single quotes are literal and close on the first `'`, double quotes take
    `\\"`, `\\\\` and `\\$` as escapes and read a bare `$` as a variable, and a
    bare value is the value. Anything else fails here, as it would there.
    """
    if text.startswith("'"):
        assert text.index("'", 1) == len(text) - 1
        return text[1:-1]
    if text.startswith('"'):
        assert re.fullmatch(r'"(?:[^"\\$]|\\["\\$])*"', text)
        return re.sub(r'\\(["\\$])', r"\1", text[1:-1])
    return text


# --- encode_for_env_file ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("abc-1.2:x@y", "abc-1.2:x@y"),
        ("", ""),
        ("base64:abc+/=", "base64:abc+/="),
        ("a,b_c", "a,b_c"),
        ("a b", "'a b'"),
        ("a\\b", "'a\\b'"),
        ("C:\\", "'C:\\'"),
        ("${X}", "'${X}'"),
        ("#x", "'#x'"),
        ('"x"', "'\"x\"'"),
        ("abc\n", "'abc\n'"),
        ("it's", '"it\'s"'),
        ('it\'s $x \\ "q"', '"it\'s \\$x \\\\ \\"q\\""'),
        ('x="y', '"x=\\"y"'),
        ('="', '"=\\""'),
        ('it\'s="x', '"it\'s=\\"x"'),
        ('$a\\="', '"\\$a\\\\=\\""'),
        # A `=` and a `"` apart do not start a multi-line value.
        ('a=b"c', "'a=b\"c'"),
        ("x='y z", '"x=\'y z"'),
    ],
)
def test_encoding_a_verbatim_value(value: str, written: str) -> None:
    assert encode_for_env_file(value) == written


@pytest.mark.parametrize(
    "value",
    [
        'x="y',
        '="',
        'a="b"c"',
        '=" ',
        '#="',
        '\\="',
        'a\\="',
        '$="',
        'it\'s="x',
        "x='\"",
        'a=""',
        TRICKY,
        'a=b"c',
        "plain value",
    ],
)
@pytest.mark.parametrize(
    ("before", "after"),
    [("FIRST=1\n", 'B=2\nC="three"\nD=4\n'), ("", ""), ('Z="z"\n', "\n# a comment\nB=2\n")],
)
def test_an_encoded_value_does_not_disturb_the_lines_around_it(
    value: str, before: str, after: str
) -> None:
    # The check that failed is phpdotenv's line grouping, which ignores quote style:
    # `A='x="y'` reads as the start of a multi-line value and takes the lines behind
    # it along, so the whole file failed to parse. Hence the neighbours.
    text = f"{before}A={encode_for_env_file(value)}\n{after}"
    loaded = load_like_phpdotenv(text)
    assert loaded.pop("A") == value
    assert loaded == load_like_phpdotenv(f"{before}{after}")


@pytest.mark.parametrize(
    "value",
    [
        TRICKY,
        "",
        "abc-1.2:x@y",
        "a b",
        "a\\b",
        "${X}",
        "$",
        "it's",
        'it\'s $x \\ "q"',
        "C:\\",
        "\\\\",
        "\\'",
        "'\\",
        "a\\'b",
        "'\"",
        "'\\\"",
    ],
)
def test_an_encoded_value_reads_back_as_it_was(value: str) -> None:
    assert read_like_phpdotenv(encode_for_env_file(value)) == value


# --- raw_assignments --------------------------------------------------------


def test_the_right_hand_side_is_kept_as_written_with_its_comment() -> None:
    assert raw_assignments('A="x y" # c\n') == ({"A": '"x y" # c'}, set())


def test_the_last_of_two_assignments_wins() -> None:
    assert raw_assignments("A=1\nA=2\n") == ({"A": "2"}, set())


def test_export_assigns_the_name_behind_it() -> None:
    assert raw_assignments("export A=1\n") == ({"A": "1"}, set())


def test_the_space_around_the_equals_sign_is_not_the_value() -> None:
    assert raw_assignments("A = 1\n") == ({"A": "1"}, set())


def test_a_carriage_return_is_not_the_value() -> None:
    assert raw_assignments("A=1\r\nB=2\r\n") == ({"A": "1", "B": "2"}, set())


def test_only_a_line_feed_ends_a_line() -> None:
    # `str.splitlines` also splits on these, which would cut a value in two.
    assert raw_assignments("A=x\x0cy\N{LINE SEPARATOR}z\n") == (
        {"A": "x\x0cy\N{LINE SEPARATOR}z"},
        set(),
    )


def test_a_comment_and_a_line_without_assignment_are_skipped() -> None:
    assert raw_assignments('# A=1\n  # B="open\nnothing here\n\nC=3\n') == ({"C": "3"}, set())


def test_a_set_but_empty_value_is_an_assignment() -> None:
    assert raw_assignments("A=\nB=''\n") == ({"A": "", "B": "''"}, set())


def test_the_scanner_says_which_lines_each_assignment_takes() -> None:
    lines = ["# c", 'A="x', "B=2", 'y" # z', "export C = 3", "D='open"]
    found = [
        (entry.name, entry.first, entry.last, entry.closed, entry.head)
        for entry in logical_assignments(lines)
    ]
    assert found == [
        ("A", 1, 3, True, "A="),
        ("C", 4, 4, True, "export C = "),
        ("D", 5, 5, False, "D="),
    ]


def test_the_scanner_goes_on_behind_a_quote_never_closed() -> None:
    # Its caller decides what that means: the remote reading stops there, and the
    # local rewrite treats the assignment as its own line.
    assert [entry.name for entry in logical_assignments(["A='x", "B=1"])] == ["A", "B"]


def test_a_scanned_value_does_not_reach_a_repr() -> None:
    assert "topsecret" not in repr(next(iter(logical_assignments(["A=topsecret"]))))


def test_a_quote_left_open_takes_the_lines_up_to_its_close_with_it() -> None:
    assert raw_assignments('A="line1\nB=2\nline3"\nC=3\n') == ({"C": "3"}, {"A"})


def test_a_single_quote_left_open_does_too() -> None:
    assert raw_assignments("A='line1\nB=2\nline3'\nC=3\n") == ({"C": "3"}, {"A"})


def test_a_backslash_is_an_ordinary_character_in_single_quotes() -> None:
    # Measured against phpdotenv v5.7.0: `A='C:\'` reads `C:\`, and the next line
    # is the next assignment.
    assert raw_assignments("A='C:\\'\nB=2\nC=3\n") == (
        {"A": "'C:\\'", "B": "2", "C": "3"},
        set(),
    )


def test_a_backslash_does_not_hide_the_quote_that_closes_a_multi_line_single_quote() -> None:
    assert raw_assignments("A='line1\nline2\\'\nB=2\n") == ({"B": "2"}, {"A"})


def test_a_quote_of_the_other_kind_does_not_close_it() -> None:
    assert raw_assignments('A="line1\nit\'s\nB=2\n"\nC=3\n') == ({"C": "3"}, {"A"})


def test_a_quote_never_closed_takes_the_rest_of_the_file() -> None:
    assert raw_assignments('A="line1\nB=2\n') == ({}, {"A"})


def test_an_escaped_quote_does_not_close_a_value() -> None:
    assert raw_assignments('A="x\\"y"\nB=2\n') == ({"A": '"x\\"y"', "B": "2"}, set())
    assert raw_assignments('A="x\\"\nB=2\n"\nC=3\n') == ({"C": "3"}, {"A"})


def test_an_escaped_backslash_leaves_the_quote_closing() -> None:
    assert raw_assignments('A="x\\\\"\nB=2\n') == ({"A": '"x\\\\"', "B": "2"}, set())


def test_a_quote_inside_an_unquoted_value_opens_nothing() -> None:
    assert raw_assignments("A=it's\nB=2\n") == ({"A": "it's", "B": "2"}, set())


def test_a_later_assignment_replaces_an_earlier_multi_line_one() -> None:
    assert raw_assignments('A="line1\nline2"\nA=1\n') == ({"A": "1"}, set())


def test_a_later_multi_line_assignment_replaces_an_earlier_one() -> None:
    assert raw_assignments('A=1\nA="line1\nline2"\n') == ({}, {"A"})


# --- read_remote_values -----------------------------------------------------


def test_from_a_env_file_the_text_is_raw_and_only_what_was_named(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, 'APP_KEY="a b" # c\nOTHER=secret\n')
    values = read_remote_values(make_context(runner, tmp_path), ["APP_KEY"])
    assert values.path == ENV_PATH
    assert values.text == {"APP_KEY": '"a b" # c'}
    assert values.missing == []
    assert values.multi_line == []
    assert values.references == []


def test_from_a_env_file_a_name_it_does_not_have_is_missing(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    values = read_remote_values(make_context(runner, tmp_path), ["B", "A", "C"])
    assert values.text == {"A": "1"}
    assert values.missing == ["B", "C"]


def test_from_a_env_file_an_open_quote_is_multi_line_and_not_missing(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, 'PEM="line1\nline2"\n')
    values = read_remote_values(make_context(runner, tmp_path), ["PEM"])
    assert values.multi_line == ["PEM"]
    assert values.missing == []
    assert values.text == {}


@pytest.mark.parametrize(
    "line",
    ["A=$B", 'A="${B}"', "A=x$B", 'A="\\\\$B"', "A=$B # c", 'A="$B" # c'],
)
def test_a_value_that_refers_to_another_variable_is_named(tmp_path: Path, line: str) -> None:
    runner = remote_file(ENV_PATH, f"{line}\n")
    values = read_remote_values(make_context(runner, tmp_path), ["A"])
    assert values.references == ["A"]
    assert values.text == {"A": line.removeprefix("A=")}


@pytest.mark.parametrize(
    "line",
    ["A='${B}'", "A=plain", 'A="\\$B"', "A=\\$B", "A=x # costs $5", 'A="x" # costs $5', "A=#$B"],
)
def test_a_dollar_that_refers_to_nothing_is_not_named(tmp_path: Path, line: str) -> None:
    runner = remote_file(ENV_PATH, f"{line}\n")
    values = read_remote_values(make_context(runner, tmp_path), ["A"])
    assert values.references == []


def test_a_reference_is_only_named_when_it_was_asked_for(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\nB=$A\n")
    assert read_remote_values(make_context(runner, tmp_path), ["A"]).references == []


def docker_context(runner: FakeCommandRunner, root: Path, **config: Any) -> ExecutionContext:
    return make_context(runner, root, provider=Provider.DOCKER, **config)


def test_from_the_handover_a_value_is_encoded(tmp_path: Path) -> None:
    runner = remote_file(HANDOVER_PATH, handover(f"SECRET={TRICKY}", "PLAIN=abc", "HOME=/root"))
    values = read_remote_values(docker_context(runner, tmp_path), ["SECRET", "PLAIN"])
    assert values.path == HANDOVER_PATH
    assert values.text == {"SECRET": encode_for_env_file(TRICKY), "PLAIN": "abc"}
    assert read_like_phpdotenv(values.text["SECRET"]) == TRICKY


def test_from_the_handover_a_dollar_is_no_reference(tmp_path: Path) -> None:
    # The value is verbatim, so `$` is a character of it; the quotes it gets
    # keep it one in the local file.
    runner = remote_file(HANDOVER_PATH, handover("A=${B}", "C=$D"))
    values = read_remote_values(docker_context(runner, tmp_path), ["A", "C"])
    assert values.references == []
    assert values.text == {"A": "'${B}'", "C": "'$D'"}


def test_from_the_handover_a_skipped_name_is_multi_line(tmp_path: Path) -> None:
    runner = remote_file(HANDOVER_PATH, handover("A=1", "DDEV_SYNC_SKIPPED=PEM,KEY,UNASKED"))
    values = read_remote_values(docker_context(runner, tmp_path), ["KEY", "A", "PEM", "GONE"])
    assert values.multi_line == ["KEY", "PEM"]
    assert values.missing == ["GONE"]
    assert values.text == {"A": "1"}


def test_from_the_handover_a_list_endpoint_has_no_skipped_line(tmp_path: Path) -> None:
    runner = remote_file(HANDOVER_PATH, handover("A=1"))
    values = read_remote_values(docker_context(runner, tmp_path), ["A", "B"])
    assert values.multi_line == []
    assert values.missing == ["B"]


@pytest.mark.parametrize(
    ("path", "contents"),
    [(ENV_PATH, "EMPTY=\n"), (HANDOVER_PATH, handover("EMPTY="))],
)
def test_a_set_but_empty_name_is_text_and_not_missing(
    tmp_path: Path, path: str, contents: str
) -> None:
    runner = remote_file(path, contents)
    values = read_remote_values(docker_context(runner, tmp_path), ["EMPTY"])
    assert values.text == {"EMPTY": ""}
    assert values.missing == []


def test_one_ssh_call_however_often_it_is_read(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\nB=2\n")
    context = make_context(runner, tmp_path)
    read_remote_values(context, ["A"])
    read_remote_values(context, ["B"])
    fetch_remote_env_file(context)
    assert len(runner.calls) == 1


def test_the_read_is_shared_with_the_credentials(tmp_path: Path) -> None:
    runner = remote_file(
        ENV_PATH,
        "DATABASE_NAME=n\nDATABASE_USER=u\nDATABASE_PASSWORD=p\n"
        "DATABASE_HOST=h\nDATABASE_PORT=3306\nA=1\n",
    )
    context = make_context(runner, tmp_path)
    read_remote_credentials(context)
    read_remote_values(context, ["A"])
    assert len(runner.calls) == 1


def test_the_remote_read_is_sensitive(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    fetch_remote_env_file(make_context(runner, tmp_path))
    assert runner.invocations[0].sensitive is True


def test_a_value_does_not_reach_a_repr() -> None:
    # A dataclass repr is what a traceback or a debug line prints.
    values = RemoteValues(
        path=ENV_PATH, text={"APP_KEY": "s3cret"}, missing=[], multi_line=[], references=[]
    )
    assert "s3cret" not in repr(values)


# --- check_pull -------------------------------------------------------------


def with_local_file(root: Path, name: str = ".env") -> Path:
    (root / name).write_text("", encoding="utf-8")
    return root


def refusal(context: ExecutionContext) -> OperationError:
    with pytest.raises(OperationError) as caught:
        check_pull(context)
    return caught.value


def test_an_empty_list_makes_no_call(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    check_pull(make_context(runner, tmp_path))
    check_pull(make_context(runner, tmp_path, env={"pull": []}))
    assert runner.calls == []


def test_it_validates_the_names_itself(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    with pytest.raises(ConfigError, match=r"env\.pull"):
        check_pull(make_context(runner, tmp_path, env={"pull": ["1BAD"]}))
    assert runner.calls == []


def test_everything_in_order_passes(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    check_pull(make_context(runner, with_local_file(tmp_path), env={"pull": ["A"]}))


def test_it_says_what_it_is_about_to_wait_for_only_when_it_will(tmp_path: Path) -> None:
    seen: list[str] = []
    runner = remote_file(ENV_PATH, "A=1\n")
    context = make_context(runner, with_local_file(tmp_path), env={"pull": ["A"]})
    check_pull(dataclasses.replace(context, progress=seen.append))
    assert seen == ["reading the values env.pull names"]

    seen.clear()
    check_pull(dataclasses.replace(make_context(runner, tmp_path), progress=seen.append))
    assert seen == []


def test_a_name_not_on_the_remote_is_refused_naming_it_and_the_file(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    context = make_context(runner, with_local_file(tmp_path), env={"pull": ["A", "B", "C"]})
    error = refusal(context)
    message = str(error)
    assert "B, C" in message
    assert ENV_PATH in message
    assert "web01.example" in message
    assert (doc_link(error) or "").endswith("#env-pull-variable-missing")
    assert "DDEV_SYNC_EXPORT_ENV" not in message


def test_a_multi_line_value_is_refused_with_its_own_slug(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, 'PEM="line1\nline2"\n')
    context = make_context(runner, with_local_file(tmp_path), env={"pull": ["PEM"]})
    error = refusal(context)
    assert "PEM" in str(error)
    assert ENV_PATH in str(error)
    assert (doc_link(error) or "").endswith("#env-pull-value-has-a-line-break")


def test_every_problem_comes_in_one_message_under_the_first_ones_slug(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, 'PEM="line1\nline2"\n')
    error = refusal(make_context(runner, tmp_path, env={"pull": ["PEM", "GONE"]}))
    message = str(error)
    assert "GONE" in message
    assert "PEM" in message
    assert ".env.example" in message
    assert (doc_link(error) or "").endswith("#env-pull-variable-missing")


def test_a_line_break_goes_first_when_nothing_is_missing(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, 'PEM="line1\nline2"\n')
    error = refusal(make_context(runner, tmp_path, env={"pull": ["PEM"]}))
    assert ".env.example" in str(error)
    assert (doc_link(error) or "").endswith("#env-pull-value-has-a-line-break")


def test_no_value_is_named(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, 'KEPT=s3cret\nPEM="s3cret2\ns3cret3"\nOTHER=s3cret4\n')
    message = str(refusal(make_context(runner, tmp_path, env={"pull": ["KEPT", "PEM", "GONE"]})))
    assert "s3cret" not in message


def test_no_value_is_named_from_the_handover(tmp_path: Path) -> None:
    runner = remote_file(HANDOVER_PATH, handover("KEPT=s3cret", "DDEV_SYNC_SKIPPED=PEM"))
    context = docker_context(runner, tmp_path, env={"pull": ["KEPT", "PEM", "GONE"]})
    assert "s3cret" not in str(refusal(context))


def test_under_docker_reading_the_handover_it_says_how_to_allow_the_name(tmp_path: Path) -> None:
    runner = remote_file(HANDOVER_PATH, handover("A=1"))
    context = docker_context(runner, with_local_file(tmp_path), env={"pull": ["B"]})
    message = str(refusal(context))
    assert HANDOVER_PATH in message
    assert "DDEV_SYNC_EXPORT_ENV" in message
    assert "all" in message
    assert "wins" not in message


def test_under_docker_reading_another_file_it_says_the_mounted_file_wins(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    context = docker_context(runner, with_local_file(tmp_path), env={"pull": ["B"]})
    message = str(refusal(context))
    assert ENV_PATH in message
    assert "mounted file wins" in message
    assert "DDEV_SYNC_EXPORT_ENV" not in message


def test_a_skipped_value_from_the_handover_is_refused_as_a_line_break(tmp_path: Path) -> None:
    runner = remote_file(HANDOVER_PATH, handover("DDEV_SYNC_SKIPPED=PEM"))
    context = docker_context(runner, with_local_file(tmp_path), env={"pull": ["PEM"]})
    error = refusal(context)
    assert "PEM" in str(error)
    assert (doc_link(error) or "").endswith("#env-pull-value-has-a-line-break")


def test_no_local_file_and_no_example_is_refused(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    error = refusal(make_context(runner, tmp_path, env={"pull": ["A"]}))
    assert ".env.local, .env" in str(error)
    assert ".env.example" in str(error)
    assert (doc_link(error) or "").endswith("#env-pull-no-example-file")


def test_a_missing_local_file_with_an_example_passes(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    check_pull(make_context(runner, with_local_file(tmp_path, ".env.example"), env={"pull": ["A"]}))


def test_the_candidates_are_the_ones_env_file_names(tmp_path: Path) -> None:
    runner = remote_file(ENV_PATH, "A=1\n")
    context = make_context(
        runner, with_local_file(tmp_path, ".env"), env={"pull": ["A"], "file": ".env.app"}
    )
    assert ".env.app" in str(refusal(context))
    check_pull(
        make_context(
            runner,
            with_local_file(tmp_path, ".env.app"),
            env={"pull": ["A"], "file": [".env.app"]},
        )
    )


def test_a_name_env_set_keeps_cannot_refuse_the_run(tmp_path: Path) -> None:
    # Its value would never be used, so it does not matter that the remote has none.
    runner = remote_file(ENV_PATH, "B=1\n")
    context = make_context(
        runner, with_local_file(tmp_path), env={"pull": ["A", "B"], "set": {"A": "x"}}
    )
    check_pull(context)


def test_a_name_env_set_keeps_makes_no_remote_read(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    context = make_context(
        runner, with_local_file(tmp_path), env={"pull": ["A"], "set": {"A": "x"}}
    )
    seen: list[str] = []
    check_pull(dataclasses.replace(context, progress=seen.append))
    assert runner.calls == []
    assert seen == []


def test_with_only_names_env_set_keeps_the_step_still_needs_a_file(tmp_path: Path) -> None:
    # `env.pull` is what lets the step create a file, so it answers for there being
    # one to create from, whether or not there is anything to read.
    context = make_context(FakeCommandRunner(), tmp_path, env={"pull": ["A"], "set": {"A": "x"}})
    assert (doc_link(refusal(context)) or "").endswith("#env-pull-no-example-file")


def test_a_directory_is_no_local_file_and_no_example(tmp_path: Path) -> None:
    (tmp_path / ".env").mkdir()
    (tmp_path / ".env.example").mkdir()
    runner = remote_file(ENV_PATH, "A=1\n")
    error = refusal(make_context(runner, tmp_path, env={"pull": ["A"]}))
    assert (doc_link(error) or "").endswith("#env-pull-no-example-file")


def test_the_match_is_against_real_directory_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a case-insensitive filesystem a probe for `.env` would find `.ENV`;
    # `_find_env_file` compares names so the file it rewrites is exact, and this
    # has to agree with it. Faked, because the test filesystem may fold or not.
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == ".env" or real_is_file(self))
    (tmp_path / ".ENV").write_text("", encoding="utf-8")
    runner = remote_file(ENV_PATH, "A=1\n")
    context = make_context(runner, tmp_path, env={"pull": ["A"]})
    assert (doc_link(refusal(context)) or "").endswith("#env-pull-no-example-file")
