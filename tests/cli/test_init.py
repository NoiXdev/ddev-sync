from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from ddev_sync.cli import init
from ddev_sync.cli.context import build_context
from ddev_sync.cli.main import main
from ddev_sync.cli.prompts import DEPLOYER_SOURCE, MANUAL_SOURCE
from ddev_sync.config.loader import GLOBAL_LABEL, PROJECT_LABEL
from ddev_sync.config.writer import WriteRefused
from ddev_sync.operations.base import OperationError
from ddev_sync.operations.preflight import PreflightOperation, PreflightReport

PROFILE_ROOT = Path(__file__).parent.parent.parent / "profiles"

EXISTING = (
    "version: 1\n"
    "# Where production lives.\n"
    "targets:\n"
    "  production:\n"
    "    host: web01.example\n"
    "    user: deploy\n"
    "    path: /srv/app/\n"
    "    stage: production\n"
)

CONNECTION: dict[str, object] = {
    "host": "web09.example",
    "user": "sync",
    "port": 2222,
    "path": "/srv/new/",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".ddev").mkdir(parents=True)
    (root / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: php\ndocroot: public\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def project_file(project: Path) -> Path:
    return project / ".ddev" / "ddev-sync.yaml"


@pytest.fixture
def local_file(project: Path) -> Path:
    return project / ".ddev" / "ddev-sync.local.yaml"


@pytest.fixture
def global_file(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".ddev" / "ddev-sync" / "config.yaml"


def invoke(project: Path, tmp_path: Path, *extra: str) -> int:
    return main(
        [
            "--project-root",
            str(project),
            "--home",
            str(tmp_path / "home"),
            "--profile-root",
            str(PROFILE_ROOT),
            *extra,
            "init",
        ]
    )


def _queue(answers: Iterable[Any], what: str, calls: list[tuple[Any, ...]]) -> Callable[..., Any]:
    """A stand-in question that gives the scripted answers in order, then fails.

    Every call's positional arguments are appended to `calls`, so a test can say
    what the user was shown.
    """
    remaining = iter(answers)

    def ask(*args: Any, **_kwargs: object) -> Any:
        calls.append(args)
        try:
            return next(remaining)
        except StopIteration:
            raise AssertionError(f"{what} was asked more often than the test expected") from None

    return ask


def script(
    monkeypatch: pytest.MonkeyPatch,
    *,
    detection: Iterable[bool | None] = (True,),
    names: Iterable[str | None] = ("production",),
    replacements: Iterable[bool | None] = (),
    source: Iterable[str | None] = (MANUAL_SOURCE,),
    connection: Iterable[dict[str, object] | None] = (CONNECTION,),
    stage: Iterable[str | None] = ("production",),
) -> dict[str, list[tuple[Any, ...]]]:
    """Replace every question `init` asks with scripted answers.

    Returns, per question, the arguments it was asked with.
    """
    asked: dict[str, list[tuple[Any, ...]]] = {}
    for attribute, answers in (
        ("confirm_detection", detection),
        ("ask_target_name", names),
        ("confirm_replacement", replacements),
        ("choose_connection_source", source),
        ("ask_connection", connection),
        ("ask_stage", stage),
    ):
        asked[attribute] = []
        monkeypatch.setattr(init, attribute, _queue(answers, attribute, asked[attribute]))
    return asked


def report_green(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make `doctor` pass, and return the host of every target it was run against."""
    hosts: list[str] = []

    def check(_self: object, context: Any) -> PreflightReport:
        hosts.append(context.target.host)
        return PreflightReport(True, True, 50 * 1024**3)

    monkeypatch.setattr(PreflightOperation, "check", check)
    return hosts


def test_every_question_answered_writes_the_target_and_runs_doctor(
    project: Path,
    project_file: Path,
    global_file: Path,
    local_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_file.write_text("version: 1\n# machine-only settings\n", encoding="utf-8")
    local_before = local_file.read_bytes()
    script(monkeypatch)
    hosts = report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    written = project_file.read_text(encoding="utf-8")
    assert written.startswith("version: 1\n")
    assert "web09.example" in written
    # `ask_connection` hands the port over as a number and it stays one in the file.
    assert "port: 2222\n" in written
    assert "stage: production" in written
    # The check ran against the target that was just written.
    assert hosts == ["web09.example"]
    # Exactly one file changes per run: the other two layers are left alone.
    assert local_file.read_bytes() == local_before
    assert not global_file.exists()
    output = capsys.readouterr().out
    assert f"Wrote the target 'production' to {PROJECT_LABEL}." in output
    # Said once the outcome is known, so after the checklist.
    assert output.index("ssh connection") < output.index("Wrote the target")


def test_the_success_message_says_the_file_is_committed_and_where_hosts_can_go(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    output = capsys.readouterr().out
    assert f"{PROJECT_LABEL} is committed with the project." in output
    assert f"move the target to {GLOBAL_LABEL}." in output


def test_the_name_is_stripped_and_the_comments_around_it_survive(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_file.write_text(EXISTING, encoding="utf-8")
    script(monkeypatch, names=("  staging ",), stage=("staging",))
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    written = project_file.read_text(encoding="utf-8")
    assert written.startswith(EXISTING)
    assert "\n  staging:\n" in written
    assert "stage: staging\n" in written


def test_a_declined_detection_writes_nothing_and_names_the_config_keys(
    project: Path,
    project_file: Path,
    global_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every later question is an empty script, which fails if it is asked.
    script(monkeypatch, detection=(False,), names=(), source=(), connection=())
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "project.type" in error
    assert "project.version" in error
    assert f"in {PROJECT_LABEL}" in error
    assert not project_file.exists()
    assert not global_file.exists()


def test_the_detection_question_names_the_profile_the_version_and_where_each_came_from(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = script(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    # `php` is no DDEV type with a profile of its own, and nothing detects a
    # version for the generic one; both facts are said rather than left out.
    assert asked["confirm_detection"] == [
        ("generic", "generic fallback (no profile for DDEV type 'php'); no version detected")
    ]


def test_the_type_and_version_flags_reach_the_question_and_the_check(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = script(monkeypatch)
    report_green(monkeypatch)
    built: list[dict[str, object]] = []
    real = build_context

    def spy(**kwargs: Any) -> Any:
        built.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(init, "build_context", spy)
    assert invoke(project, tmp_path, "--type", "laravel", "--framework-version", "11") == 0
    assert asked["confirm_detection"] == [
        ("laravel 11", "--type flag; version from --framework-version flag")
    ]
    # The context the check runs in is built from the same answers the question was.
    assert len(built) == 1
    assert built[0]["type_flag"] == "laravel"
    assert built[0]["framework_version_flag"] == "11"


@pytest.mark.parametrize(
    ("answers", "existing"),
    [
        ({"detection": (None,), "names": (), "source": (), "connection": ()}, False),
        ({"names": (None,), "source": (), "connection": ()}, False),
        ({"replacements": (None,), "source": (), "connection": ()}, True),
        ({"source": (None,), "connection": ()}, False),
        ({"connection": (None,)}, False),
        ({"stage": (None,)}, False),
    ],
    ids=["detection", "name", "replacement", "source", "connection", "stage"],
)
def test_a_missing_terminal_stops_init_with_nothing_written(
    answers: dict[str, Any],
    existing: bool,
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if existing:
        project_file.write_text(EXISTING, encoding="utf-8")
    script(monkeypatch, **answers)
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "terminal" in error
    assert "by hand" in error
    # Only the flags that exist are named; there is no flag that answers a question.
    assert "--" not in error
    if existing:
        assert project_file.read_text(encoding="utf-8") == EXISTING
    else:
        assert not project_file.exists()


def test_a_target_the_file_already_defines_is_replaced_when_the_user_agrees(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_file.write_text(EXISTING, encoding="utf-8")
    asked = script(monkeypatch, replacements=(True,))
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    # What the user is shown is what is there now, in the file the question names.
    assert asked["confirm_replacement"] == [
        ("production", PROJECT_LABEL, "deploy@web01.example:/srv/app/")
    ]
    written = project_file.read_text(encoding="utf-8")
    assert "web01.example" not in written
    assert "web09.example" in written
    assert "# Where production lives." in written


def test_a_declined_replacement_asks_again_and_leaves_the_first_target_alone(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_file.write_text(EXISTING, encoding="utf-8")
    asked = script(
        monkeypatch,
        names=("production", "staging"),
        replacements=(False,),
        stage=("staging",),
    )
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    written = project_file.read_text(encoding="utf-8")
    # The first target is byte for byte what it was; the second follows it.
    assert written.startswith(EXISTING)
    assert "\n  staging:\n    host: web09.example\n" in written
    # The second question offers a name that is free, so Enter does not collide again.
    assert asked["ask_target_name"] == [("production",), ("production-2",)]
    assert "Nothing was replaced" in capsys.readouterr().out


def test_the_suggested_name_skips_the_numbers_the_file_already_uses(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_file.write_text(
        EXISTING + "  production-2:\n    host: other.example\n    stage: dev\n", encoding="utf-8"
    )
    asked = script(
        monkeypatch,
        names=("production", "other"),
        replacements=(False,),
    )
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert asked["ask_target_name"] == [("production",), ("production-3",)]


def test_a_target_only_another_file_defines_is_not_asked_about(
    project: Path,
    project_file: Path,
    global_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Choosing the project file adds a `production` there; nothing is replaced
    # in it, so there is nothing to confirm. `confirm_replacement` has no
    # scripted answer and would fail the test if it were asked.
    global_file.parent.mkdir(parents=True)
    global_file.write_text(EXISTING, encoding="utf-8")
    script(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert global_file.read_text(encoding="utf-8") == EXISTING
    assert "web09.example" in project_file.read_text(encoding="utf-8")


def test_three_declined_replacements_end_the_command_with_nothing_changed(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_file.write_text(EXISTING, encoding="utf-8")
    script(
        monkeypatch,
        names=("production",) * 3,
        replacements=(False,) * 3,
        source=(),
        connection=(),
    )
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "nothing was written" in error
    assert "3 times" in error
    assert project_file.read_text(encoding="utf-8") == EXISTING


def test_a_refused_write_prints_the_block_and_leaves_the_file_as_it_was(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A flow-style `targets` is not found by the line scan, so a second
    # `targets:` would be appended beside it; the writer's own check refuses.
    original = "version: 1\ntargets: {}\n"
    project_file.write_text(original, encoding="utf-8")
    script(monkeypatch)
    assert invoke(project, tmp_path) == 1
    output = capsys.readouterr().out
    assert "nothing was changed" in output
    assert f"Add the target to {PROJECT_LABEL} yourself, under `targets:`" in output
    assert "  production:\n    host: web09.example\n" in output
    assert project_file.read_bytes() == original.encode()


def test_a_refusal_the_writer_raises_is_reported_the_same_way(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(**_kwargs: object) -> None:
        raise WriteRefused("it would have changed something else")

    script(monkeypatch)
    monkeypatch.setattr(init, "write_target", refuse)
    assert invoke(project, tmp_path) == 1
    output = capsys.readouterr().out
    assert "it would have changed something else" in output
    assert f"Add the target to {PROJECT_LABEL} yourself" in output
    assert "Wrote" not in output


def test_a_failing_check_leaves_the_file_and_exits_non_zero(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script(monkeypatch)
    monkeypatch.setattr(
        PreflightOperation, "check", lambda _self, _context: PreflightReport(False, False, 1024)
    )
    assert invoke(project, tmp_path) == 1
    assert "web09.example" in project_file.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "FAIL" in output
    # A failing check is not a success, so the success line is not printed.
    assert "Wrote" not in output
    assert f"The target stays in {PROJECT_LABEL}" in output
    assert "ddev sync --target production doctor" in output


def test_an_unresolvable_profile_prints_no_success_line_and_names_the_file(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script(monkeypatch)
    report_green(monkeypatch)
    # `confirm_detection` is scripted, so the made-up type is only found out
    # when the context is built, after the file is written.
    assert invoke(project, tmp_path, "--type", "nonesuch") == 1
    captured = capsys.readouterr()
    assert "Wrote" not in captured.out
    assert f"is in {PROJECT_LABEL}, but it could not be checked" in captured.err
    assert "nonesuch" in captured.err
    assert "ddev sync --target production doctor" in captured.err
    # The target is kept: the user edits a file, they do not start over.
    assert "web09.example" in project_file.read_text(encoding="utf-8")


def test_a_check_that_raises_is_reported_like_one_that_could_not_start(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_self: object, _context: object) -> PreflightReport:
        raise OperationError("unexpected output from the remote probe")

    script(monkeypatch)
    monkeypatch.setattr(PreflightOperation, "check", explode)
    assert invoke(project, tmp_path) == 1
    captured = capsys.readouterr()
    assert "Wrote" not in captured.out
    assert f"is in {PROJECT_LABEL}" in captured.err
    assert "unexpected output" in captured.err


def test_a_stronger_layer_that_redefines_the_target_is_named_and_is_no_success(
    project: Path,
    project_file: Path,
    local_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_file.write_text(
        "version: 1\ntargets:\n  production:\n    host: elsewhere.example\n", encoding="utf-8"
    )
    script(monkeypatch)
    hosts = report_green(monkeypatch)
    assert invoke(project, tmp_path) == 1
    captured = capsys.readouterr()
    # The check really did run against the other definition, which is the point.
    assert hosts == ["elsewhere.example"]
    assert "Wrote" not in captured.out
    assert ".ddev/ddev-sync.local.yaml" in captured.err
    assert "not against what was just written" in captured.err
    # What was written is kept, and the local file is not touched.
    assert "web09.example" in project_file.read_text(encoding="utf-8")
    assert "elsewhere.example" in local_file.read_text(encoding="utf-8")


def test_another_layer_that_defines_the_same_target_identically_is_no_conflict(
    project: Path,
    global_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_file.parent.mkdir(parents=True)
    global_file.write_text(
        "version: 1\ntargets:\n  production:\n    host: web09.example\n"
        "    user: sync\n    port: 2222\n    path: /srv/new/\n    stage: production\n",
        encoding="utf-8",
    )
    script(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert "Wrote the target" in capsys.readouterr().out


def test_an_interrupted_check_still_says_which_file_holds_the_target(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(_self: object, _context: object) -> PreflightReport:
        raise KeyboardInterrupt

    script(monkeypatch)
    monkeypatch.setattr(PreflightOperation, "check", interrupt)
    # `main` ends an interrupt with 130, and that is not changed by saying more.
    assert invoke(project, tmp_path) == 130
    captured = capsys.readouterr()
    assert "Wrote" not in captured.out
    assert f"is in {PROJECT_LABEL}, but the check did not finish" in captured.out
    assert "ddev sync --target production doctor" in captured.out
    assert "web09.example" in project_file.read_text(encoding="utf-8")


def test_an_unexpected_error_in_the_check_still_says_which_file_holds_the_target(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_self: object, _context: object) -> PreflightReport:
        raise ValueError("something nobody foresaw")

    script(monkeypatch)
    monkeypatch.setattr(PreflightOperation, "check", fail)
    assert invoke(project, tmp_path) == 1
    captured = capsys.readouterr()
    assert "Wrote" not in captured.out
    assert f"is in {PROJECT_LABEL}, but the check did not finish" in captured.out
    assert "something nobody foresaw" in captured.err


def test_a_passing_check_does_not_say_the_target_stays(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert "The target stays in" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("recipe", "offered"),
    [(None, False), ("deploy.php", True), ("deploy.yaml", True)],
)
def test_deployer_is_offered_only_when_the_project_has_a_recipe(
    recipe: str | None,
    offered: bool,
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if recipe is not None:
        (project / recipe).write_text("", encoding="utf-8")
    script(monkeypatch)
    report_green(monkeypatch)
    seen: list[bool] = []

    def choose(deployer_available: bool) -> str:
        seen.append(deployer_available)
        return MANUAL_SOURCE

    monkeypatch.setattr(init, "choose_connection_source", choose)
    assert invoke(project, tmp_path) == 0
    assert seen == [offered]


def test_choosing_deployer_says_it_is_not_available_yet_and_writes_nothing(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Task 4 replaces this branch, and this test goes with it.
    script(monkeypatch, source=(DEPLOYER_SOURCE,), connection=(), stage=())
    assert invoke(project, tmp_path) == 1
    assert "not available yet" in capsys.readouterr().err
    assert not project_file.exists()


def test_outside_a_ddev_project_init_names_the_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(tmp_path / "nowhere", tmp_path) == 1
    assert ".ddev/config.yaml" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [
        ("--dry-run",),
        ("--yes",),
        ("--target", "production"),
        ("--steps", "db"),
        ("--features", "node-build"),
        ("--no-features",),
        ("--only-feature-setup",),
    ],
)
def test_options_init_would_ignore_are_refused_and_nothing_is_written(
    flags: tuple[str, ...],
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script(monkeypatch, detection=(), names=(), source=(), connection=(), stage=())
    assert invoke(project, tmp_path, *flags) == 2
    assert f"{flags[0]} cannot be combined with 'init'" in capsys.readouterr().err
    assert not project_file.exists()


def test_the_option_check_leaves_other_subcommands_alone(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_green(monkeypatch)
    project_file = project / ".ddev" / "ddev-sync.yaml"
    project_file.write_text(EXISTING, encoding="utf-8")
    assert (
        main(
            [
                "--project-root",
                str(project),
                "--home",
                str(tmp_path / "home"),
                "--profile-root",
                str(PROFILE_ROOT),
                "--target",
                "production",
                "doctor",
            ]
        )
        == 0
    )
