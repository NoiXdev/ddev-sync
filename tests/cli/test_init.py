import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml

from ddev_sync.cli import init
from ddev_sync.cli.context import build_context
from ddev_sync.cli.main import main
from ddev_sync.cli.prompts import (
    DEPLOYER_SOURCE,
    ENV_FILE_NONE,
    EXPLICIT_SOURCE,
    MANUAL_SOURCE,
    REMOTE_ENV_NONE,
    WP_CLI_SOURCE,
)
from ddev_sync.config.loader import GLOBAL_LABEL, PROJECT_LABEL
from ddev_sync.config.writer import WriteRefused
from ddev_sync.operations.base import OperationError
from ddev_sync.operations.preflight import PreflightOperation, PreflightReport
from ddev_sync.transport.fake import FakeCommandRunner

CREDENTIALS = {
    "name": "acme_prod",
    "user": "acme",
    "password": "s3cret",
    "host": "db.internal",
    "port": "3306",
}

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
    production_url: Iterable[str | None] = (),
    names: Iterable[str | None] = ("production",),
    replacements: Iterable[bool | None] = (),
    source: Iterable[str | None] = (MANUAL_SOURCE,),
    connection: Iterable[dict[str, object] | None] = (CONNECTION,),
    stage: Iterable[str | None] = ("production",),
    deployer_host: Iterable[str | None] = (),
    remote_env_file: Iterable[str | None] = (),
    credentials_source: Iterable[str | None] = (),
    credentials: Iterable[dict[str, str] | None] = (),
) -> dict[str, list[tuple[Any, ...]]]:
    """Replace every question `init` asks with scripted answers.

    Returns, per question, the arguments it was asked with. `production_url`
    defaults to an empty script: most fixtures here build a `php`-typed
    project, which resolves to the generic profile and never asks it, so a
    test that does not expect the question fails loudly if it fires anyway.
    `remote_env_file` defaults the same way - the credentials-fix question
    (task 5) only fires when `PreflightOperation.check` reports a red
    `credentials_ok` (see `stub_checks`), which none of the tests that omit it
    ever scripts. `credentials_source` and `credentials` (task 6) answer the
    override behind `REMOTE_ENV_NONE`, and default empty for the same reason.
    """
    asked: dict[str, list[tuple[Any, ...]]] = {}
    for attribute, answers in (
        ("confirm_detection", detection),
        ("ask_production_url", production_url),
        ("ask_target_name", names),
        ("confirm_replacement", replacements),
        ("choose_connection_source", source),
        ("ask_connection", connection),
        ("ask_stage", stage),
        ("choose_deployer_host", deployer_host),
        ("choose_remote_env_file", remote_env_file),
        ("choose_credentials_source", credentials_source),
        ("ask_credentials", credentials),
    ):
        asked[attribute] = []
        monkeypatch.setattr(init, attribute, _queue(answers, attribute, asked[attribute]))
    return asked


def _answer_the_target_questions(
    monkeypatch: pytest.MonkeyPatch, *, production_url: Iterable[str | None] = ()
) -> dict[str, list[tuple[Any, ...]]]:
    """Script ordinary answers for the target itself, for a test that is
    really about `_ask_project_settings` and does not want to repeat
    `script`'s defaults to get there.
    """
    return script(monkeypatch, production_url=production_url)


def _wordpress(project: Path) -> None:
    """Overwrite the project fixture's `.ddev/config.yaml` to detect as WordPress.

    The WordPress profile's `rewrite-site-url` rule carries the production URL
    placeholder in its `from:` (profiles/wordpress.yaml), so a project that
    detects this way is one `_wants_production_url` says yes to.
    """
    (project / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: wordpress\ndocroot: public\n", encoding="utf-8"
    )


def _generic(project: Path) -> None:
    """Overwrite the project fixture's `.ddev/config.yaml` to detect as generic.

    Spelled out explicitly rather than relying on the fixture's own default
    (which already resolves here): the generic profile has no WordPress
    `wp_cli` choice and no production-url placeholder, so it is the plain
    case the credentials override (task 6) is tested against, and a change to
    the fixture's default must not silently turn these tests into something
    else.
    """
    (project / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: php\ndocroot: public\n", encoding="utf-8"
    )


def _laravel(project: Path) -> None:
    """Overwrite the project fixture's `.ddev/config.yaml` to detect as Laravel.

    The Laravel profile's rules never mention the placeholder
    (profiles/laravel.yaml), so a project that detects this way is one
    `_wants_production_url` says no to. It is also the profile these tests use
    to reach the env question: `env.strategy: patch_keys` with `env.set`
    non-empty (profiles/laravel.yaml), unlike `php` (generic) or `wordpress`,
    both `strategy: none`.
    """
    (project / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: laravel\ndocroot: public\n", encoding="utf-8"
    )


