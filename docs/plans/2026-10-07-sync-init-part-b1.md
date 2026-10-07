# `ddev sync init`, part B1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ddev sync init` ask the three questions it can answer without touching the network — the production URL a WordPress sanitize rule needs, the env file the sync will otherwise stop at, and the detected features — and write the answers into `.ddev/ddev-sync.yaml` under a proof as tight as the one part A's target write already has.

**Architecture:** Three layers, bottom up. `config/writer.py` learns to place a block at any key path (`place_entry`) and to write a set of settings under the mirrored proof of part A's `write_target` (`write_settings`); nothing about part A's behaviour changes, and `place_target` stays as the name its callers use. `cli/context.py` splits at the seam it already has — everything above `resolve_target` becomes `resolve_project`, so `init` can see the merged profile + feature + user configuration before a target exists. Then `cli/init.py` asks the three questions, each skipped when its precondition is absent, and writes once at the end.

**Tech Stack:** Python 3.12, click, questionary, PyYAML, pydantic, uv, pytest.

**Spec:** `docs/specs/2026-10-07-sync-init-part-b1-design.md`. Read it. It carries the reasoning and the measurements; this plan carries the code.

## Global Constraints

- Tests: `uv run pytest`. Coverage gate is **100 % line and branch** (`--cov-fail-under=100`, `branch = true`); `# pragma: no cover` is **forbidden** anywhere in the project — `exclude_lines` is deliberately empty, so a pragma cannot be used to reach the number. **Write no unreachable branch**: a defensive `else` or a trailing `raise AssertionError` that no test can hit will fail the gate, and the fix is to restructure, not to add a pragma.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests` must pass **at the committed state**. Ruff line length is 100. mypy is strict over `src`, `tests` and `tools`.
- `subprocess` may only be imported in `src/ddev_sync/transport/runner.py`; an AST-parsing test (`tests/test_subprocess_is_isolated.py`) enforces it. No task here needs it.
- **PyYAML must never parse the user's configuration file in order to write it.** It renders new blocks and it reads a written file to *verify*; it never round-trips the document through `safe_load`/`safe_dump` to produce the bytes that land on disk. The add-on ships `.ddev/ddev-sync.yaml` full of explanatory comments and a round trip deletes every one.
- `init` still writes **exactly one file per run**. Both writes go to `.ddev/ddev-sync.yaml`. Nothing in B1 touches `.ddev/ddev-sync.local.yaml`.
- Everything code-side is English: code, comments, docstrings, test names, commit messages. Every string `init` prints is English too; there is no i18n layer.
- Conventional Commits. Every message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Write the message to a file and use `git commit -F <file>`; never nest a heredoc inside another.
- Comments explain *why*, not *what*, and must be **true of the code beside them**. On the three previous plans in this repo the reviews found roughly a dozen comments that stated something the code did not do, most copied from the plan's own prose. Read this plan's wording critically and report anything that does not hold rather than copying it.
- Git history is linear, fast-forward only. **Do not push.**
- TDD: the failing test first, every time. The reviewer will look for RED/GREEN evidence.

## Review Focus

Inputs the spec implies and no task's happy path exercises. Each has its test assigned to the task that owns the code; they are listed here once so the reviewer can check them as a set, most likely to bite first.

Items 1 and 3 were found by executing this plan's own Task 1 and Task 2 code against its own tests before the plan was handed over. Both were plan bugs, and both are the kind that pass a casual reading.

1. **Running `ddev sync init` a second time on the same project.** Every setting this writes is a leaf whose value sits on the same line — `production_url: https://…`, `features: [horizon]`. A scan that matches only a bare `key:` does not find them, so the second run appends a duplicate key, the strict loader refuses it and the write is undone: `init` would work exactly once per project and then refuse forever, with a message about a file it wrote itself. This is why `_key_line` takes `inline`. (Task 1)
2. **A `sanitize:` or `env:` key the naive scan cannot see** — `sanitize: {}` on one line, or a quoted `"sanitize":`. `place_entry` misses it and appends a second top-level key; `yamlfile.py` refuses a document naming a key twice, so `write_settings` must refuse and restore the original bytes. This is the exact bargain part A struck for `targets:` and it must hold here. (Task 2)
3. **A list already in the file written at its key's own indent** — `file:` over `- .env`, which is how PyYAML itself writes one. `_block_end`'s "children are more indented" rule ends the block above those items, so a replacement would strand them under a mapping line and produce a document that does not parse. (Tasks 1 and 2)
4. **A CRLF document.** Part A's `place_target` converted the block's line endings; `place_entry` must do the same for the *parent* lines it creates, or a file gets both endings. (Task 1)
5. **Deselecting every feature** writes `features: []`, which `parse_feature_key` reads as "none, deliberately" — distinct from the absent key. The write-back check must not confuse an empty list with a missing one. (Tasks 2 and 7)
6. **A directory named `.env`.** `_find_env_file` filters `iterdir()` by `is_file()`; the init-time check must filter identically, or it reports a file the sync will then not find. (Task 6)
7. **`${PRODUCTION_URL}` in a rule's `sql:` rather than in a parameter**, and an answer carrying surrounding whitespace. The detection must look at the whole unexpanded `sanitize` node, and the validator must reject whitespace before it ever reaches `wp search-replace`. (Tasks 4 and 5)

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/ddev_sync/config/writer.py` | **Modify.** `place_target` becomes a wrapper over a new `place_entry(document, path, block)`; `_targets_line`/`_target_line` collapse into one `_key_line`. Adds `render_setting` and `write_settings` with the mirrored proof. Everything else — the atomic write, the restore, `_block_end`, `_outside_targets`, `_file_targets` — is reused unchanged. |
| `src/ddev_sync/cli/context.py` | **Modify.** Adds `ProjectResolution` and `resolve_project`; `build_context` becomes that call plus the target resolution it already ends with. No behaviour change. |
| `src/ddev_sync/operations/placeholders.py` | **Modify.** Registers `${PRODUCTION_URL}`, resolved from `sanitize.production_url`, and exports `validate_production_url` so the prompt and the resolver share one rule. |
| `profiles/wordpress.yaml` | **Modify.** Line 23 only: the `REPLACE-ME.example` literal becomes the placeholder. |
| `src/ddev_sync/cli/prompts.py` | **Modify.** Three questions, in the shape the existing nine already have. |
| `src/ddev_sync/cli/init.py` | **Modify.** Calls `resolve_project` once, asks the three questions, writes the collected settings after the target. |
| `ddev-sync/ddev-sync.example.yaml` | **Modify.** Documents `sanitize.production_url` where the template already documents `database.remote_credentials`. |

---

### Task 1: Place a block at any key path

**Files:**
- Modify: `src/ddev_sync/config/writer.py` (`place_target`, `_targets_line`, `_target_line`)
- Test: `tests/config/test_writer.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `place_entry(document: str, path: tuple[str, ...], block: str) -> str`. `place_target(document, name, block)` survives as a wrapper and keeps its exact behaviour, so every existing test in `tests/config/test_writer.py` must pass untouched. `_key_line(lines, lo, hi, key, indent) -> int | None` replaces both `_targets_line` and `_target_line`.

**Context the task needs:** part A's scan is deliberately naive — `_targets_line` finds only the exact spelling `targets:`, misses `targets: {}` and a quoted key, and mistakes the same text inside a multi-line quoted scalar for the real thing. That is acceptable **because** `write_target` verifies afterwards, not in spite of it. Keep it naive. `_block_end`'s rule — blank and comment lines after the last content line belong to whatever follows, not to this block — is what stops a replacement from deleting someone's comment, and it is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_place_entry_writes_a_nested_key_into_an_existing_parent() -> None:
    document = "version: 1\nenv:\n  strategy: patch_keys\n"
    result = place_entry(document, ("env", "file"), "  file: [.env.ddev]\n")
    assert result == "version: 1\nenv:\n  strategy: patch_keys\n  file: [.env.ddev]\n"


def test_place_entry_replaces_the_nested_key_it_finds() -> None:
    document = "version: 1\nenv:\n  file: [.env]\n  strategy: patch_keys\n"
    result = place_entry(document, ("env", "file"), "  file: [.env.ddev]\n")
    assert result == "version: 1\nenv:\n  file: [.env.ddev]\n  strategy: patch_keys\n"


def test_place_entry_creates_the_missing_parent() -> None:
    document = "version: 1\n"
    result = place_entry(document, ("sanitize", "production_url"), "  production_url: https://a.example\n")
    assert result == "version: 1\n\nsanitize:\n  production_url: https://a.example\n"


def test_place_entry_writes_a_whole_top_level_key() -> None:
    document = "version: 1\n"
    result = place_entry(document, ("features",), "features:\n- horizon\n")
    assert result == "version: 1\n\nfeatures:\n- horizon\n"


