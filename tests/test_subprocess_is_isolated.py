"""Confine `subprocess` to `transport/runner.py`.

The rest of this project talks to the outside world only through
`transport.runner.CommandRunner` (and its `FakeCommandRunner` test double),
which is what makes every other module's tests deterministic - nothing else
is allowed to shell out directly. This scanner enforces that by *parsing*
each module with `ast` rather than matching a literal string.

The old check was `"import subprocess" in text`. `_OLD_LITERAL_CHECK` below
keeps that exact predicate around (nowhere else in the codebase) purely so
`test_spellings_the_old_literal_check_missed` can demonstrate, concretely,
which import spellings it actually failed to catch - a plain substring scan
over the whole file still happens to match `import subprocess as sp` and an
indented `import subprocess` inside a function or `if` block, because
neither breaks the contiguous substring "import subprocess". What it
genuinely cannot match is any `from subprocess import ...` form, where the
two words are never adjacent in the source at all. The ast-based detector
below catches every one of these uniformly, by construction, rather than by
accident of which spelling happens to contain the lucky substring.
"""

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "ddev_sync"
ALLOWED = {"transport/runner.py"}


def _imports_subprocess(tree: ast.Module) -> bool:
    """True if `tree` imports `subprocess` (or a submodule of it) anywhere.

    `ast.walk` visits every node regardless of nesting, so an import inside a
    function body or an `if` block is found exactly like a top-level one -
    there is no separate "look inside blocks" step to forget.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            _names_subprocess(alias.name) for alias in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and _names_subprocess(node.module)
        ):
            return True
    return False


def _names_subprocess(dotted_name: str) -> bool:
    """True for `subprocess` itself or any of its submodules (`subprocess.foo`)."""
    return dotted_name == "subprocess" or dotted_name.startswith("subprocess.")


def test_only_the_runner_imports_subprocess() -> None:
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        if relative in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _imports_subprocess(tree):
            offenders.append(relative)
    assert offenders == [], (
        "subprocess must only be imported by transport/runner.py, found in: " + ", ".join(offenders)
    )


# --- the detector itself, against every spelling the old literal match missed


def test_a_plain_import_is_detected() -> None:
    assert _imports_subprocess(ast.parse("import subprocess\n"))


def test_an_aliased_import_is_detected() -> None:
    assert _imports_subprocess(ast.parse("import subprocess as sp\n"))


def test_a_from_import_is_detected() -> None:
    assert _imports_subprocess(ast.parse("from subprocess import run\n"))


def test_a_from_import_of_a_submodule_is_detected() -> None:
    tree = ast.parse("from subprocess import TimeoutExpired as Timeout\n")
    assert _imports_subprocess(tree)


def test_a_conditional_import_is_detected() -> None:
    tree = ast.parse("if True:\n    import subprocess\n")
    assert _imports_subprocess(tree)


def test_a_function_local_import_is_detected() -> None:
    tree = ast.parse("def run() -> None:\n    import subprocess\n    subprocess.run(['x'])\n")
    assert _imports_subprocess(tree)


def test_an_unrelated_module_is_not_a_false_positive() -> None:
    tree = ast.parse("import json\nfrom pathlib import Path\n")
    assert not _imports_subprocess(tree)


def test_a_module_merely_mentioning_subprocess_in_a_string_is_not_detected() -> None:
    # A docstring or comment naming "subprocess" must not trip the scanner -
    # only a real import node does.
    tree = ast.parse('"""This module never touches subprocess."""\n')
    assert not _imports_subprocess(tree)


# --- the old literal check, kept only to prove what it actually missed -----


def _old_literal_check(text: str) -> bool:
    """The exact predicate this file used before this rewrite."""
    return "import subprocess" in text


@pytest.mark.parametrize(
    ("source", "old_catches_it"),
    [
        # A plain substring scan over the whole file text, not anchored to
        # a line start, still happens to match these two - indentation and
        # a trailing " as sp" do not break the contiguous substring
        # "import subprocess".
        pytest.param("import subprocess as sp\n", True, id="aliased-import"),
        pytest.param("if True:\n    import subprocess\n", True, id="conditional-import"),
        pytest.param(
            "def run() -> None:\n    import subprocess\n", True, id="function-local-import"
        ),
        # A "from" import never puts the two words "import subprocess"
        # next to each other, so the old check genuinely cannot see it -
        # the one real gap the rewrite closes.
        pytest.param("from subprocess import run\n", False, id="from-import"),
        pytest.param("from subprocess import run as r\n", False, id="aliased-from-import"),
    ],
)
def test_spellings_the_old_literal_check_missed(source: str, old_catches_it: bool) -> None:
    # Whichever spelling, the new ast-based detector always catches it - the
    # point of this test is to show precisely where the OLD one stopped.
    assert _imports_subprocess(ast.parse(source))
    assert _old_literal_check(source) is old_catches_it