def _never_called(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("should not have been asked")


def _laravel_with_horizon(project: Path) -> None:
    """`_laravel`, plus a `.env` and a `composer.lock` `horizon.yaml`'s
    `detect:` really matches.

    The `.env` keeps the unrelated env question (fired by `_laravel`'s own
    profile, see `_laravel`'s docstring) from also consuming the scripted
    answers meant for the feature question. The `composer.lock` is real
    detection, not a patched resolver: `features/detect.py::_composer_has`
    reads this file off disk exactly as a project's own lockfile would.
    """
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _write_composer_lock(project, "laravel/horizon")


def _laravel_with_horizon_and_livewire(project: Path) -> None:
    """`_laravel_with_horizon`, plus a second feature `livewire.yaml` matches."""
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _write_composer_lock(project, "laravel/horizon", "livewire/livewire")


def _write_composer_lock(project: Path, *packages: str) -> None:
    (project / "composer.lock").write_text(
        json.dumps({"packages": [{"name": name} for name in packages]}),
        encoding="utf-8",
    )


DEP_CONFIG = json.dumps(
    {
        name: {
            "hostname": f"{name}.example",
            "remote_user": "deploy",
            "port": None,
            "deploy_path": f"/srv/{name}",
            "current_path": f"/srv/{name}/current",
        }
        for name in ("web-1", "staging")
    }
)


def with_runner(monkeypatch: pytest.MonkeyPatch, **scripted: Any) -> FakeCommandRunner:
    """Make `main` hand `init` a fake runner whose `docker exec` answers as scripted."""
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], **scripted)
    monkeypatch.setattr("ddev_sync.cli.main.SubprocessRunner", lambda: runner)
    return runner


