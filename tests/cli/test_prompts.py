import pytest
import questionary

from ddev_sync.cli import prompts


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


def test_cancelling_the_target_prompt_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_cancelling_the_step_prompt_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompts, "_checkbox", lambda _q, _c, _d: None)
    assert prompts.choose_steps(["db"]) is None


class _FakeQuestion:
    """Stands in for `questionary.Question`; `.ask()` is the only method used."""

    def __init__(self, answer: object) -> None:
        self._answer = answer

    def ask(self) -> object:
        return self._answer


def test_select_asks_questionary_and_returns_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_select(question: str, choices: list[str]) -> _FakeQuestion:
        seen["question"] = question
        seen["choices"] = choices
        return _FakeQuestion("b")

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

    monkeypatch.setattr(questionary, "checkbox", fake_checkbox)
    assert prompts._checkbox("pick some", ["a", "b"], ["a"]) == ["a"]
    choices = seen["choices"]
    assert isinstance(choices, list)
    assert [choice.title for choice in choices] == ["a", "b"]
    assert [choice.checked for choice in choices] == [True, False]