def test_place_entry_replaces_a_whole_top_level_key() -> None:
    document = "version: 1\nfeatures:\n- horizon\n- livewire\nother: 1\n"
    result = place_entry(document, ("features",), "features:\n- horizon\n")
    assert result == "version: 1\nfeatures:\n- horizon\nother: 1\n"


def test_place_entry_keeps_a_comment_above_the_next_key() -> None:
    # `_block_end`'s rule: the comment belongs to `other`, not to `features`.
    document = "version: 1\nfeatures:\n- horizon\n\n# What follows is unrelated.\nother: 1\n"
    result = place_entry(document, ("features",), "features:\n- livewire\n")
    assert "# What follows is unrelated.\n" in result
    assert "- horizon" not in result


def test_place_entry_uses_the_documents_own_line_ending_for_parents() -> None:
    # Review Focus 2: a CRLF file must not end up with both endings, and the
    # parent line `place_entry` invents is as much at risk as the block.
    document = "version: 1\r\n"
    result = place_entry(document, ("sanitize", "production_url"), "  production_url: https://a.example\n")
    assert "\n" not in result.replace("\r\n", "")


def test_place_entry_takes_a_sequence_written_at_the_keys_own_indent() -> None:
    # PyYAML writes a block list's items at the *same* indent as the key, so
    # `_block_end`'s "children are more indented" rule would end the block
    # above them and a replacement would leave the old items behind - as a
    # mapping line followed by stray sequence items, which is not valid YAML.
    document = "version: 1\nenv:\n  file:\n  - .env\n  - .env.local\n  strategy: patch_keys\n"
    result = place_entry(document, ("env", "file"), "  file: [.env.ddev]\n")
    assert result == "version: 1\nenv:\n  file: [.env.ddev]\n  strategy: patch_keys\n"


def test_place_entry_rewrites_a_key_it_wrote_itself() -> None:
    # The one that matters most: `write_settings` writes a leaf with its value
    # on the same line, so a second run must find that line. Without the
    # `inline` match it appends a duplicate key instead and `init` works
    # exactly once per project.
    document = "version: 1\n"
    for url in ("https://one.example", "https://two.example", "https://three.example"):
        document = place_entry(
            document, ("sanitize", "production_url"), f"  production_url: {url}\n"
        )
    assert document.count("sanitize:") == 1
    assert yaml.safe_load(document)["sanitize"]["production_url"] == "https://three.example"


def test_place_target_still_appends_targets_exactly_as_before() -> None:
    # Part A's behaviour, pinned here as well as in its own tests: the wrapper
    # must not change the blank line before a newly created `targets:`.
    document = "version: 1\n"
    assert place_target(document, "production", "  production:\n    stage: production\n") == (
        "version: 1\n\ntargets:\n  production:\n    stage: production\n"
    )


def test_place_entry_does_not_descend_into_an_inline_parent() -> None:
    # Part A's flow-mapping cases, as a property of `place_entry` itself: a
    # parent carrying its value inline opens no block, so it is not matched
    # and the result names the key twice - which the strict loader refuses and
    # `write_target`/`write_settings` then undo.
    document = "version: 1\ntargets: {production: {host: old.example}}\n"
    result = place_target(document, "production", "  production:\n    host: new.example\n")
    assert result.count("targets:") == 2
    assert "old.example" in result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/config/test_writer.py -k place_entry -v`
Expected: FAIL with `ImportError: cannot import name 'place_entry'`.

- [ ] **Step 3: Implement `place_entry` and collapse the two line-finders**

Replace `place_target`, `_targets_line` and `_target_line` with this. Add `place_entry` to the imports of any module that needs it (none yet — Task 2 is the first caller).

```python
def place_target(document: str, name: str, block: str) -> str:
    """Return `document` with `block` as the `name` target under `targets:`.

    Part A's entry point, kept because its callers and its tests name it, and
    because "place a target" is what `init` is doing at that call site. The
    mechanism beneath it is no longer target-specific.
    """
    return place_entry(document, (TARGETS_KEY, name), block)


def place_entry(document: str, path: tuple[str, ...], block: str) -> str:
    """Return `document` with `block` as the value at `path`.

    `block` arrives already indented for its depth - `render_target` and
    `render_setting` both do that - so this function places lines, it does not
    reformat them.

    The walk is the same one part A did by hand for `targets:` then the target
    name, with the depth no longer fixed at two: each key is looked for within
    the block its parent opened, and the first key that is not there ends the
    search, because everything below a key that does not exist cannot exist
    either. The scan stays as naive as part A's - only an exactly-spelled
    `key:` line counts - and `write_target` and `write_settings` are what make
    that safe.
    """
    eol = "\r\n" if "\r\n" in document else "\n"
    block = block.replace("\n", eol)
    # A last line without its newline would otherwise run into the block that
    # is placed after it.
    if document and not document.endswith("\n"):
        document += eol
    lines = document.splitlines(keepends=True)
    low, high = 0, len(lines)
    for level, key in enumerate(path[:-1]):
        found = _key_line(lines, low, high, key, _INDENT * level, inline=False)
        if found is None:
            return _append(lines, high, path[level:-1], block, eol, level)
        low, high = found + 1, _block_end(lines, found)
    depth = len(path) - 1
    found = _key_line(lines, low, high, path[-1], _INDENT * depth, inline=True)
    if found is None:
        return _append(lines, high, (), block, eol, depth)
    return "".join(lines[:found]) + block + "".join(lines[_block_end(lines, found) :])


def _append(
    lines: list[str],
    at: int,
    parents: tuple[str, ...],
    block: str,
    eol: str,
    level: int,
) -> str:
    """Insert `parents` (as empty mappings) and then `block` at line `at`.

    A new **top-level** key is preceded by a blank line, which is what part A
    did for a newly created `targets:` and what keeps the shipped template -
    which is almost entirely comments - readable after `init` has written to
    it. A nested key gets no such separator: a blank line inside a mapping
    reads as the end of it.
    """
    head = "".join(lines[:at])
    if level == 0 and head:
        head += eol
    opening = "".join(
        f"{_INDENT * depth}{name}:{eol}" for depth, name in enumerate(parents, start=level)
    )
    return head + opening + block + "".join(lines[at:])


def _key_line(
    lines: list[str], low: int, high: int, key: str, indent: str, *, inline: bool
) -> int | None:
    """The index in `lines[low:high]` of the line opening `key`, if any.

    `inline` is the difference between a key on the way down and the key being
    written. A **parent** must be a bare `key:` - the walk descends into the
    block it opens, and `targets: {a: 1}` opens no block, so matching it would
    mean descending into nothing. A **leaf** may carry its value on the same
    line, because that is what every scalar looks like (`production_url:
    https://...`) and what this module writes for a list (`features: [a, b]`).
    Without that, nothing `write_settings` wrote could ever be rewritten: the
    second run would not find its own key, would append a duplicate, and the
    strict loader would refuse the result - `init` would work exactly once per
    project.

    Still not found, by design: a quoted key, `key:  # note`, and the same text
    inside a multi-line quoted scalar, which is taken for the real thing. The
    checks in `write_target` and `write_settings` exist to catch all three.
    """
    for index in range(low, high):
        text = lines[index].rstrip("\r\n")
        if text == f"{indent}{key}:":
            return index
        if inline and text.startswith(f"{indent}{key}: "):
            return index
    return None
```

**This asymmetry is what keeps part A's behaviour identical.** Its three flow-mapping tests (`tests/config/test_writer.py:126`, `:203`, `:394`) all put the value inline on `targets:`, which is a *parent* — still unmatched, so the write still appends a second `targets:` key and the strict loader in `yamlfile.py` still refuses it. Verified by running this algorithm against those three documents.

And extend `_block_end` by one condition. Everything else about it — above all that blank and comment lines after the last content line belong to whatever follows — is unchanged:

```python
def _block_end(lines: list[str], start: int) -> int:
    """The index just past the last content line of the block at `start`.

    Blank and comment lines after the last content line belong to whatever
    follows, not to this block: they may be the note above the next target or
    a trailing note at the end of the file, and a replacement that took them
    along would delete someone's comment. A comment between two content
    lines is inside the block and goes with it.

    A **sequence item at the key's own indent** is part of that key's value,
    not the start of the next one. `file:` followed by `- .env` at the same
    column is how PyYAML itself writes a list and how most people write one by
    hand; treating those items as outside the block would make a replacement
    leave them behind, stranded under a mapping line, which is not even valid
    YAML. Reachable through `env.file`, which is exactly that shape.
    """
    opening = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        here = len(lines[index]) - len(lines[index].lstrip())
        if here < opening:
            break
        if here == opening and not (stripped == "-" or stripped.startswith("- ")):
            break
        end = index + 1
    return end
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/config/test_writer.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean, coverage still 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/config/writer.py tests/config/test_writer.py
cat > /tmp/msg <<'EOF'
refactor(config): place a block at any key path, not only under `targets`