def report_green(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make `doctor` pass, and return the host of every target it was run against."""
    hosts: list[str] = []

    def check(_self: object, context: Any) -> PreflightReport:
        hosts.append(context.target.host)
        return PreflightReport(True, True, 50 * 1024**3)

    monkeypatch.setattr(PreflightOperation, "check", check)
    return hosts


def _report(
    credentials_ok: bool | None, *, ssh_ok: bool = True, remote_path_ok: bool = True
) -> PreflightReport:
    """A `PreflightReport` naming only what the credentials-fix tests vary."""
    return PreflightReport(ssh_ok, remote_path_ok, 50 * 1024**3, credentials_ok=credentials_ok)


def stub_checks(monkeypatch: pytest.MonkeyPatch, *reports: PreflightReport) -> None:
    """Replace `PreflightOperation.check` with one report per call, repeating the last.

    `_fix_credentials`'s own guard and `doctor_command` each call `check()`
    once per `init` run - R1 of `progress.md`'s ruling that `init` must go
    through `check()` rather than the private probes it is built from, so
    `report_green`'s stub still governs every test that does not call this
    one. A test proving the fix actually helped scripts a red report and then
    a green one; a test where nothing about credentials is in question
    scripts a single report, which this function then answers both calls
    with - the same way `report_green`'s one-report stub already does for
    every pre-existing test.
    """
    sequence = list(reports)

    def check(_self: object, _context: object) -> PreflightReport:
        return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    monkeypatch.setattr(PreflightOperation, "check", check)


def _runner_whose_remote_listing_is(
    monkeypatch: pytest.MonkeyPatch, entries: list[str]
) -> FakeCommandRunner:
    """Install a fake runner whose `ls -1a` of the target's data root answers `entries`.

    `PreflightOperation.check` is always stubbed (`stub_checks`) in a test
    that calls this, so the one real command any of them drives through the
    runner is `_remote_env_entries`'s listing - the probe and the credentials
    fetch `check()` would otherwise also run never reach a real runner at all.
    """
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], stdout="".join(f"{entry}\n" for entry in entries))
    monkeypatch.setattr("ddev_sync.cli.main.SubprocessRunner", lambda: runner)
    return runner


def _runner_where_the_remote_env_is(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> FakeCommandRunner:
    return _runner_whose_remote_listing_is(monkeypatch, [name])


def _symfony(project: Path) -> None:
    """Overwrite the project fixture's `.ddev/config.yaml` to detect as Symfony.

    `profiles/symfony.yaml` sets `env.file: [.env.local, .env]` - textually
    identical to `remote_env_candidates`'s own unconfigured default - which is
    what lets a test assert that fallback list without separately configuring
    it to get there. `_laravel`'s profile sets `env.file: [.env]` instead, so
    it cannot be used for that.
    """
    (project / ".ddev" / "config.yaml").write_text(
        "name: acme\ntype: symfony\ndocroot: public\n", encoding="utf-8"
    )


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
    # The check ran against the target that was just written - twice: the
    # credentials-fix guard's own `check()` (task 5), then `doctor`'s.
    assert hosts == ["web09.example", "web09.example"]
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
    # `--type laravel` resolves to a profile whose `env.set` is non-empty
    # (profiles/laravel.yaml), unlike the `php` the fixture's `.ddev/config.yaml`
    # declares; an `.env` file keeps this test about flag plumbing, not a
    # second question it does not script.
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
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


def test_an_unresolvable_profile_stops_init_before_any_question_or_write(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Before this task, `confirm_detection` ran its own, separate detection,
    # so a made-up type was only found out when the context was built for
    # `doctor`, after the target was written. `init` now resolves the project
    # once, with `resolve_project`, before asking anything - so an unresolvable
    # profile is caught there instead, before the detection question and
    # before any write. No question is scripted: one asked would fail the test.
    script(monkeypatch, detection=(), names=(), source=(), connection=())
    assert invoke(project, tmp_path, "--type", "nonesuch") == 1
    captured = capsys.readouterr()
    assert "Wrote" not in captured.out
    assert "unknown profile 'nonesuch'" in captured.err
    assert not project_file.exists()


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
    # The check really did run against the other definition, which is the
    # point - twice: the credentials-fix guard's own `check()`, then `doctor`'s.
    assert hosts == ["elsewhere.example", "elsewhere.example"]
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


def test_a_picked_deployer_host_is_written_as_a_block_naming_the_alias(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "deploy.php").write_text("", encoding="utf-8")
    runner = with_runner(monkeypatch, stdout=DEP_CONFIG)
    asked = script(
        monkeypatch,
        source=(DEPLOYER_SOURCE,),
        connection=(),
        deployer_host=("staging",),
        # The alias says "staging" and the answer says otherwise: the stage is
        # asked, not read off the alias.
        stage=("production",),
    )
    hosts = report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    written = project_file.read_text(encoding="utf-8")
    assert "deployer:\n      host: staging\n" in written
    assert "stage: production" in written
    # The engine rejects a connection written beside the block, and resolving
    # at run time is the point of the block, so nothing was copied in.
    for copied in ("staging.example", "user:", "port:", "path:", "layout:"):
        assert copied not in written
    assert [line for line in written.splitlines() if line.strip().startswith("host:")] == [
        "      host: staging"
    ]
    # Every host Deployer declared was offered, in the order it printed them.
    assert asked["choose_deployer_host"] == [(["web-1", "staging"],)]
    assert len(asked["ask_stage"]) == 1
    # The check resolved the alias that was written, and `dep` ran only once
    # for the listing, both checks (the credentials-fix guard's, then
    # `doctor`'s) and the comparison with what was written.
    assert hosts == ["staging.example", "staging.example"]
    assert len(runner.calls) == 1
    assert f"Wrote the target 'production' to {PROJECT_LABEL}." in capsys.readouterr().out


def test_deployer_hosts_are_not_asked_about_when_the_connection_is_entered_by_hand(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "deploy.php").write_text("", encoding="utf-8")
    runner = with_runner(monkeypatch, stdout=DEP_CONFIG)
    script(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert runner.calls == []


@pytest.mark.parametrize(
    ("scripted", "said"),
    [
        ({"returncode": 1, "stderr": "PHP Fatal error in deploy.php"}, "deploy.php"),
        ({"returncode": 127, "stderr": "not found"}, "require-dev"),
        ({"stdout": "usage: dep [options]"}, "did not print JSON"),
    ],
)
def test_a_failing_dep_config_stops_init_with_nothing_written(
    scripted: dict[str, Any],
    said: str,
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Falling back to manual entry would write a target that is not the one
    # asked for, and the failure - a stopped web container, a recipe that
    # errors - is one every later run of that target would meet as well.
    (project / "deploy.php").write_text("", encoding="utf-8")
    with_runner(monkeypatch, **scripted)
    asked = script(monkeypatch, source=(DEPLOYER_SOURCE,), connection=(), stage=())
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "nothing was written" in error
    assert said in error
    assert "by hand" in error
    assert asked["choose_deployer_host"] == []
    assert not project_file.exists()


def test_a_deployer_that_declares_no_hosts_is_an_error_not_an_empty_question(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "deploy.php").write_text("", encoding="utf-8")
    with_runner(monkeypatch, stdout="{}")
    asked = script(monkeypatch, source=(DEPLOYER_SOURCE,), connection=(), stage=())
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "no hosts" in error
    assert "nothing was written" in error
    assert asked["choose_deployer_host"] == []
    assert not project_file.exists()


def test_no_terminal_at_the_host_question_is_the_no_terminal_error(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "deploy.php").write_text("", encoding="utf-8")
    with_runner(monkeypatch, stdout=DEP_CONFIG)
    asked = script(
        monkeypatch, source=(DEPLOYER_SOURCE,), connection=(), stage=(), deployer_host=(None,)
    )
    assert invoke(project, tmp_path) == 1
    assert "none is attached" in capsys.readouterr().err
    assert asked["ask_stage"] == []
    assert not project_file.exists()


def test_no_terminal_at_the_stage_question_after_a_deployer_host_writes_nothing(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "deploy.php").write_text("", encoding="utf-8")
    with_runner(monkeypatch, stdout=DEP_CONFIG)
    script(
        monkeypatch,
        source=(DEPLOYER_SOURCE,),
        connection=(),
        stage=(None,),
        deployer_host=("web-1",),
    )
    assert invoke(project, tmp_path) == 1
    assert "none is attached" in capsys.readouterr().err
    assert not project_file.exists()


def test_a_deployer_target_that_already_exists_is_described_as_the_block_it_is(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "deploy.php").write_text("", encoding="utf-8")
    project_file.write_text(
        "version: 1\ntargets:\n  production:\n    deployer: {host: old}\n    stage: production\n",
        encoding="utf-8",
    )
    with_runner(monkeypatch, stdout=DEP_CONFIG)
    script(
        monkeypatch,
        source=(DEPLOYER_SOURCE,),
        connection=(),
        replacements=(True,),
        deployer_host=("web-1",),
    )
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    written = project_file.read_text(encoding="utf-8")
    assert "host: web-1" in written
    assert "host: old" not in written


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


def test_init_asks_for_the_production_url_and_writes_it(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wordpress(project)
    _answer_the_target_questions(monkeypatch, production_url=("https://acme.example",))
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert loaded["sanitize"]["production_url"] == "https://acme.example"
    assert loaded["targets"]["production"]["host"] == "web09.example"


def test_init_does_not_ask_when_the_profile_uses_no_placeholder(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_laravel`'s profile never mentions the placeholder, so `production_url`
    # is left at `script`'s empty default: a call would fail the test. The
    # `.env` file keeps the unrelated env question - which this profile's
    # `env.set` would otherwise raise - from firing too.
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_does_not_ask_when_the_url_is_already_set(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wordpress(project)
    project_file.write_text(
        "version: 1\nsanitize:\n  production_url: https://set.example\n", encoding="utf-8"
    )
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_still_asks_when_the_configured_url_is_invalid(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Distinct from "already set": a configured value that `validate_production_url`
    # itself rejects does not count as supplied, so the question still fires.
    _wordpress(project)
    project_file.write_text(
        "version: 1\nsanitize:\n  production_url: not-a-url\n", encoding="utf-8"
    )
    asked = _answer_the_target_questions(monkeypatch, production_url=("https://acme.example",))
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert len(asked["ask_production_url"]) == 1


def test_init_treats_a_blanked_out_sanitize_block_as_nothing_configured(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A project may blank `sanitize:` out entirely to turn every rule off -
    # `config/merge.py`'s `_merge_value` treats a scalar (here `None`, from an
    # empty `sanitize:`) as replacing the profile's `sanitize:` dict wholesale,
    # not merging into it. `_setting`'s walk must not crash on the non-dict
    # node that produces partway through the path; it reads as "nothing
    # configured", the same as the key being absent, and the placeholder is
    # gone along with the rules that carried it, so the question is not asked.
    _wordpress(project)
    project_file.write_text("version: 1\nsanitize:\n", encoding="utf-8")
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_finds_the_placeholder_in_a_rules_sql(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review Focus 5: the placeholder may sit in `sql:` rather than in a
    # parameter, and the detection must not care which. The `.env` file keeps
    # the unrelated env question - which this profile's `env.set` would
    # otherwise raise - from firing too.
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "sanitize:\n"
        "  rules:\n"
        "  - name: x\n"
        "    sql: \"UPDATE t SET url = '${PRODUCTION_URL}'\"\n",
        encoding="utf-8",
    )
    asked = _answer_the_target_questions(monkeypatch, production_url=("https://a.example",))
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert len(asked["ask_production_url"]) == 1


def test_init_writes_nothing_when_the_name_question_runs_out(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Part A's promise, which the second write must not break: the URL answer
    # is collected before the target and written after it, so an `init` that
    # never reaches the write leaves the file alone.
    _wordpress(project)
    project_file.write_text(EXISTING, encoding="utf-8")
    script(
        monkeypatch,
        production_url=("https://acme.example",),
        names=("production",) * 3,
        replacements=(False,) * 3,
        source=(),
        connection=(),
    )
    assert invoke(project, tmp_path) == 1
    assert project_file.read_text(encoding="utf-8") == EXISTING


def test_no_terminal_at_the_production_url_question_stops_init_with_nothing_written(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wordpress(project)
    script(monkeypatch, production_url=(None,), names=(), source=(), connection=())
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "terminal" in error
    assert not project_file.exists()


def test_a_refusal_from_write_settings_names_the_rest_to_add_by_hand(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wordpress(project)
    _answer_the_target_questions(monkeypatch, production_url=("https://acme.example",))

    def refuse(**_kwargs: object) -> None:
        raise WriteRefused("it would have changed something else")

    monkeypatch.setattr(init, "write_settings", refuse)
    assert invoke(project, tmp_path) == 1
    output = capsys.readouterr().out
    assert "it would have changed something else" in output
    assert "The target 'production' was written." in output
    assert f"Add the rest to {PROJECT_LABEL} yourself" in output
    assert "sanitize.production_url: 'https://acme.example'" in output
    assert "Wrote" not in output
    # The target itself made it to disk; only the settings write was refused.
    assert "web09.example" in project_file.read_text(encoding="utf-8")


def test_init_writes_the_env_file_the_user_names(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env.ddev").write_text("APP_ENV=local\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_env_file", lambda looked_for, present: ".env.ddev")
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert yaml.safe_load(project_file.read_text(encoding="utf-8"))["env"]["file"] == [".env.ddev"]


def test_init_does_not_ask_when_the_env_file_exists(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_env_file", _never_called)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_does_not_ask_when_the_strategy_is_none(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wordpress(project)  # `env.strategy: none`, so there is no step to fail
    monkeypatch.setattr(init, "choose_env_file", _never_called)
    # Passed through `_answer_the_target_questions` rather than a direct
    # `monkeypatch.setattr` beside it: `script` re-patches `ask_production_url`
    # with its own (empty-by-default) queue, so a patch applied before that
    # call is clobbered the moment it runs.
    _answer_the_target_questions(monkeypatch, production_url=("https://a.example",))
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_offers_the_env_files_that_are_there(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env.example").write_text("APP_ENV=\n", encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def answer(looked_for: list[str], present: list[str]) -> str:
        seen.update(looked_for=looked_for, present=present)
        return ENV_FILE_NONE

    monkeypatch.setattr(init, "choose_env_file", answer)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    invoke(project, tmp_path)
    assert seen["looked_for"] == [".env"]
    assert seen["present"] == [".env.example"]


def test_init_writes_nothing_when_no_env_file_is_chosen(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _laravel(project)
    monkeypatch.setattr(init, "choose_env_file", lambda looked_for, present: ENV_FILE_NONE)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    invoke(project, tmp_path)
    assert "env" not in yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert "after the database has been imported" in capsys.readouterr().out


def test_a_directory_named_env_is_not_a_file(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review Focus 4: `_find_env_file` filters `iterdir()` by `is_file()`, so a
    # directory named `.env` is not the env file and the question must still
    # be asked - otherwise `init` reports a file the sync then cannot find.
    _laravel(project)
    (project / ".env").mkdir()
    asked: list[bool] = []

    def answer(looked_for: list[str], present: list[str]) -> str:
        asked.append(True)
        return ENV_FILE_NONE

    monkeypatch.setattr(init, "choose_env_file", answer)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    invoke(project, tmp_path)
    assert asked == [True]


def test_a_file_named_env_in_capitals_does_not_satisfy_the_lowercase_candidate(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The case `iterdir()` exists for (`_missing_env_file`'s own docstring):
    # a case-insensitive filesystem's `is_file()` would fold ".env" and
    # ".ENV" together and wrongly call the question answered. The directory
    # holds only ".ENV", so the question must still be asked.
    _laravel(project)
    (project / ".ENV").write_text("APP_ENV=local\n", encoding="utf-8")
    asked: list[bool] = []

    def answer(looked_for: list[str], present: list[str]) -> str:
        asked.append(True)
        return ENV_FILE_NONE

    monkeypatch.setattr(init, "choose_env_file", answer)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    invoke(project, tmp_path)
    assert asked == [True]


def test_a_non_iterable_env_file_setting_fails_the_way_the_engine_would(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_missing_env_file`'s docstring claims its two conditions are
    # `engine/plan.py::_env`'s own - but that function's guard between its two
    # conditions, which turns a non-str/non-list `env.file` into a `ConfigError`
    # sentence instead of a raw `TypeError`, had not been copied along with them.
    _laravel(project)
    project_file.write_text(
        "version: 1\nenv:\n  file: 7\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(init, "choose_env_file", _never_called)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "env.file must be a file name or a list of file names" in error
    assert "7" in error


def test_no_terminal_at_the_env_file_question_stops_init_with_nothing_written(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    monkeypatch.setattr(init, "choose_env_file", lambda looked_for, present: None)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "terminal" in error
    assert not project_file.exists()


def test_init_does_not_ask_when_env_set_is_configured_empty(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The second of `_env`'s own two conditions: a strategy other than `none`
    # with nothing configured to write is also no step to fail, independent
    # of whether any env file exists.
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\nenv:\n  strategy: patch_keys\n  set: {}\n", encoding="utf-8"
    )
    monkeypatch.setattr(init, "choose_env_file", _never_called)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_writes_nothing_when_every_feature_is_kept(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The rule that keeps detection alive: a feature added next month is still
    # found, because no `features:` key was written.
    _laravel_with_horizon(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: list(labels))
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert "features" not in yaml.safe_load(project_file.read_text(encoding="utf-8"))


def test_init_writes_the_kept_features_when_one_is_dropped(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel_with_horizon_and_livewire(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: ["horizon"])
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert yaml.safe_load(project_file.read_text(encoding="utf-8"))["features"] == ["horizon"]
    # A `features:` key freezes detection for this project, so `init` says so.
    assert "Feature detection is now off for this project" in capsys.readouterr().out


def test_init_writes_an_empty_list_when_all_are_dropped(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review Focus 3: `[]` is "none, deliberately" and must survive the write.
    _laravel_with_horizon(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: [])
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert yaml.safe_load(project_file.read_text(encoding="utf-8"))["features"] == []


def test_init_does_not_ask_when_nothing_was_detected(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_features", _never_called)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_does_not_ask_when_the_user_already_wrote_the_key(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The entries then came from that key, not from detection, so offering to
    # deselect them would be offering to rewrite an answer already given.
    _laravel_with_horizon(project)
    project_file.write_text("version: 1\nfeatures:\n- horizon\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_features", _never_called)
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_the_feature_question_shows_why_each_was_detected(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel_with_horizon(project)
    seen: dict[str, str] = {}
    monkeypatch.setattr(init, "choose_features", lambda labels: seen.update(labels) or list(labels))
    _answer_the_target_questions(monkeypatch)
    report_green(monkeypatch)
    invoke(project, tmp_path)
    assert "composer package" in seen["horizon"]


def test_no_terminal_at_the_feature_question_stops_init_with_nothing_written(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel_with_horizon(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: None)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "terminal" in error
    assert not project_file.exists()


# --- `init` offers a fix when the credentials row is red (task 5) ----------
#
# `PreflightOperation.check` is stubbed for every test below (`stub_checks`),
# never driven for real: `report_green`'s own stub already proves twenty other
# tests never call `_offer_remote_env_file` by accident, and reaching this
# code through a real probe would mean also faking the web-container and
# ssh-agent rows `check()` always runs - exposure this feature has nothing to
# do with. The one thing these tests do drive through a real `FakeCommandRunner`
# is `_remote_env_entries`'s `ls`, which is new code `check()` knows nothing
# about.


def test_init_writes_the_remote_env_file_the_user_names(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _runner_where_the_remote_env_is(monkeypatch, ".env.production")
    script(monkeypatch, remote_env_file=(".env.production",))
    # Red, then green: the second report is `doctor`'s own check, standing in
    # for the write having actually fixed the configuration.
    stub_checks(monkeypatch, _report(False), _report(True))
    assert invoke(project, tmp_path) == 0
    loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert loaded["database"]["env_file"] == [".env.production"]


def test_init_does_not_ask_when_the_credentials_already_resolve(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    script(monkeypatch, remote_env_file=())  # asking would exhaust the empty queue
    stub_checks(monkeypatch, _report(True))
    assert invoke(project, tmp_path) == 0


def test_init_offers_the_env_files_that_are_on_the_server(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _symfony(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _runner_whose_remote_listing_is(
        monkeypatch, [".env.production", ".env.example", "composer.json"]
    )
    # Declining the listing now falls to the credentials override (task 6),
    # which this test has no opinion about - `None` ends it with the usual
    # no-terminal error, which `invoke`'s unchecked return leaves harmless.
    script(monkeypatch, credentials_source=(None,))
    # After `script`, which also patches `choose_remote_env_file` with its own
    # (here, unused) empty queue - this stand-in must be the one left in place.
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        init,
        "choose_remote_env_file",
        lambda looked_for, present: (
            seen.update(looked_for=looked_for, present=present) or REMOTE_ENV_NONE
        ),
    )
    stub_checks(monkeypatch, _report(False))
    invoke(project, tmp_path)
    assert seen["looked_for"] == [".env.local", ".env"]
    assert seen["present"] == [".env.example", ".env.production"]


def test_init_does_not_ask_when_ssh_itself_is_down(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A red credentials row whose cause is connectivity is not a question's
    # to fix; the ssh row already says so (`_check_credentials` returns
    # `None`, never `False`, when ssh or the deploy path is down).
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_remote_env_file", _never_called)
    script(monkeypatch)
    stub_checks(monkeypatch, _report(None, ssh_ok=False, remote_path_ok=False))
    assert invoke(project, tmp_path) == 1


def test_declining_the_remote_file_falls_to_the_override(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Task 6 replaces the old dead end behind `REMOTE_ENV_NONE` (formerly
    # "I will fix it another way", which then did nothing further) with the
    # override question itself - declining the file is no longer where this
    # stops. A missing terminal at that question is reported the same way a
    # `check()` that raised would be, same as the remote-env-file question
    # itself a few lines above.
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _runner_whose_remote_listing_is(monkeypatch, [])
    script(monkeypatch, remote_env_file=(REMOTE_ENV_NONE,), credentials_source=(None,))
    stub_checks(monkeypatch, _report(False))
    assert invoke(project, tmp_path) == 1
    assert "database" not in yaml.safe_load(project_file.read_text(encoding="utf-8"))
    error = capsys.readouterr().err
    assert "terminal" in error
    assert f"is in {PROJECT_LABEL}" in error


def test_declining_the_remote_file_can_still_succeed_via_the_override(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unlike the test above, the override this falls to does not raise here -
    # `_offer_remote_env_file` itself returns normally once `_offer_override`
    # has written something, rather than the exception propagating through it.
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _runner_whose_credentials_probe(monkeypatch, ok=True)
    script(
        monkeypatch,
        remote_env_file=(REMOTE_ENV_NONE,),
        credentials_source=(EXPLICIT_SOURCE,),
        credentials=(CREDENTIALS,),
    )
    stub_checks(monkeypatch, _report(False), _report(True))
    assert invoke(project, tmp_path) == 0
    local = project / ".ddev" / "ddev-sync" / "local.yaml"
    loaded = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert loaded["database"]["credentials"] == CREDENTIALS


def test_no_terminal_at_the_remote_env_file_question_is_folded_into_the_check_error(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unlike every earlier question, this one runs after the target and its
    # settings are already on disk, inside the same try/except that already
    # wraps a `doctor` failure - so a missing terminal here is reported the
    # same way a `check()` that raised would be, rather than with its own
    # bespoke message.
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _runner_where_the_remote_env_is(monkeypatch, ".env.production")
    script(monkeypatch, remote_env_file=(None,))
    stub_checks(monkeypatch, _report(False))
    assert invoke(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "terminal" in error
    assert f"is in {PROJECT_LABEL}" in error
    assert "web09.example" in project_file.read_text(encoding="utf-8")


def test_a_remote_listing_that_fails_offers_only_the_opt_out(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    runner = FakeCommandRunner()
    runner.script_prefix(["ssh"], returncode=1, stderr="no such file or directory")
    monkeypatch.setattr("ddev_sync.cli.main.SubprocessRunner", lambda: runner)
    # `credentials_source=(None,)` for the same reason as the test above:
    # declining falls to the override, which this test has no opinion about.
    script(monkeypatch, credentials_source=(None,))
    # After `script`, for the same reason as the test above.
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        init,
        "choose_remote_env_file",
        lambda looked_for, present: (
            seen.update(looked_for=looked_for, present=present) or REMOTE_ENV_NONE
        ),
    )
    stub_checks(monkeypatch, _report(False))
    invoke(project, tmp_path)
    assert seen["present"] == []


def test_a_non_env_source_skips_the_remote_file_question(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review finding 2: the remote-file question's premise - a `.env` on the
    # target that nothing named - is only ever true for an `env` source.
    # `_generic` resolves to `explicit` (profiles/generic.yaml), the
    # headline case this fix closes: asking anyway led straight to a
    # question that could not fix anything, and only declining it reached
    # the override that could. `choose_remote_env_file` must not fire at all.
    _generic(project)
    monkeypatch.setattr(init, "choose_remote_env_file", _never_called)
    script(monkeypatch, credentials_source=(None,))  # None ends it harmlessly
    stub_checks(monkeypatch, _report(False))
    assert invoke(project, tmp_path) == 1


def test_an_env_source_still_asks_which_remote_file(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The mirror of the test above: an `env` source (the Laravel profile's
    # default) is exactly the case the remote-file question fits, so the
    # gate must still let it through rather than skipping straight to the
    # override.
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    _runner_where_the_remote_env_is(monkeypatch, ".env.production")
    asked = script(monkeypatch, remote_env_file=(".env.production",))
    stub_checks(monkeypatch, _report(False), _report(True))
    assert invoke(project, tmp_path) == 0
    assert asked["choose_remote_env_file"], "the remote-file question never fired"
    assert not asked["choose_credentials_source"], "the override must not also have fired"


# --- `init` falls back to credentials entered by hand (task 6) -------------
#
# Reached straight away for a non-`env` source (`_fix_credentials`'s gate,
# review finding 2) - `_generic`'s `explicit` and `_wordpress`'s `wp_cli` both
# skip the remote-file question entirely, since its premise (a `.env` on the
# target that nothing named) is only ever true for `env`. `PreflightOperation.
# check` is stubbed, as above, but `_credentials_work`'s own probe
# (`verify_credentials`, `operations/preflight.py`) is not: it is the real
# `SHOW TABLES` round trip over the fake runner (`_runner_whose_credentials_
# probe`) - the one test below whose candidate must fail scripts that round
# trip to fail, rather than standing in for the function that runs it.


def _runner_whose_credentials_probe(
    monkeypatch: pytest.MonkeyPatch, *, ok: bool, stderr: str = ""
) -> FakeCommandRunner:
    """Install a fake runner for `_credentials_work`'s own `SHOW TABLES` probe.

    The only ssh round trip left once a non-`env` source skips the remote-file
    question outright (review finding 2): there is no listing to stub, since
    `_remote_env_entries` is never reached for `explicit` or `wp_cli`.
    """
    runner = FakeCommandRunner()
    if ok:
        runner.script_prefix(["ssh"], stdout="")
    else:
        runner.script_prefix(["ssh"], returncode=1, stderr=stderr)
    monkeypatch.setattr("ddev_sync.cli.main.SubprocessRunner", lambda: runner)
    return runner


def test_init_writes_probed_credentials_to_the_local_layer(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _generic(project)
    _runner_whose_credentials_probe(monkeypatch, ok=True)
    script(
        monkeypatch,
        credentials_source=(EXPLICIT_SOURCE,),
        credentials=(CREDENTIALS,),
    )
    # Red, then green: the second report is `doctor`'s own check, standing in
    # for the write having actually fixed the configuration.
    stub_checks(monkeypatch, _report(False), _report(True))
    assert invoke(project, tmp_path) == 0
    local = project / ".ddev" / "ddev-sync" / "local.yaml"
    loaded = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert loaded["database"]["credentials"] == CREDENTIALS
    assert loaded["database"]["remote_credentials"] == "explicit"
    # The new path is already the one the add-on ignores - nothing to warn
    # about, and a project here has nothing to act on.
    assert "gitignore" not in capsys.readouterr().out


def test_credentials_that_fail_the_probe_are_never_written(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Review Focus 5. The probe runs first precisely so a wrong password
    # cannot reach the disk. Driven through the fake runner - the candidate's
    # `SHOW TABLES` round trip (`verify_credentials`) really fails here, so
    # this proves the feature itself, not that some stand-in was called.
    _generic(project)
    _runner_whose_credentials_probe(
        monkeypatch, ok=False, stderr="ERROR 1045 (28000): Access denied for user 'acme'@'host'"
    )
    script(
        monkeypatch,
        credentials_source=(EXPLICIT_SOURCE,),
        credentials=(CREDENTIALS,),
    )
    stub_checks(monkeypatch, _report(False), _report(True))
    invoke(project, tmp_path)
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()
    assert "were not written" in capsys.readouterr().out


def test_init_appends_to_the_old_local_file_when_it_exists(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Review Focus 4: writing the new path beside an existing old one would
    # create the both-exist state the loader refuses.
    _generic(project)
    legacy = project / ".ddev" / "ddev-sync.local.yaml"
    legacy.write_text("version: 1\ndefaults:\n  keep_dump: true\n", encoding="utf-8")
    _runner_whose_credentials_probe(monkeypatch, ok=True)
    script(
        monkeypatch,
        credentials_source=(EXPLICIT_SOURCE,),
        credentials=(CREDENTIALS,),
    )
    stub_checks(monkeypatch, _report(False), _report(True))
    assert invoke(project, tmp_path) == 0
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()
    loaded = yaml.safe_load(legacy.read_text(encoding="utf-8"))
    assert loaded["database"]["credentials"] == CREDENTIALS
    assert loaded["defaults"]["keep_dump"] is True
    # The acute case this fix round closes: a secret just landed on the one
    # path the add-on's shipped .gitignore does not cover, in silence before
    # this, and `init` is the only place that knows it just happened.
    output = capsys.readouterr().out
    assert ".ddev/ddev-sync.local.yaml" in output
    assert ".ddev/ddev-sync/local.yaml" in output
    assert "gitignore" in output
    # Review finding 4: this warning used to be echoed immediately, where
    # `doctor`'s whole checklist and two more closing lines then printed over
    # it. It must now be the very last thing `init` prints, after both.
    lines = output.rstrip("\n").splitlines()
    assert lines[-1].startswith("The credentials were written to")
    assert "committed with the project" in lines[-2]


def test_choosing_wp_cli_writes_the_project_file_not_the_local_one(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `wp_cli` reads the values from the remote, so there is no secret to keep.
    # No runner stub needed: the `WP_CLI_SOURCE` branch writes a plain setting
    # and returns without probing anything (`_offer_override`).
    _wordpress(project)
    script(
        monkeypatch,
        production_url=("https://acme.example",),
        credentials_source=(WP_CLI_SOURCE,),
    )
    stub_checks(monkeypatch, _report(False), _report(True))
    assert invoke(project, tmp_path) == 0
    loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert loaded["database"]["remote_credentials"] == "wp_cli"
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()


def test_no_terminal_while_entering_credentials_by_hand_writes_nothing(
    project: Path,
    project_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No runner stub needed: a missing terminal at `ask_credentials` raises
    # before `_credentials_work` ever probes anything (`_offer_override`).
    _generic(project)
    script(
        monkeypatch,
        credentials_source=(EXPLICIT_SOURCE,),
        credentials=(None,),
    )
    stub_checks(monkeypatch, _report(False))
    assert invoke(project, tmp_path) == 1
    assert not (project / ".ddev" / "ddev-sync" / "local.yaml").exists()
    error = capsys.readouterr().err
    assert "terminal" in error
    assert f"is in {PROJECT_LABEL}" in error
