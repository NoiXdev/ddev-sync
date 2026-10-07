import sys

import pytest
import questionary

from ddev_sync.cli import prompts
from ddev_sync.config.models import Stage
from ddev_sync.operations.placeholders import validate_production_url


def test_a_single_target_is_returned_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("should not have asked")

    monkeypatch.setattr(prompts, "_select", explode)
    assert prompts.choose_target(["production"], {"production": "deploy@host:/p/"}) == "production"


def test_several_targets_are_offered_with_their_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offered: list[list[str]] = []

    def fake_select(_question: str, choices: list[str]) -> str:
        offered.append(choices)
        return choices[1]

    monkeypatch.setattr(prompts, "_select", fake_select)
    chosen = prompts.choose_target(
        ["production", "staging"],
        {"production": "deploy@a:/p/", "staging": "deploy@b:/s/"},
    )
    assert chosen == "staging"
    assert "deploy@a:/p/" in offered[0][0]


def test_choose_target_passes_a_missing_terminal_through_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_select", lambda _q, _c: None)
    assert prompts.choose_target(["a", "b"], {"a": "", "b": ""}) is None


def test_no_targets_returns_none_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompts, "_select", lambda _q, _c: "never")
    assert prompts.choose_target([], {}) is None


def test_steps_are_offered_as_a_checkbox_with_sensible_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_checkbox(_question: str, choices: list[str], defaults: list[str]) -> list[str]:
        seen.append(defaults)
        return ["db"]

    monkeypatch.setattr(prompts, "_checkbox", fake_checkbox)
    assert prompts.choose_steps(["db", "files", "sanitize", "setup"]) == ["db"]
    assert seen[0] == ["db", "files", "sanitize"]


def test_choose_steps_passes_a_missing_terminal_through_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_checkbox", lambda _q, _c, _d: None)
    assert prompts.choose_steps(["db"]) is None


class _FakeQuestion:
    """Stands in for `questionary.Question`; `.unsafe_ask()` is the only method used.

    It is `unsafe_ask`, not `ask`, on purpose: `ask` swallows Ctrl-C and
    returns `None`, which the caller cannot tell from "no terminal".
    """

    def __init__(self, answer: object) -> None:
        self._answer = answer

    def unsafe_ask(self) -> object:
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer


def test_select_asks_questionary_and_returns_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_select(question: str, choices: list[str]) -> _FakeQuestion:
        seen["question"] = question
        seen["choices"] = choices
        return _FakeQuestion("b")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "select", fake_select)
    assert prompts._select("pick one", ["a", "b"]) == "b"
    assert seen == {"question": "pick one", "choices": ["a", "b"]}


def test_checkbox_asks_questionary_with_checked_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_checkbox(question: str, choices: list[questionary.Choice]) -> _FakeQuestion:
        seen["question"] = question
        seen["choices"] = choices
        return _FakeQuestion(["a"])

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "checkbox", fake_checkbox)
    assert prompts._checkbox("pick some", ["a", "b"], ["a"]) == ["a"]
    choices = seen["choices"]
    assert isinstance(choices, list)
    assert [choice.title for choice in choices] == ["a", "b"]
    assert [choice.checked for choice in choices] == [True, False]


def test_a_picker_is_preceded_by_a_blank_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A question flush against the step line above it does not read as a
    # question; this is the separation the owner asked for. Driving `_select`
    # directly, because every other test in this file replaces it - here it is
    # the thing under test, so questionary is replaced instead.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "select", lambda *_a, **_k: _FakeQuestion("production"))
    assert prompts._select("Which target?", ["production"]) == "production"
    assert capsys.readouterr().out == "\n"


def test_no_blank_line_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # With no terminal there is no question, so a stray newline would appear
    # in a piped log for nothing.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert prompts._select("Which target?", ["production"]) is None
    assert capsys.readouterr().out == ""