`place_target` was the only placement the writer could do, and part B needs
the same mechanism for `sanitize`, `env` and `features`. The walk is now
depth-agnostic and `place_target` is a wrapper over it, so part A's behaviour
and its tests are unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 2: Write settings under the mirrored proof

**Files:**
- Modify: `src/ddev_sync/config/writer.py`
- Test: `tests/config/test_writer.py`

**Interfaces:**
- Consumes: `place_entry` (Task 1); `_write`, `_restore`, `_targets`, `_outside_targets`, `_file_targets`, `_fingerprint`, `WriteRefused` (all already in the module).
- Produces: `render_setting(route: tuple[str, ...], value: Any) -> str`; `write_settings(*, path: Path, paths: ConfigPaths, settings: Mapping[str, Any]) -> None`. `settings` is keyed by dotted path — `{"sanitize.production_url": "https://a.example", "features": ["horizon"]}`.

**Context the task needs:** part A's `write_target` proves that it changed the one target and nothing else. `write_settings` proves the complement: every requested key is on disk as asked, no top-level key *outside* the requested ones moved, and **no** target changed. Together the two cover the file. Do not relax either: a proof with a caller-supplied exemption is a weaker proof, which is why `write_target` is not being widened to do this job.

- [ ] **Step 1: Write the failing tests**

```python
def test_render_setting_indents_for_its_depth() -> None:
    assert render_setting(("sanitize", "production_url"), "https://a.example") == (
        "  production_url: https://a.example\n"
    )


def test_render_setting_writes_a_list_on_one_line() -> None:
    assert render_setting(("features",), ["horizon", "livewire"]) == (
        "features: [horizon, livewire]\n"
    )
    assert render_setting(("features",), []) == "features: []\n"
    assert render_setting(("env", "file"), [".env.ddev"]) == "  file: [.env.ddev]\n"


def test_render_setting_quotes_a_list_item_that_needs_it() -> None:
    # Flow style is still PyYAML's job, not ours - which is the point of
    # rendering through it rather than formatting the line by hand.
    rendered = render_setting(("env", "file"), [".env: odd, name"])
    assert yaml.safe_load(rendered.strip()) == {"file": [".env: odd, name"]}


def test_write_settings_writes_several_keys(tmp_path: Path) -> None:
    paths, path = _paths(tmp_path, "version: 1\n")
    write_settings(
        path=path,
        paths=paths,
        settings={"sanitize.production_url": "https://a.example", "features": ["horizon"]},
    )
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["sanitize"]["production_url"] == "https://a.example"
    assert loaded["features"] == ["horizon"]


def test_write_settings_writes_an_empty_list_as_itself(tmp_path: Path) -> None:
    # Review Focus 3: `features: []` means "none, deliberately" and is not the
    # same as an absent key, so the write-back check must accept it.
    paths, path = _paths(tmp_path, "version: 1\n")
    write_settings(path=path, paths=paths, settings={"features": []})
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["features"] == []


def test_write_settings_keeps_the_comments_around_it(tmp_path: Path) -> None:
    paths, path = _paths(tmp_path, "# Keep me.\nversion: 1\n# And me.\n")
    write_settings(path=path, paths=paths, settings={"features": ["horizon"]})
    written = path.read_text(encoding="utf-8")
    assert "# Keep me.\n" in written
    assert "# And me.\n" in written


def test_write_settings_refuses_a_key_the_scan_cannot_see(tmp_path: Path) -> None:
    # Review Focus 1: a flow mapping is not found by `_key_line`, so the write
    # appends a second `sanitize:` - which `yamlfile.py` refuses as a document
    # naming one key twice. The refusal must restore the original bytes.
    original = "version: 1\nsanitize: {}\n"
    paths, path = _paths(tmp_path, original)
    with pytest.raises(WriteRefused):
        write_settings(path=path, paths=paths, settings={"sanitize.production_url": "https://a.example"})
    assert path.read_text(encoding="utf-8") == original


def test_write_settings_refuses_when_a_target_would_change(tmp_path: Path, monkeypatch) -> None:
    # The mirrored half of `write_target`'s own neighbour check. Forced by
    # placing a block that lands inside the targets mapping, which is what a
    # scan miss looks like from the proof's side.
    original = "version: 1\ntargets:\n  production:\n    stage: production\n"
    paths, path = _paths(tmp_path, original)
    monkeypatch.setattr(
        writer,
        "place_entry",
        lambda document, path_, block: "version: 1\ntargets:\n  production:\n    stage: dev\n",
    )
    with pytest.raises(WriteRefused):
        write_settings(path=path, paths=paths, settings={"features": []})
    assert path.read_text(encoding="utf-8") == original


def test_write_settings_refuses_when_an_untouched_key_would_move(tmp_path: Path, monkeypatch) -> None:
    original = "version: 1\nother: keep\n"
    paths, path = _paths(tmp_path, original)
    monkeypatch.setattr(
        writer,
        "place_entry",
        lambda document, path_, block: "version: 1\nother: moved\nfeatures: []\n",
    )
    with pytest.raises(WriteRefused):
        write_settings(path=path, paths=paths, settings={"features": []})
    assert path.read_text(encoding="utf-8") == original


def test_write_settings_refuses_when_the_key_is_not_what_was_asked(tmp_path: Path, monkeypatch) -> None:
    original = "version: 1\n"
    paths, path = _paths(tmp_path, original)
    monkeypatch.setattr(
        writer,
        "place_entry",
        lambda document, path_, block: "version: 1\nfeatures:\n- something-else\n",
    )
    with pytest.raises(WriteRefused):
        write_settings(path=path, paths=paths, settings={"features": ["horizon"]})
    assert path.read_text(encoding="utf-8") == original


def test_write_settings_refuses_a_file_the_loader_already_rejects(tmp_path: Path) -> None:
    paths, path = _paths(tmp_path, "version: 1\nversion: 2\n")
    with pytest.raises(ConfigError):
        write_settings(path=path, paths=paths, settings={"features": []})


def test_write_settings_creates_a_missing_file_with_a_version(tmp_path: Path) -> None:
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    write_settings(path=paths.project_config, paths=paths, settings={"features": []})
    loaded = yaml.safe_load(paths.project_config.read_text(encoding="utf-8"))
    assert loaded["version"] == 1
    assert loaded["features"] == []
```

Add this helper beside the existing fixtures in the file:

```python
def _paths(tmp_path: Path, document: str) -> tuple[ConfigPaths, Path]:
    paths = discover_config_paths(tmp_path / "project", tmp_path / "home")
    paths.project_config.parent.mkdir(parents=True, exist_ok=True)
    paths.project_config.write_text(document, encoding="utf-8")
    return paths, paths.project_config
```

`import yaml` and `from ddev_sync.config.writer import render_setting, write_settings` go at the top of the test file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/config/test_writer.py -k settings -v`
Expected: FAIL with `ImportError: cannot import name 'write_settings'`.

- [ ] **Step 3: Implement `render_setting`, `write_settings` and its proof**

```python
def render_setting(route: tuple[str, ...], value: Any) -> str:
    """The YAML lines for one setting, indented to sit under its parents.

    Rendered through PyYAML for the same reason `render_target` is: quoting is
    a solved problem and not worth re-solving.

    A list is written in **flow** style - `file: [.env]`, not `file:` over a
    block of items - so the whole setting is one line. Two reasons, and the
    first is enough on its own: a one-line value cannot be half-replaced, so a
    rewrite of this key can never strand part of the old value in the file.
    The second is that `default_flow_style=None`, which would also flow the
    list, flows the *mapping* too and would write
    `{production_url: 'https://...'}` for the scalar case - a key the next
    run's scan could not find.
    """
    key = route[-1]
    if isinstance(value, list):
        body = f"{key}: {yaml.safe_dump(value, default_flow_style=True).strip()}\n"
    else:
        body = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False)
    return indent(body, _INDENT * (len(route) - 1))


def write_settings(*, path: Path, paths: ConfigPaths, settings: Mapping[str, Any]) -> None:
    """Place each dotted key of `settings` in `path`, then prove it on disk.

    The mirror of `write_target`: that function proves it touched one target
    and nothing else, this one proves it touched the named keys and no target.
    Between them the file is covered. `write_target` was deliberately not
    widened to do this - its check that *nothing* outside `targets` moved is
    worth more than one that admits a list of exceptions the caller supplies.

    As in `write_target`, `original` is held in memory rather than written to a
    backup file, so a refusal leaves no stray `.bak` behind, and an empty file
    is treated like a missing one because the loader rejects a layer with no
    `version`.
    """
    # Read before anything is touched, for its error alone: a file the loader
    # already refuses is one the engine would misread, so writing to it would
    # build on a document it cannot agree on.
    _targets(paths)
    original = path.read_bytes() if path.exists() else None
    document = original.decode("utf-8") if original else "version: 1\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    written = document
    for dotted, value in settings.items():
        route = tuple(dotted.split("."))
        written = place_entry(written, route, render_setting(route, value))
    try:
        _write(path, written.encode("utf-8"))
        _verify_settings(path=path, paths=paths, settings=settings, document=document)
    except BaseException:
        _restore(path, original)
        raise


def _verify_settings(
    *,
    path: Path,
    paths: ConfigPaths,
    settings: Mapping[str, Any],
    document: str,
) -> None:
    written = path.read_bytes().decode("utf-8")
    try:
        # Read for its error path alone: this proves the whole merged
        # configuration still loads, including the strict loader's refusal of
        # a file that now names a key twice - which is exactly what placing a
        # block at a key the scan could not see produces.
        _targets(paths)
    except DdevSyncError as error:
        raise WriteRefused(
            f"writing {', '.join(sorted(settings))} to {path} produced a file the "
            f"loader rejected, so nothing was changed: {error}"
        ) from error

    loaded = yaml.safe_load(written) or {}
    for dotted, value in settings.items():
        if _at(loaded, tuple(dotted.split("."))) != _fingerprint(value):
            raise WriteRefused(
                f"the setting {dotted!r} in {path} is not the one that was written, "
                "so nothing was changed"
            )
    # Only the top-level key of each route is exempt: a route's deeper keys
    # live inside it, so comparing at the top level already covers them.
    touched = {dotted.split(".")[0] for dotted in settings}
    before = {key: value for key, value in _outside_targets(document).items() if key not in touched}
    after = {key: value for key, value in _outside_targets(written).items() if key not in touched}
    moved = sorted(before.items() ^ after.items())
    if moved:
        keys = sorted({key for key, _ in moved})
        raise WriteRefused(
            f"writing {', '.join(sorted(settings))} to {path} would also have changed "
            f"{', '.join(map(repr, keys))}, so nothing was changed"
        )
    # No target may move at all - this writer never has a reason to touch one,
    # so unlike `write_target` there is no name to exempt.
    if _file_targets(document) != _file_targets(written):
        raise WriteRefused(
            f"writing {', '.join(sorted(settings))} to {path} would also have changed "
            "a target, so nothing was changed"
        )


def _at(loaded: Any, route: tuple[str, ...]) -> str | None:
    """The value at `route` in a parsed document, as text; None if absent.

    Compared as text for the reason `_definition` gives: a document may hold an
    alias that refers back to itself, which `safe_load` accepts and `==`
    recurses through until it fails.
    """
    node: Any = loaded
    for key in route:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return _fingerprint(node)