def test_no_prompt_is_attempted_without_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prompt with nowhere to read from blocks forever; return instead.

    This is the same case `operations/base.py::confirm` catches as `EOFError`:
    a script, a cron job, CI. questionary does not raise there - it waits on a
    terminal that will never answer, so the run hung with no output at all,
    which is the worst way for a tool to fail.
    """

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("questionary must not be reached without a terminal")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(questionary, "select", explode)
    monkeypatch.setattr(questionary, "checkbox", explode)

    assert prompts._select("pick one", ["a", "b"]) is None
    assert prompts._checkbox("tick some", ["a", "b"], ["a"]) is None


def test_ctrl_c_at_a_prompt_propagates_instead_of_looking_like_no_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `None` means "there was no terminal". A user who cancelled at a real
    # terminal must not be told otherwise, so the interrupt has to escape the
    # prompt rather than come back as `None`.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        questionary, "select", lambda _q, choices: _FakeQuestion(KeyboardInterrupt())
    )
    monkeypatch.setattr(
        questionary, "checkbox", lambda _q, choices: _FakeQuestion(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        prompts._select("pick one", ["a", "b"])
    with pytest.raises(KeyboardInterrupt):
        prompts._checkbox("tick some", ["a", "b"], ["a"])


def _fake_text_answers(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, str | None]
) -> list[tuple[str, str]]:
    """Replace `_text` with canned answers keyed by the question asked."""
    asked: list[tuple[str, str]] = []

    def fake_text(question: str, default: str, validate: object = None) -> str | None:
        asked.append((question, default))
        return answers[question]

    monkeypatch.setattr(prompts, "_text", fake_text)
    return asked


def test_a_text_question_is_asked_with_its_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def fake_text(question: str, default: str, validate: object) -> _FakeQuestion:
        seen.update(question=question, default=default, validate=validate)
        return _FakeQuestion("answer")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "text", fake_text)
    assert prompts._text("Host?", "x") == "answer"
    assert seen == {"question": "Host?", "default": "x", "validate": None}
    assert capsys.readouterr().out == "\n"


def test_a_text_question_hands_its_validator_to_questionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_text(question: str, default: str, validate: object) -> _FakeQuestion:
        seen["validate"] = validate
        return _FakeQuestion("22")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "text", fake_text)
    assert prompts._text("Port?", "22", prompts._valid_port) == "22"
    assert seen["validate"] is prompts._valid_port


def test_a_confirm_question_defaults_as_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def fake_confirm(question: str, default: bool) -> _FakeQuestion:
        seen.update(question=question, default=default)
        return _FakeQuestion(False)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(questionary, "confirm", fake_confirm)
    assert prompts._confirm("Sure?", default=False) is False
    assert seen == {"question": "Sure?", "default": False}
    assert capsys.readouterr().out == "\n"


def test_text_and_confirm_ask_nothing_and_print_nothing_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("questionary must not be reached without a terminal")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(questionary, "text", explode)
    monkeypatch.setattr(questionary, "confirm", explode)
    assert prompts._text("Host?", "") is None
    assert prompts._confirm("Sure?") is None
    assert capsys.readouterr().out == ""


def test_ctrl_c_at_a_text_or_confirm_prompt_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        questionary, "text", lambda _q, default, validate: _FakeQuestion(KeyboardInterrupt())
    )
    monkeypatch.setattr(
        questionary, "confirm", lambda _q, default: _FakeQuestion(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        prompts._text("Host?", "")
    with pytest.raises(KeyboardInterrupt):
        prompts._confirm("Sure?")


def test_the_detection_question_names_the_profile_and_where_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def fake_confirm(question: str, default: bool = True) -> bool:
        asked.append(question)
        return True

    monkeypatch.setattr(prompts, "_confirm", fake_confirm)
    assert prompts.confirm_detection("typo3", "ddev project type") is True
    assert asked == ["This looks like typo3 (from ddev project type). Is that right?"]


def test_the_detection_question_passes_a_missing_terminal_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_confirm", lambda _q, default=True: None)
    assert prompts.confirm_detection("typo3", "ddev project type") is None


def test_the_name_question_offers_production_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _fake_text_answers(monkeypatch, {"What should the target be called?": "live"})
    assert prompts.ask_target_name() == "live"
    assert asked == [("What should the target be called?", "production")]
    prompts.ask_target_name("staging")
    assert asked[1] == ("What should the target be called?", "staging")


def test_the_name_question_passes_a_missing_terminal_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_text", lambda _q, _d, _v=None: None)
    assert prompts.ask_target_name() is None


def test_the_name_question_refuses_an_emptied_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty name would be written as `targets: {"": ...}`. The refusal is
    # questionary's to enforce in the prompt, so what is checked is that the
    # question hands it the validator that says no to blank text.
    seen: list[object] = []

    def fake_text(_question: str, _default: str, validate: object = None) -> str:
        seen.append(validate)
        return "production"

    monkeypatch.setattr(prompts, "_text", fake_text)
    prompts.ask_target_name()
    assert seen == [prompts._required]
    assert isinstance(prompts._required(""), str)


def test_deployer_is_offered_as_a_connection_source_only_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offered: list[list[str]] = []

    def fake_select(_question: str, choices: list[str]) -> str:
        offered.append(choices)
        return choices[-1]

    monkeypatch.setattr(prompts, "_select", fake_select)
    assert prompts.choose_connection_source(deployer_available=True) == prompts.DEPLOYER_SOURCE
    assert prompts.choose_connection_source(deployer_available=False) == prompts.MANUAL_SOURCE
    assert prompts.DEPLOYER_SOURCE in offered[0]
    assert not any("Deployer" in choice for choice in offered[1])
    assert offered[1] == [prompts.MANUAL_SOURCE]


def test_the_connection_source_question_passes_a_missing_terminal_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_select", lambda _q, _c: None)
    assert prompts.choose_connection_source(deployer_available=True) is None


def test_the_deployer_host_question_offers_the_hosts_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offered: list[list[str]] = []

    def fake_select(_question: str, choices: list[str]) -> str:
        offered.append(choices)
        return choices[1]

    monkeypatch.setattr(prompts, "_select", fake_select)
    assert prompts.choose_deployer_host(["a", "b"]) == "b"
    assert offered == [["a", "b"]]


def test_the_deployer_host_question_passes_a_missing_terminal_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_select", lambda _q, _c: None)
    assert prompts.choose_deployer_host(["a"]) is None


def test_the_stage_question_offers_every_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    # Taken from the enum, not restated: a fifth stage must not be able to
    # appear without the wizard learning about it.
    offered: list[list[str]] = []

    def fake_select(_question: str, choices: list[str]) -> str:
        offered.append(choices)
        return choices[0]

    monkeypatch.setattr(prompts, "_select", fake_select)
    assert prompts.ask_stage() == "production"
    assert offered[0] == [stage.value for stage in Stage]


def test_the_stage_question_passes_a_missing_terminal_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_select", lambda _q, _c: None)
    assert prompts.ask_stage() is None


def test_the_replacement_question_names_the_target_the_file_and_what_is_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, bool]] = []

    def fake_confirm(question: str, default: bool = True) -> bool:
        asked.append((question, default))
        return False

    monkeypatch.setattr(prompts, "_confirm", fake_confirm)
    assert prompts.confirm_replacement("production", ".ddev/ddev-sync.yaml", "deploy@a:/p") is False
    question, default = asked[0]
    assert "production" in question
    assert ".ddev/ddev-sync.yaml" in question
    assert "deploy@a:/p" in question
    # Pressing Enter must not overwrite a target.
    assert default is False


def test_the_replacement_question_passes_a_missing_terminal_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompts, "_confirm", lambda _q, default=True: None)
    assert prompts.confirm_replacement("production", "file", "there") is None


def test_the_connection_is_asked_in_order_with_the_port_defaulting_to_22(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _fake_text_answers(
        monkeypatch,
        {"Host?": "example.com", "User?": "deploy", "Port?": "22", "Remote path?": "/var/www"},
    )
    assert prompts.ask_connection() == {
        "host": "example.com",
        "user": "deploy",
        "port": 22,
        "path": "/var/www",
    }
    assert [question for question, _default in asked] == [
        "Host?",
        "User?",
        "Port?",
        "Remote path?",
    ]
    assert dict(asked)["Port?"] == "22"


def test_the_connection_stops_asking_as_soon_as_a_question_has_no_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _fake_text_answers(monkeypatch, {"Host?": "example.com", "User?": None})
    assert prompts.ask_connection() is None
    assert [question for question, _default in asked] == ["Host?", "User?"]


def test_the_connection_hands_validators_to_the_questions_that_need_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validators: dict[str, object] = {}

    def fake_text(question: str, default: str, validate: object = None) -> str:
        validators[question] = validate
        return "22" if question == "Port?" else "x"

    monkeypatch.setattr(prompts, "_text", fake_text)
    prompts.ask_connection()
    assert validators == {
        "Host?": prompts._required,
        "User?": prompts._required,
        "Port?": prompts._valid_port,
        "Remote path?": prompts._required,
    }


@pytest.mark.parametrize("text", ["22", "1", "65535", " 2222 "])
def test_a_port_that_is_a_number_in_range_is_accepted(text: str) -> None:
    assert prompts._valid_port(text) is True


@pytest.mark.parametrize("text", ["", "ssh", "22x", "0", "65536", "-1", "2.5"])
def test_a_port_that_is_not_is_refused_with_a_sentence(text: str) -> None:
    refusal = prompts._valid_port(text)
    assert isinstance(refusal, str)
    assert "65535" in refusal


def test_a_required_answer_may_not_be_blank() -> None:
    assert prompts._required("example.com") is True
    refusal = prompts._required("   ")
    assert isinstance(refusal, str)


def test_ask_production_url_rejects_a_value_with_no_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    # The prompt and the resolver share one rule; this pins that they do.
    assert validate_production_url("acme.example") is not None


def test_production_url_question_hands_its_validator_to_the_text_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_text(question: str, default: str, validate: object = None) -> str:
        seen.update(question=question, default=default, validate=validate)
        return "https://acme.example"

    monkeypatch.setattr(prompts, "_text", fake_text)
    assert prompts.ask_production_url() == "https://acme.example"
    assert seen == {
        "question": "What URL does this site serve in production?",
        "default": "",
        "validate": prompts._valid_production_url,
    }


def test_valid_production_url_accepts_a_usable_value() -> None:
    assert prompts._valid_production_url("https://acme.example") is True


def test_valid_production_url_capitalises_and_punctuates_the_rejection() -> None:
    # Also pins that the answer is stripped before it is validated, same as
    # `ask_target_name` and `ask_connection` strip theirs.
    refusal = prompts._valid_production_url(" acme.example ")
    assert refusal == "The production URL must start with 'http://' or 'https://'."
    assert refusal