```

Note `_at` returns `None` for an absent key and `_fingerprint(None)` for a key present with a null value — they are different strings, which is what keeps `features: []` distinguishable from an absent `features`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/config/test_writer.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/config/writer.py tests/config/test_writer.py
cat > /tmp/msg <<'EOF'
feat(config): write settings into a layer, and prove no target moved

The mirror of `write_target`: it proves it touched one target and nothing
else, this proves it touched the named keys and no target. Between them the
file is covered, which is why `write_target` was not widened instead - a
proof with a caller-supplied exemption is a weaker proof.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 3: Resolve the project without a target

**Files:**
- Modify: `src/ddev_sync/cli/context.py` (`build_context`, lines 75-137)
- Test: `tests/cli/test_context.py`

**Interfaces:**
- Consumes: everything `build_context` already imports.
- Produces: `ProjectResolution` — a frozen dataclass with `project: DdevProject`, `detection: Detection`, `profile: ResolvedProfile`, `features: FeatureResolution`, `merged: MergeResult`, `user_layers: tuple[LayerDoc, ...]`; and `resolve_project(*, project_root, home, type_flag, framework_version_flag, profile_root, feature_root=DEFAULT_FEATURE_ROOT, features_flag=None, no_features=False) -> ProjectResolution`.

**Context the task needs:** `build_context` already does this work in this order and only then calls `resolve_target` — nothing above that call knows a target exists. This is a pure extraction at a seam that is already there, and `build_context`'s existing tests are the specification of "unchanged". It is the same move part A made with `detect_project`, for the same stated reason: so `init`'s questions ask about exactly what a real run resolves, rather than a second implementation of the same logic.

- [ ] **Step 1: Write the failing tests**

These use the `project` fixture already at the top of `tests/cli/test_context.py` (it writes `.ddev/config.yaml` with `type: php` and one `production` target) and that file's `PROFILE_ROOT` constant.

```python
def test_resolve_project_agrees_with_build_context(project: Path, tmp_path: Path) -> None:
    # The extraction is only correct if the two produce the same values, so
    # that is the assertion rather than a restatement of what a profile holds.
    resolved = resolve_project(
        project_root=project,
        home=tmp_path / "home",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    context = build_context(
        project_root=project,
        home=tmp_path / "home",
        target_name="production",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert resolved.profile.name == context.profile.name
    assert resolved.merged.data == context.merged.data
    assert resolved.project == context.project
    assert resolved.features.activated == context.features.activated


def test_resolve_project_needs_no_target(project: Path, tmp_path: Path) -> None:
    # The whole point: with no targets at all it still resolves, which is what
    # lets `init` ask its questions before it has written one. `build_context`
    # on the same project raises, and that contrast is the test.
    (project / ".ddev" / "ddev-sync.yaml").write_text("version: 1\n", encoding="utf-8")
    resolved = resolve_project(
        project_root=project,
        home=tmp_path / "home",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert resolved.profile.name
    assert resolved.detection.profile.value
    with pytest.raises(ConfigError):
        build_context(
            project_root=project,
            home=tmp_path / "home",
            target_name=None,
            type_flag=None,
            framework_version_flag=None,
            profile_root=PROFILE_ROOT,
        )


def test_resolve_project_carries_the_user_layers(project: Path, tmp_path: Path) -> None:
    # `init` reads these to see whether the user already wrote a `features:`
    # key (Task 7), so they are part of the contract, not an implementation
    # detail that happens to be reachable.
    resolved = resolve_project(
        project_root=project,
        home=tmp_path / "home",
        type_flag=None,
        framework_version_flag=None,
        profile_root=PROFILE_ROOT,
    )
    assert [layer.label for layer in resolved.user_layers] == [PROJECT_LABEL]
```

`test_no_targets_at_all_points_at_the_wizard` already in this file confirms the `ConfigError` the second test relies on; check its exact exception type and match it rather than assuming `ConfigError`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/cli/test_context.py -k resolve_project -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_project'`.

- [ ] **Step 3: Extract `resolve_project` and rebuild `build_context` on it**

Add the dataclass beside `RunContext`:

```python
@dataclass(frozen=True)
class ProjectResolution:
    """Everything a run resolves before it knows which target it is for.

    This is the half of `build_context` that `ddev sync init` needs while it
    is still asking questions: the target it will write does not exist yet, so
    anything that depends on one cannot be here.
    """

    project: DdevProject
    detection: Detection
    profile: ResolvedProfile
    features: FeatureResolution
    merged: MergeResult
    user_layers: tuple[LayerDoc, ...]
```

Add the function, lifted verbatim from `build_context`'s body down to (not including) the `deployer`/`resolve_target` lines:

```python
def resolve_project(
    *,
    project_root: Path,
    home: Path,
    type_flag: str | None,
    framework_version_flag: str | None,
    profile_root: Path,
    feature_root: Path = DEFAULT_FEATURE_ROOT,
    features_flag: str | None = None,
    no_features: bool = False,
) -> ProjectResolution:
    """Read config, detect type and version, resolve profile and features.

    Everything `build_context` does except resolve a target - which it already
    did last, so this is a split at a seam rather than a reordering. It is its
    own function so that `ddev sync init` can ask about exactly what a run
    would use, before it has a target to resolve; `detect_project` above was
    extracted from the same function for the same reason.
    """
    project = read_ddev_project(project_root)
    user_layers = load_user_layers(discover_config_paths(project_root, home))
    user_merged = merge_layers(user_layers)
    detection = detect_project(project, user_merged, type_flag, framework_version_flag)
    major = None if detection.version is None else detection.version.value

    registry = ProfileRegistry(profile_root)
    profile = registry.resolve(detection.profile.value, major)

    # Features need the profile's name (to filter `applies_to`), so they can
    # only be resolved once the profile itself is - and must happen before
    # the final merge, since their layers slot between the profile's and the
    # user's own configuration (spec §4, amended).
    feature_registry = FeatureRegistry(feature_root)
    detect_enabled, disabled_reason = _detect_enabled(user_merged.data, no_features=no_features)
    requests = _feature_requests(user_merged, features_flag)
    resolution = resolve_features(
        feature_registry, project, profile.name, requests, detect_enabled, disabled_reason
    )

    return ProjectResolution(
        project=project,
        detection=detection,
        profile=profile,
        features=resolution,
        merged=merge_layers([*profile.layers, *resolution.layers, *user_layers]),
        user_layers=tuple(user_layers),
    )
```

Then replace `build_context`'s body — its signature, docstring and return value are unchanged:

```python
    resolved = resolve_project(
        project_root=project_root,
        home=home,
        type_flag=type_flag,
        framework_version_flag=framework_version_flag,
        profile_root=profile_root,
        feature_root=feature_root,
        features_flag=features_flag,
        no_features=no_features,
    )
    user_layers = list(resolved.user_layers)
    if deployer is None and runner is not None:
        deployer = make_deployer_resolver(resolved.project, runner)
    target = resolve_target(
        _pick_target_name(target_name, user_layers),
        collect_targets(user_layers),
        resolve_deployer=deployer,
    )

    notices = () if resolved.profile.notice is None else (resolved.profile.notice,)
    return RunContext(
        project=resolved.project,
        profile=resolved.profile,
        target=target,
        merged=resolved.merged,
        notices=notices,
        features=resolved.features,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/cli/ tests/engine/ -v`
Expected: PASS. Every pre-existing `build_context` test must pass without edits; if one needs changing, the extraction changed behaviour and is wrong.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/cli/context.py tests/cli/test_context.py
cat > /tmp/msg <<'EOF'
refactor(cli): split the target resolution off `build_context`

`init` needs the merged profile, features and configuration while it is still
asking questions, and the target it will write does not exist yet.
`build_context` already resolved the target last, so this is a split at a seam
that was there - the same extraction `detect_project` was, for the same reason.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 4: `${PRODUCTION_URL}`

**Files:**
- Modify: `src/ddev_sync/operations/placeholders.py`
- Modify: `profiles/wordpress.yaml` (line 23)
- Test: `tests/operations/test_placeholders.py`, `tests/profiles/` (whichever file asserts on profile contents)

**Interfaces:**
- Consumes: `ExecutionContext.config`, `OperationError`.
- Produces: `PRODUCTION_URL_CONFIG_KEY = "sanitize.production_url"`; `PRODUCTION_URL_PLACEHOLDER = "${PRODUCTION_URL}"`; `validate_production_url(value: str) -> str | None` — the reason the value is unusable, or `None` when it is fine. Task 5 imports all three.

**Context the task needs:** this is the `${WP_TABLE_PREFIX}` pattern without the probe: a config key, and a named `OperationError` when it is absent. Read `_resolve_wp_table_prefix` and the `${SYMFONY_USER_TABLE}` entry in `_substitutions` before writing anything.

**Validation is about emptiness, not quoting.** `SearchReplace._via_wp_cli` (`operations/catalog.py:110`) passes the value as a list element to `web_command`, never as a shell string, so `_validated_identifier`'s SQL rules do not apply here and must not be copied. The hazard is a degenerate value: `wp search-replace "" ...` matches everywhere in every column.

**This is a behaviour change for existing WordPress users.** A target whose configuration was never hand-edited runs `rewrite-site-url` against `https://REPLACE-ME.example` today: a full pass over the database that matches nothing, after which production's URL is still in every row, and the sync reports success. After this change the same configuration fails with a message naming the key. The message is most people's only documentation, so write it to stand alone.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://acme.example", None),
        ("http://acme.example", None),
        ("", "the production URL cannot be empty"),
        ("https://acme.example ", "the production URL must contain no whitespace"),
        ("acme.example", "the production URL must start with 'http://' or 'https://'"),
    ],
)
def test_validate_production_url(value: str, expected: str | None) -> None:
    assert validate_production_url(value) == expected


def test_production_url_expands_from_the_config_key(tmp_path: Path) -> None:
    context = make_context(
        FakeCommandRunner(), tmp_path, sanitize={"production_url": "https://acme.example"}
    )
    assert expand_value("${PRODUCTION_URL}/x", context) == "https://acme.example/x"


def test_production_url_without_the_key_names_it(tmp_path: Path) -> None:
    context = make_context(FakeCommandRunner(), tmp_path)
    with pytest.raises(OperationError) as error:
        expand_value("${PRODUCTION_URL}", context)
    assert "sanitize.production_url" in str(error.value)
    assert "ddev sync init" in str(error.value)


def test_production_url_rejects_a_configured_value_that_is_unusable(tmp_path: Path) -> None:
    # A hand-edited file never passes the prompt, so the resolver is the only
    # thing standing between a degenerate value and `wp search-replace`.
    context = make_context(
        FakeCommandRunner(), tmp_path, sanitize={"production_url": "acme.example"}
    )
    with pytest.raises(OperationError) as error:
        expand_value("${PRODUCTION_URL}", context)
    assert "http://" in str(error.value)


def test_the_wordpress_profile_carries_no_placeholder_literal() -> None:
    text = (Path(__file__).parent.parent.parent / "profiles" / "wordpress.yaml").read_text()
    assert "REPLACE-ME" not in text
    assert "${PRODUCTION_URL}" in text
```

`make_context` is the helper already at the top of `tests/operations/test_placeholders.py`; its signature is `make_context(runner, root, *, docroot="public", **config)` and the `**config` keywords become the merged layer's data, which is why `sanitize={...}` above works unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/operations/test_placeholders.py -k production_url -v`
Expected: FAIL with `ImportError: cannot import name 'validate_production_url'`.

- [ ] **Step 3: Implement the placeholder**

Add beside the other config-key constants near the top of `placeholders.py`:

```python
PRODUCTION_URL_CONFIG_KEY = "sanitize.production_url"
PRODUCTION_URL_PLACEHOLDER = "${PRODUCTION_URL}"

_URL_SCHEMES = ("http://", "https://")
```

Add the validator and resolver:

```python
def validate_production_url(value: str) -> str | None:
    """Why `value` cannot be used as a production URL, or `None` if it can.

    Deliberately *not* `_validated_identifier`: this value reaches
    `wp search-replace` as a list element (`operations/catalog.py::SearchReplace
    ._via_wp_cli`), never as part of a shell string, so there is nothing to
    escape and nothing to inject into. The hazard is a degenerate value - an
    empty search term matches everywhere in every column of the database - so
    what is checked is that the value is a URL at all.

    Returns the reason rather than raising so that the prompt
    (`cli/prompts.py`) and the resolver below can enforce one rule: the prompt
    shows it while the user is still typing, the resolver raises it for a file
    somebody edited by hand.
    """
    if not value:
        return "the production URL cannot be empty"
    if any(character.isspace() for character in value):
        return "the production URL must contain no whitespace"
    if not value.startswith(_URL_SCHEMES):
        return "the production URL must start with 'http://' or 'https://'"
    return None


def _resolve_production_url(context: ExecutionContext) -> str:
    """The URL this site serves in production, from the configuration.

    There is nothing to probe for it: the remote's own URL is not something
    the engine can read over ssh without knowing the framework, and asking the
    user once is both cheaper and more reliable. So unlike ${WP_TABLE_PREFIX}
    this is a config key or an error, with no middle branch.
    """
    configured = context.config(PRODUCTION_URL_CONFIG_KEY, default="")
    problem = validate_production_url(configured)
    if problem is None:
        return configured
    raise OperationError(
        f"could not resolve {PRODUCTION_URL_PLACEHOLDER}: {problem}. It is the URL this "
        "site serves in production - the one a sanitize rule rewrites to the local "
        f"DDEV address after the import. Set {PRODUCTION_URL_CONFIG_KEY!r} in "
        ".ddev/ddev-sync.yaml, or run 'ddev sync init', which asks for it."
    )
```

Register it in `_substitutions`, beside `${SYMFONY_USER_TABLE}`:

```python
        # Not memoised: resolving it reads a config key and validates a short
        # string, which is the same reason ${SYMFONY_USER_TABLE} needs no thunk
        # that does real work. ${WP_TABLE_PREFIX} is the one that shells out.
        PRODUCTION_URL_PLACEHOLDER: lambda: _resolve_production_url(context),
```

Then change `profiles/wordpress.yaml` line 23 from
`      from: "https://REPLACE-ME.example"` to
`      from: "${PRODUCTION_URL}"`,
and update the comment block above the `sanitize:` rules to say where the value now comes from.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/operations/ tests/profiles/ -v`
Expected: PASS. Any existing test asserting on the `REPLACE-ME` literal is now wrong and should be updated to the placeholder, not deleted.

- [ ] **Step 5: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/operations/placeholders.py profiles/wordpress.yaml tests/
cat > /tmp/msg <<'EOF'
fix(sanitize): resolve the production URL instead of shipping a placeholder

`rewrite-site-url` searched for `https://REPLACE-ME.example`, which no site
serves: a full pass over the database that matched nothing and left
production's URL in every row, reported as a success. It now resolves
`sanitize.production_url` and fails by name when that is unset.

Overriding the rule instead was considered and rejected: `sanitize.rules` can
only be replaced wholesale, so an override would have dropped the anonymise,
password-reset and admin rules beside it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 5: `init` asks for the production URL and writes it

**Files:**
- Modify: `src/ddev_sync/cli/prompts.py`
- Modify: `src/ddev_sync/cli/init.py`
- Test: `tests/cli/test_prompts.py`, `tests/cli/test_init.py`

**Interfaces:**
- Consumes: `resolve_project`, `ProjectResolution` (Task 3); `write_settings` (Task 2); `PRODUCTION_URL_CONFIG_KEY`, `PRODUCTION_URL_PLACEHOLDER`, `validate_production_url` (Task 4).
- Produces: `ask_production_url() -> str | None` in `prompts.py`; in `init.py` the private helpers `_setting(data, dotted) -> Any`, `_mentions(value, needle) -> bool`, `_wants_production_url(resolution) -> bool`, and `_ask_project_settings(resolution: ProjectResolution, echo: Callable[[str], None]) -> dict[str, Any]`, plus the `write_settings` call after the target write. Tasks 6 and 7 add their questions inside `_ask_project_settings` — **`echo` is already a parameter from this task on**, because those two print a line beside the answer they take.

**Context the task needs:** this task establishes the shape the next two reuse. `init` calls `resolve_project` **once**, before the name question, and `_confirm_detection` stops merging the user layers for itself — it takes the detection it is given. The settings are collected before the target is written and written after it, so an `init` that ends at the name question (a declined collision, three times) writes nothing at all, which is part A's existing promise.

- [ ] **Step 1: Write the failing tests**

```python
def test_ask_production_url_rejects_a_value_with_no_scheme(monkeypatch) -> None:
    # The prompt and the resolver share one rule; this pins that they do.
    assert validate_production_url("acme.example") is not None


def test_init_asks_for_the_production_url_and_writes_it(project, project_file, tmp_path, monkeypatch) -> None:
    _wordpress(project)
    monkeypatch.setattr(init, "ask_production_url", lambda: "https://acme.example")
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0
    loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert loaded["sanitize"]["production_url"] == "https://acme.example"
    assert loaded["targets"]["production"]["host"] == "web09.example"


def test_init_does_not_ask_when_the_profile_uses_no_placeholder(project, tmp_path, monkeypatch) -> None:
    _laravel(project)
    monkeypatch.setattr(init, "ask_production_url", _never_called)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_does_not_ask_when_the_url_is_already_set(project, project_file, tmp_path, monkeypatch) -> None:
    _wordpress(project)
    project_file.write_text(
        "version: 1\nsanitize:\n  production_url: https://set.example\n", encoding="utf-8"
    )
    monkeypatch.setattr(init, "ask_production_url", _never_called)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_finds_the_placeholder_in_a_rules_sql(project, tmp_path, monkeypatch) -> None:
    # Review Focus 5: the placeholder may sit in `sql:` rather than in a
    # parameter, and the detection must not care which.
    _laravel(project)
    (project / ".ddev" / "ddev-sync.yaml").write_text(
        "version: 1\n"
        "sanitize:\n"
        "  rules:\n"
        "  - name: x\n"
        "    sql: \"UPDATE t SET url = '${PRODUCTION_URL}'\"\n",
        encoding="utf-8",
    )
    asked: list[bool] = []
    monkeypatch.setattr(init, "ask_production_url", lambda: asked.append(True) or "https://a.example")
    _answer_the_target_questions(monkeypatch)
    invoke(project, tmp_path)
    assert asked == [True]


def test_init_writes_nothing_when_the_name_question_runs_out(project, project_file, tmp_path, monkeypatch) -> None:
    # Part A's promise, which the second write must not break: the URL answer
    # is collected before the target and written after it, so an `init` that
    # never reaches the write leaves the file alone.
    _wordpress(project)
    project_file.write_text(EXISTING, encoding="utf-8")
    monkeypatch.setattr(init, "ask_production_url", lambda: "https://acme.example")
    monkeypatch.setattr(init, "ask_target_name", lambda default="production": "production")
    monkeypatch.setattr(init, "confirm_replacement", lambda *a, **k: False)
    assert invoke(project, tmp_path) == 1
    assert project_file.read_text(encoding="utf-8") == EXISTING
```

Add `_wordpress(project)` and `_laravel(project)` helpers that write the matching `.ddev/config.yaml` type, `_never_called` (a lambda that fails the test if invoked), and `_answer_the_target_questions(monkeypatch)` which patches `confirm_detection`, `ask_target_name`, `choose_connection_source`, `ask_connection` and `ask_stage` the way the existing tests in `tests/cli/test_init.py` already do — reuse that file's existing patching style rather than inventing a second one.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/cli/test_init.py -k production_url -v`
Expected: FAIL with `AttributeError: module 'ddev_sync.cli.init' has no attribute 'ask_production_url'`.

- [ ] **Step 3: Add the prompt**

In `prompts.py`, beside the other questions:

```python
def ask_production_url() -> str | None:
    """Ask what URL the site serves in production.

    Validated through the same function the placeholder resolver uses, so a
    value this question accepts is one a sync can use, and the wording of a
    rejection is written once.
    """
    return _text("What URL does this site serve in production?", "", _valid_production_url)


def _valid_production_url(answer: str) -> bool | str:
    problem = validate_production_url(answer.strip())
    return True if problem is None else f"{problem[0].upper()}{problem[1:]}."
```

with `from ddev_sync.operations.placeholders import validate_production_url` at the top.

- [ ] **Step 4: Wire it into `init`**

In `init.py`: add `from typing import Any`, `Detection`, `ProjectResolution` and `resolve_project` from `ddev_sync.cli.context`, `write_settings` from `ddev_sync.config.writer`, `ask_production_url` from `ddev_sync.cli.prompts`, and `PRODUCTION_URL_CONFIG_KEY`, `PRODUCTION_URL_PLACEHOLDER`, `validate_production_url` from `ddev_sync.operations.placeholders`. Replace the opening of `init_command`:

```python
    paths = discover_config_paths(project_root, home)
    resolution = resolve_project(
        project_root=project_root,
        home=home,
        type_flag=type_flag,
        framework_version_flag=framework_version_flag,
        profile_root=profile_root,
        feature_root=feature_root,
    )
    project = resolution.project
    _confirm_detection(resolution.detection)
    # Asked before the target and written after it: an `init` that ends at the
    # name question must leave the file exactly as it found it, which is part
    # A's promise and the reason these answers are not written as they arrive.
    settings = _ask_project_settings(resolution, echo)
    name = _ask_name(paths, echo)
```

The `project = read_ddev_project(project_root)` line that opened the function **goes away**: `resolve_project` already read it, and reading `.ddev/config.yaml` twice is two chances to disagree. `project` is still bound because `_has_deployer_recipe(project.root)` further down needs it. Drop the now-unused `read_ddev_project` import if nothing else in the module uses it — ruff will say so.

`_confirm_detection` loses its `project`, `paths` and flag parameters and its `merge_layers(load_user_layers(paths))` line, taking the `Detection` instead:

```python
def _confirm_detection(detection: Detection) -> None:
    """Ask whether the detected profile is right; refuse to go on if it is not.

    It comes first because everything written afterwards is checked against
    the profile this resolves to. The detection is handed in rather than
    computed here: `resolve_project` already did it, with the same inputs a
    real run uses.
    """
    profile = detection.profile
    version = detection.version
    ...  # the rest of the body is unchanged
```

Add the collector and its two helpers:

```python
def _ask_project_settings(
    resolution: ProjectResolution, echo: Callable[[str], None]
) -> dict[str, Any]:
    """The configuration keys `init` asks about, beyond the target itself.

    Each question is skipped when there is nothing to decide, so a project
    that needs none of them sees no extra question at all. The keys are dotted
    paths, which is what `write_settings` takes. `echo` is here because two of
    the three questions say something beside the answer they take - one when
    no env file is named, one when detection is being switched off.
    """
    settings: dict[str, Any] = {}
    if _wants_production_url(resolution):
        url = ask_production_url()
        if url is None:
            raise _no_terminal()
        settings[PRODUCTION_URL_CONFIG_KEY] = url.strip()
    return settings


def _wants_production_url(resolution: ProjectResolution) -> bool:
    """Whether a rule needs the URL and the configuration does not supply it.

    The search is for the literal placeholder anywhere under `sanitize`, in
    the *unexpanded* merged configuration - not for the profile being
    `wordpress`. A rule may carry it in `sql:` or in any parameter, and a
    project or a future profile that adopts the placeholder is asked the same
    question without anything here learning its name.
    """
    configured = _setting(resolution.merged.data, PRODUCTION_URL_CONFIG_KEY)
    if isinstance(configured, str) and validate_production_url(configured) is None:
        return False
    return _mentions(resolution.merged.data.get("sanitize"), PRODUCTION_URL_PLACEHOLDER)


def _setting(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _mentions(value: Any, needle: str) -> bool:
    """Whether `needle` occurs in any string anywhere inside `value`."""
    if isinstance(value, str):
        return needle in value
    if isinstance(value, list):
        return any(_mentions(item, needle) for item in value)
    if isinstance(value, dict):
        return any(_mentions(item, needle) for item in value.values())
    return False
```

And after the existing `write_target` block, before `build_context` is called:

```python
    if settings:
        try:
            write_settings(path=paths.project_config, paths=paths, settings=settings)
        except WriteRefused as refusal:
            echo(str(refusal))
            echo(
                f"The target {name!r} was written. Add the rest to {PROJECT_LABEL} "
                f"yourself: {', '.join(f'{key}: {value!r}' for key, value in settings.items())}"
            )
            return 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS, including every pre-existing `test_init.py` test.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/cli/ tests/cli/
cat > /tmp/msg <<'EOF'
feat(cli): ask `init` for the production URL a sanitize rule needs

`init` now resolves the project once, before the target exists, and asks only
when a rule actually carries the placeholder and no value is configured. The
answer is collected before the target is written and written after it, so an
`init` that ends early still leaves the file alone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 6: `init` checks the env file

**Files:**
- Modify: `src/ddev_sync/cli/prompts.py`
- Modify: `src/ddev_sync/cli/init.py` (`_ask_project_settings`)
- Test: `tests/cli/test_init.py`

**Interfaces:**
- Consumes: `_ask_project_settings` (Task 5).
- Produces: `choose_env_file(looked_for: list[str], present: list[str]) -> str | None` and `ENV_FILE_NONE: str` in `prompts.py`.

**Context the task needs:** `engine/plan.py::_env` emits the env step whenever `env.strategy` is not `none` and `env.set` is non-empty, **without** checking that the file exists; `operations/env.py::_find_env_file` looks for it mid-sync, after the database has been imported. Read both. The two conditions `_env` uses are the two this check uses, read from the same merged configuration, so the question appears exactly when the step would.

`_find_env_file:152` builds its candidate set from `iterdir()` filtered by `is_file()`, because `is_file()` on a constructed path folds case on a case-insensitive filesystem and would match `.ENV` for `.env`. Match it exactly — including the `is_file()` filter, which is what keeps a *directory* named `.env` from being reported as the file.

Offering `env.strategy: none` was considered and rejected; do not add it.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_writes_the_env_file_the_user_names(project, project_file, tmp_path, monkeypatch) -> None:
    _laravel(project)
    (project / ".env.ddev").write_text("APP_ENV=local\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_env_file", lambda looked_for, present: ".env.ddev")
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert yaml.safe_load(project_file.read_text(encoding="utf-8"))["env"]["file"] == [".env.ddev"]


def test_init_does_not_ask_when_the_env_file_exists(project, tmp_path, monkeypatch) -> None:
    _laravel(project)
    (project / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_env_file", _never_called)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_does_not_ask_when_the_strategy_is_none(project, tmp_path, monkeypatch) -> None:
    _wordpress(project)  # `env.strategy: none`, so there is no step to fail
    monkeypatch.setattr(init, "choose_env_file", _never_called)
    monkeypatch.setattr(init, "ask_production_url", lambda: "https://a.example")
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_offers_the_env_files_that_are_there(project, tmp_path, monkeypatch) -> None:
    _laravel(project)
    (project / ".env.example").write_text("APP_ENV=\n", encoding="utf-8")
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        init,
        "choose_env_file",
        lambda looked_for, present: seen.update(looked_for=looked_for, present=present)
        or ENV_FILE_NONE,
    )
    _answer_the_target_questions(monkeypatch)
    invoke(project, tmp_path)
    assert seen["looked_for"] == [".env"]
    assert seen["present"] == [".env.example"]


def test_init_writes_nothing_when_no_env_file_is_chosen(project, project_file, tmp_path, monkeypatch, capsys) -> None:
    _laravel(project)
    monkeypatch.setattr(init, "choose_env_file", lambda looked_for, present: ENV_FILE_NONE)
    _answer_the_target_questions(monkeypatch)
    invoke(project, tmp_path)
    assert "env" not in yaml.safe_load(project_file.read_text(encoding="utf-8"))
    assert "after the database has been imported" in capsys.readouterr().out


def test_a_directory_named_env_is_not_a_file(project, tmp_path, monkeypatch) -> None:
    # Review Focus 4: `_find_env_file` filters `iterdir()` by `is_file()`, so a
    # directory named `.env` is not the env file and the question must still
    # be asked - otherwise `init` reports a file the sync then cannot find.
    _laravel(project)
    (project / ".env").mkdir()
    asked: list[bool] = []
    monkeypatch.setattr(
        init, "choose_env_file", lambda looked_for, present: asked.append(True) or ENV_FILE_NONE
    )
    _answer_the_target_questions(monkeypatch)
    invoke(project, tmp_path)
    assert asked == [True]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/cli/test_init.py -k env_file -v`
Expected: FAIL with `AttributeError: module 'ddev_sync.cli.init' has no attribute 'choose_env_file'`.

- [ ] **Step 3: Add the prompt**

```python
ENV_FILE_NONE = "None of these - I will create the file myself"


def choose_env_file(looked_for: list[str], present: list[str]) -> str | None:
    """Ask which file holds this project's environment, or `ENV_FILE_NONE`.

    `present` is every `.env*` file really in the project root, offered
    because the usual cause of this question is a file under another name.
    `.env.example` is among them when it is there - hiding a file that exists
    is its own kind of lie - but nothing is preselected: patching the example
    is almost never wanted, and the fix for the state that produces it is to
    copy it, not to configure it.
    """
    return _select(
        f"No environment file found (looked for {', '.join(looked_for)}). Which file is it?",
        [*present, ENV_FILE_NONE],
    )
```

- [ ] **Step 4: Add the check to `_ask_project_settings`**

```python
    candidates = _missing_env_file(resolution)
    if candidates is not None:
        present = sorted(
            entry.name
            for entry in resolution.project.root.iterdir()
            if entry.is_file() and entry.name.startswith(".env")
        )
        answer = choose_env_file(candidates, present)
        if answer is None:
            raise _no_terminal()
        if answer == ENV_FILE_NONE:
            echo(
                f"No environment file was named. The sync will stop at the env step, "
                f"after the database has been imported - create one of "
                f"{', '.join(candidates)} in the project root before running it."
            )
        else:
            settings["env.file"] = [answer]
```

`echo` is already `_ask_project_settings`'s second parameter from Task 5; no signature change here.

```python
def _missing_env_file(resolution: ProjectResolution) -> list[str] | None:
    """The configured candidates when none exists; `None` when there is no question.

    The two conditions for "there is no env step" are `engine/plan.py::_env`'s
    own, read from the same merged configuration, so this question appears
    exactly when that step would - and the file is matched the way
    `operations/env.py::_find_env_file` matches it, against real directory
    entries rather than a constructed path, because `is_file()` folds case on
    a case-insensitive filesystem.
    """
    node = resolution.merged.data.get("env", {})
    settings = node if isinstance(node, dict) else {}
    if str(settings.get("strategy", "patch_keys")) == "none":
        return None
    raw_values = settings.get("set", {})
    if not isinstance(raw_values, dict) or not raw_values:
        return None
    configured = settings.get("file", [".env.local", ".env"])
    candidates = (
        [configured] if isinstance(configured, str) else [str(entry) for entry in configured]
    )
    existing = {entry.name for entry in resolution.project.root.iterdir() if entry.is_file()}
    if any(name in existing for name in candidates):
        return None
    return candidates
```

Note `env.file` is written as a **child** key, so a project that has set its own `env.set` overrides does not lose them — this is what `place_entry`'s path walk is for.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/cli/ tests/cli/
cat > /tmp/msg <<'EOF'
feat(cli): have `init` find the env file before a sync stops at it

The env step is planned whenever a strategy and values are set, and the file
is only looked for mid-sync - after the database has been imported. `init`
now checks it while nothing has happened yet, offers the `.env*` files that
are really there, and writes `env.file` for the one named. Choosing none
writes nothing: a missing `.env` in a fresh clone is usually temporary.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 7: `init` offers to switch detected features off

**Files:**
- Modify: `src/ddev_sync/cli/prompts.py`
- Modify: `src/ddev_sync/cli/init.py` (`_ask_project_settings`)
- Test: `tests/cli/test_init.py`

**Interfaces:**
- Consumes: `_ask_project_settings` (Tasks 5 and 6); `ProjectResolution.features`, `ProjectResolution.user_layers` (Task 3).
- Produces: `choose_features(labels: dict[str, str]) -> list[str] | None` in `prompts.py`.

**Context the task needs:** `features/resolver.py:68` — a written `features:` key takes the explicit branch and detection never runs again. So writing the key freezes this project's feature set, which is why it is written **only** when the user actually deselects something. `parse_feature_key` treats an absent key and `[]` as different things on purpose; `features: []` is "none, deliberately" and is a legitimate thing to write.

A bare name reproduces detection exactly: `_from_request` builds `resolve_settings(feature, Request(name, {}, source))`, which is the same call `_detected` makes. So the written list needs no settings.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_writes_nothing_when_every_feature_is_kept(project, project_file, tmp_path, monkeypatch) -> None:
    # The rule that keeps detection alive: a feature added next month is still
    # found, because no `features:` key was written.
    _laravel_with_horizon(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: list(labels))
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert "features" not in yaml.safe_load(project_file.read_text(encoding="utf-8"))


def test_init_writes_the_kept_features_when_one_is_dropped(project, project_file, tmp_path, monkeypatch) -> None:
    _laravel_with_horizon_and_livewire(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: ["horizon"])
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert yaml.safe_load(project_file.read_text(encoding="utf-8"))["features"] == ["horizon"]


def test_init_writes_an_empty_list_when_all_are_dropped(project, project_file, tmp_path, monkeypatch) -> None:
    # Review Focus 3: `[]` is "none, deliberately" and must survive the write.
    _laravel_with_horizon(project)
    monkeypatch.setattr(init, "choose_features", lambda labels: [])
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0
    assert yaml.safe_load(project_file.read_text(encoding="utf-8"))["features"] == []


def test_init_does_not_ask_when_nothing_was_detected(project, tmp_path, monkeypatch) -> None:
    _laravel(project)
    monkeypatch.setattr(init, "choose_features", _never_called)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_init_does_not_ask_when_the_user_already_wrote_the_key(project, project_file, tmp_path, monkeypatch) -> None:
    # The entries then came from that key, not from detection, so offering to
    # deselect them would be offering to rewrite an answer already given.
    _laravel_with_horizon(project)
    project_file.write_text("version: 1\nfeatures:\n- horizon\n", encoding="utf-8")
    monkeypatch.setattr(init, "choose_features", _never_called)
    _answer_the_target_questions(monkeypatch)
    assert invoke(project, tmp_path) == 0


def test_the_feature_question_shows_why_each_was_detected(project, tmp_path, monkeypatch) -> None:
    _laravel_with_horizon(project)
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        init, "choose_features", lambda labels: seen.update(labels) or list(labels)
    )
    _answer_the_target_questions(monkeypatch)
    invoke(project, tmp_path)
    assert "composer package" in seen["horizon"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/cli/test_init.py -k feature -v`
Expected: FAIL with `AttributeError: module 'ddev_sync.cli.init' has no attribute 'choose_features'`.

- [ ] **Step 3: Add the prompt**

```python
def choose_features(labels: dict[str, str]) -> list[str] | None:
    """Ask which detected features to keep; every one starts ticked.

    `labels` maps a feature name to the reason it was detected, which
    `ActivatedFeature.reason` already phrases ("detected: composer package
    filament/filament"). The reason is shown because the answer is otherwise a
    guess: a name alone does not say what in this checkout matched.

    An empty answer is "none of them", which is a real answer here and not the
    absence of one - see `features/activation.py::parse_feature_key`.
    """
    shown = {f"{name:<24} {labels[name]}".rstrip(): name for name in labels}
    ticked = _checkbox("Which detected features should the sync use?", list(shown), list(shown))
    return None if ticked is None else [shown[choice] for choice in ticked]
```

- [ ] **Step 4: Add the question to `_ask_project_settings`**

```python
    labels = _detected_features(resolution)
    if labels is not None:
        kept = choose_features(labels)
        if kept is None:
            raise _no_terminal()
        # Written only when something was actually dropped. A `features:` key
        # switches detection off for good (`features/resolver.py:68`), so
        # writing one that merely restates what detection found would freeze
        # this project's feature set for no benefit at all.
        if len(kept) != len(labels):
            settings["features"] = kept
            echo(
                "Feature detection is now off for this project: the `features:` key in "
                f"{PROJECT_LABEL} lists what the sync uses. Delete it to detect again."
            )
    return settings


def _detected_features(resolution: ProjectResolution) -> dict[str, str] | None:
    """Detected features and why, or `None` when there is no question to ask.

    There is none when nothing was activated, and none when the user has
    already written a `features:` key - in that case the resolution's entries
    came from that key rather than from detection, so offering to deselect
    them would be offering to rewrite an answer already given. That is read
    from the layers directly rather than inferred from `reason`'s text.
    """
    if any("features" in layer.data for layer in resolution.user_layers):
        return None
    activated = resolution.features.activated
    if not activated:
        return None
    return {entry.feature.name: entry.reason for entry in activated}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS.

- [ ] **Step 6: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/cli/ tests/cli/
cat > /tmp/msg <<'EOF'
feat(cli): show `init` the detected features and let one be dropped

Written only when something is actually deselected: a `features:` key turns
detection off for good, so restating what detection already found would
freeze the set for no benefit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

### Task 8: Say it in the template and record what changed

**Files:**
- Modify: `ddev-sync/ddev-sync.example.yaml`
- Modify: `docs/specs/2026-10-07-sync-init-part-b1-design.md` (append a corrections section)
- Test: none in pytest. The only test that touches this file is `tests/test.bats` (lines 139 and 273), which needs a real DDEV and does not run in the unit suite — so verify the template by parsing it directly, as step 2 does.

**Context the task needs:** the template is the file the add-on installs as `.ddev/ddev-sync.yaml`, and it is where a user who never reads a spec learns what a key does. It already documents `database.remote_credentials` in a commented block; `sanitize.production_url` belongs beside it. The `${PRODUCTION_URL}` change is a **break** for existing WordPress users, and the template is the closest thing this repo has to a release note.

- [ ] **Step 1: Document the key in the template**

Add after the `database:` commented block:

```yaml
# The URL this site serves in production. WordPress rewrites it to the local
# DDEV address after the import (`wp search-replace`), so a sync cannot do
# that step without knowing it. `ddev sync init` asks for it; a sync whose
# profile needs it and does not have it stops before touching the database.
# sanitize:
#   production_url: https://www.example.com
```

- [ ] **Step 2: Verify the template still parses and keeps its marker**

The addition is inside a comment block, so the parsed document must be byte-identical in meaning to before — that is what this checks.

Run:
```bash
uv run python -c "
import yaml, pathlib
text = pathlib.Path('ddev-sync/ddev-sync.example.yaml').read_text()
assert text.startswith('#ddev-generated'), 'the generated-file marker is gone'
assert yaml.safe_load(text) == {'version': 1}, yaml.safe_load(text)
print('ok')
"
```
Expected: `ok`. A failure means the new lines are not commented out.

- [ ] **Step 3: Append the corrections to the spec**

Add a `## Corrections after the first implementation` section, in the shape part A's spec has, recording anything this plan's wording turned out to get wrong — above all any comment the implementation could not honestly write. If nothing was wrong, say that in one line; an empty section means the check was run.

- [ ] **Step 4: Run the gates**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests`
Expected: all clean, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add ddev-sync/ddev-sync.example.yaml docs/specs/2026-10-07-sync-init-part-b1-design.md
cat > /tmp/msg <<'EOF'
docs(addon): document the production URL where the template documents the rest

A WordPress sync now stops without `sanitize.production_url` instead of
quietly rewriting a URL nobody serves, and the installed template is where
anyone who has not read the spec will look for what that key is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git commit -F /tmp/msg
```

---

## Notes for the reviewer

- **Part A must be untouched in effect.** `tests/config/test_writer.py`'s pre-existing tests and `tests/cli/test_context.py`'s `build_context` tests are the specification of that. If either needed editing, Task 1 or Task 3 changed behaviour and the change is the bug.
- **Watch the comments.** The three previous plans in this repo each left behind comments stating something the code did not do, most copied from the plan's own prose. This plan's code blocks carry comments; verify each against what was actually written rather than pasting it.
- **The `${PRODUCTION_URL}` break is deliberate.** A reviewer meeting a newly-failing WordPress test should check that the failure is the named `OperationError`, not restore the `REPLACE-ME` literal.
