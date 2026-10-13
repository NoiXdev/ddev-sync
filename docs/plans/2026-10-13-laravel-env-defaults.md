# Profile env defaults — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a profile declare env values a project may keep, so a sync stops
rewriting `QUEUE_CONNECTION`, `SESSION_DRIVER` and `CACHE_STORE` in a Laravel
project that configured them itself.

**Architecture:** A new `env.default` mapping beside `env.set`, applied after it
and only for keys no existing candidate env file declares. The presence test
reuses the same `_ASSIGNMENT` regex `patch_keys` uses, so the two cannot
disagree. The laravel profile moves three keys into it and gains a `REDIS_HOST`
pin. The matrix gains its first assertion that reads a written `.env`.

**Tech Stack:** Python 3.12, uv, pytest, ruff, mypy strict; YAML profiles;
Docker Compose matrix cells driven by `tests/matrix/`.

**Spec:** `docs/specs/2026-10-13-laravel-env-defaults-design.md`

## Global Constraints

- 100% line **and** branch coverage of `src/ddev_sync` (`--cov=ddev_sync`,
  `--cov-fail-under=100`); `# pragma: no cover` is forbidden and
  `exclude_lines` is deliberately empty.
- ruff line-length 100, rules `E,F,I,UP,B,SIM,RUF,ANN`; `ruff format` clean.
- mypy strict over `src tests tools`.
- `subprocess` only in `src/ddev_sync/transport/runner.py`; an AST test
  enforces it over `src/ddev_sync` (`tests/matrix/` is exempt).
- Conventional Commits; every commit ends with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Everything code-side in English: code, comments, commit messages.
- Run each gate on its own line. A `cmd | tail` pipeline reports `tail`'s exit
  status, which has bitten this project four times.
- Matrix runs need `-vv -rA` (`addopts` carries `-q`, which cancels a single
  `-v`) and `DDEV_SYNC_MATRIX_KEEP=1` to keep the installed tree.
- No cell name may contain another cell's name: CI runs `pytest -k "$CELL"`
  and `-k` matches substrings.

## Review Focus

1. **A key the project declares with unusual spacing** — `QUEUE_CONNECTION
   =redis` or a tab. `patch_keys` accepts `\s*=\s*` via `_ASSIGNMENT`
   (`env.py:29`), so a presence test written with `startswith` would call the
   key absent, write the default, and leave two assignments for one key. Pinned
   in Task 1, Step 1.
2. **A key present only as a comment** — `# QUEUE_CONNECTION=redis`.
   `_ASSIGNMENT` anchors on the key at line start, so a comment is not a
   declaration and the default must apply. Pinned in Task 1, Step 1.
3. **A key declared in both `set` and `default`** — the imposed value must win,
   because `set` is the stronger statement and a profile may inherit one of the
   two from a base. Pinned in Task 1, Step 7.
4. **`default` under `strategy: none`** — the operation returns before reading
   either mapping, so the declaration is inert. That is correct, and silent, so
   it is pinned as behaviour rather than left to be rediscovered. Task 1, Step 7.
5. **No env file at all** — `_find_env_file` raises `OperationError("no
   environment file found in the project; looked at: …")`. A `default` must not
   change that, and must not be written anywhere else. Task 1, Step 7.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/ddev_sync/operations/env.py` | Reads both mappings, computes which defaults are absent, applies them through the existing `patch_keys` call |
| `src/ddev_sync/profiles/schema.py` | Rejects a malformed or incompatible `default` at profile load |
| `profiles/laravel.yaml` | The key assignment the spec's decision 5 describes |
| `tests/operations/test_env.py` | Unit coverage for the operation, including every Review Focus line |
| `tests/profiles/test_schema.py` | Unit coverage for the two rejections |
| `tests/profiles/test_laravel_symfony.py` | That the profile's own env block says what the spec decided |
| `tests/matrix/cells.py` | `EnvMarker`, `Cell.env_markers`, and `mxfeathorizon`'s declarations |
| `tests/matrix/test_matrix.py` | The assertion that reads the written `.env` |
| `tests/matrix/cells/mxfeathorizon/` | Loses the override that this change makes unnecessary |
| `profiles/VERIFIED.md` | The `laravel` row (line 28) and `horizon` row (line 49) |

---

### Task 1: `env.default` in the env operation

**Files:**
- Modify: `src/ddev_sync/operations/env.py` (the `EnvRewriteOperation.execute`
  body at lines 92-122, plus one new module-level helper)
- Test: `tests/operations/test_env.py`

**Interfaces:**
- Consumes: `patch_keys(text, values)`, `append_block(text, values)`,
  `expand_placeholders(mapping, context)`, `_ASSIGNMENT`,
  `EnvRewriteOperation._find_env_file(context, settings)` — all already in
  `src/ddev_sync/operations/env.py`.
- Produces: `declared_keys(text: str) -> set[str]`, module-level in
  `src/ddev_sync/operations/env.py`, which Task 4's matrix assertion does
  **not** use (it reads the file directly) but Task 2's error messages name.

- [ ] **Step 1: Write the failing tests for the presence parser**

Append to `tests/operations/test_env.py`:

```python
def test_declared_keys_finds_an_ordinary_assignment() -> None:
    assert declared_keys("APP_ENV=local\nDB_HOST=db\n") == {"APP_ENV", "DB_HOST"}


def test_declared_keys_accepts_the_spacing_patch_keys_accepts() -> None:
    # `_ASSIGNMENT` allows `\s*=\s*`, and `patch_keys` rewrites such a line in
    # place. A presence test that missed it would call the key absent, write a
    # default, and leave the file with two assignments for one key.
    text = "QUEUE_CONNECTION =redis\nCACHE_STORE\t=\tredis\n"
    assert declared_keys(text) == {"QUEUE_CONNECTION", "CACHE_STORE"}
    assert patch_keys(text, {"QUEUE_CONNECTION": "sync"}).count("QUEUE_CONNECTION") == 1


def test_declared_keys_ignores_a_commented_out_key() -> None:
    # A comment is not a declaration: `_ASSIGNMENT` anchors the key at line
    # start, so `patch_keys` would append rather than rewrite, and a default
    # must apply for the same reason.
    assert declared_keys("# QUEUE_CONNECTION=redis\n") == set()


def test_declared_keys_counts_an_empty_value_as_declared() -> None:
    # `REDIS_PASSWORD=` means "no password", not "unconfigured". A written line
    # is a decision.
    assert declared_keys("REDIS_PASSWORD=\n") == {"REDIS_PASSWORD"}
```

Add `declared_keys` to the existing `from ddev_sync.operations.env import (...)`
block at the top of the file, in alphabetical position (`append_block`,
`confirm_env_change`, `declared_keys`, `expand_placeholders`, `patch_keys`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/operations/test_env.py -k declared_keys --no-cov -q`
Expected: FAIL at import — `ImportError: cannot import name 'declared_keys'`.

- [ ] **Step 3: Write the parser**

In `src/ddev_sync/operations/env.py`, directly after `patch_keys` (so it sits
beside the function whose parsing it must match):

```python
def declared_keys(text: str) -> set[str]:
    """Every key an env file assigns, by the same rule `patch_keys` rewrites by.

    Deliberately the same `_ASSIGNMENT` match rather than a cheaper
    `startswith`: `patch_keys` rewrites `KEY = value` in place, so a presence
    test that did not see that line would call the key absent, let a default be
    written, and leave the file with two assignments for one key. A commented
    line is not an assignment, and an empty value is one - `REDIS_PASSWORD=`
    means "no password", not "unconfigured".
    """
    found: set[str] = set()
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match is not None:
            found.add(match.group("key"))
    return found
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/operations/test_env.py -k declared_keys --no-cov -q`
Expected: 4 passed.

- [ ] **Step 5: Write the failing tests for the operation's behaviour**

`tests/operations/test_env.py` already has what these need, and no new helper
should be written: `make_context(root, **config)` at line 120 builds an
`ExecutionContext` for a `DdevProject` named `acme` with `assume_yes=True`,
where `**config` becomes the merged profile layer; `PROFILE_PATCH` at line 168
is an example of the shape. The operation is driven as
`EnvRewriteOperation().execute(context, {})` — the `{}` is the step's params,
not the config. Append:

```python
def test_a_default_is_written_when_the_key_is_absent(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "APP_ENV=local\nQUEUE_CONNECTION=sync\n"


def test_a_default_leaves_a_declared_key_alone(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("QUEUE_CONNECTION=redis\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "QUEUE_CONNECTION=redis\n"


def test_a_default_is_absent_when_another_candidate_declares_it(tmp_path: Path) -> None:
    # The write goes to the first existing candidate, but the presence test
    # spans all of them: a key set in `.env` while `.env.local` is the written
    # file would otherwise be overridden through Dotenv's overlay.
    (tmp_path / ".env.local").write_text("APP_ENV=dev\n", encoding="utf-8")
    (tmp_path / ".env").write_text("QUEUE_CONNECTION=redis\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env.local", ".env"],
            "strategy": "patch_keys",
            "default": {"QUEUE_CONNECTION": "sync"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env.local").read_text(encoding="utf-8") == "APP_ENV=dev\n"


def test_a_profile_with_only_defaults_still_runs(tmp_path: Path) -> None:
    # The early return used to trip on an empty `set` and would have made a
    # default-only profile silently inert.
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"CACHE_STORE": "file"}},
    )
    EnvRewriteOperation().execute(context, {})
    assert "CACHE_STORE=file" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_a_default_expands_placeholders(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env"],
            "strategy": "patch_keys",
            "default": {"REDIS_HOST": "ddev-${DDEV_SITENAME}-redis"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert "REDIS_HOST=ddev-acme-redis" in (tmp_path / ".env").read_text(encoding="utf-8")
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/operations/test_env.py -k "default or set_still" --no-cov -q`
Expected: FAIL — the defaults are not written, and
`test_a_profile_with_only_defaults_still_runs` fails because the operation
returns early on an absent `set`.

- [ ] **Step 7: Rewrite the operation body**

Replace the body of `EnvRewriteOperation.execute` between the strategy check
and `path = self._find_env_file(...)`. The current code is:

```python
        raw_values = settings.get("set", {})
        if not isinstance(raw_values, dict) or not raw_values:
            return
        values = expand_placeholders(
            {str(key): str(value) for key, value in raw_values.items()}, context
        )
```

It becomes:

```python
        raw_set = settings.get("set", {})
        raw_default = settings.get("default", {})
        imposed = raw_set if isinstance(raw_set, dict) else {}
        defaulted = raw_default if isinstance(raw_default, dict) else {}
        if not imposed and not defaulted:
            return
        if defaulted and strategy == "append_block":
            # `append_block` writes a block rather than reconciling keys, so
            # "only if absent" has nowhere to land. `profiles/schema.py`
            # rejects this in a profile; this catches a project layer, which
            # that validator never sees.
            raise OperationError("env.default cannot be used with strategy 'append_block'")

        path = self._find_env_file(context, settings)
        original = path.read_text(encoding="utf-8")
        values = expand_placeholders(
            {str(key): str(value) for key, value in imposed.items()}, context
        )
        if defaulted:
            # Across every candidate that exists, not just the written file:
            # a key declared in `.env` while `.env.local` is written would
            # otherwise be overridden through Dotenv's overlay. `set` wins a
            # collision, because it is the stronger statement and a profile may
            # inherit one of the two from a base.
            declared = self._declared_across_candidates(context, settings)
            absent = {
                str(key): str(value)
                for key, value in defaulted.items()
                if str(key) not in declared and str(key) not in values
            }
            values = {**values, **expand_placeholders(absent, context)}
        if not values:
            return
```

The lines that followed — `path = ...` and `original = ...` — move up into this
block as shown, so delete the originals. The rest of the method (the
`patch_keys`/`append_block` choice, the `rewritten == original` early return,
the confirmation and the write) is unchanged.

Add the helper as a static method on the class, below `_find_env_file`:

```python
    @staticmethod
    def _declared_across_candidates(
        context: ExecutionContext, settings: dict[str, Any]
    ) -> set[str]:
        """Every key declared by any candidate env file that exists.

        `_find_env_file` returns the one file the step writes. The presence
        test for `env.default` is deliberately wider: Symfony's Dotenv
        overlays `.env.local` on `.env`, so a default written into
        `.env.local` because `.env`'s declaration was not looked at would
        override the project's configured value - the exact surprise
        `env.default` exists to remove.
        """
        configured = settings.get("file", [".env.local", ".env"])
        candidates = [configured] if isinstance(configured, str) else list(configured)
        existing = {entry.name for entry in context.project.root.iterdir() if entry.is_file()}
        declared: set[str] = set()
        for name in candidates:
            if str(name) in existing:
                declared |= declared_keys(context.local(str(name)).read_text(encoding="utf-8"))
        return declared
```

Then add the three Review Focus tests that pin the rest:

```python
def test_a_key_in_both_mappings_takes_the_imposed_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={
            "file": [".env"],
            "strategy": "patch_keys",
            "set": {"QUEUE_CONNECTION": "sync"},
            "default": {"QUEUE_CONNECTION": "redis"},
        },
    )
    EnvRewriteOperation().execute(context, {})
    assert "QUEUE_CONNECTION=sync" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_a_default_is_inert_under_strategy_none(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path, env={"strategy": "none", "default": {"CACHE_STORE": "file"}}
    )
    EnvRewriteOperation().execute(context, {})
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "APP_ENV=local\n"


def test_a_default_under_append_block_is_an_error(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=local\n", encoding="utf-8")
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "append_block", "default": {"CACHE_STORE": "file"}},
    )
    with pytest.raises(OperationError, match="append_block"):
        EnvRewriteOperation().execute(context, {})


def test_a_default_does_not_invent_an_env_file(tmp_path: Path) -> None:
    # No candidate exists, so `_find_env_file` raises. A default must not be
    # written anywhere else, and the message must still name what was looked at.
    context = make_context(
        tmp_path,
        env={"file": [".env"], "strategy": "patch_keys", "default": {"CACHE_STORE": "file"}},
    )
    with pytest.raises(OperationError, match="no environment file found"):
        EnvRewriteOperation().execute(context, {})
    assert list(tmp_path.iterdir()) == []
```

The last test writes no env file at all, which is what makes `_find_env_file`
raise — `make_context` creates nothing on disk by itself, so simply not
writing one is enough.

- [ ] **Step 8: Run the full unit suite and the gates**

```bash
uv run pytest
```
Expected: all pass, coverage 100.00%. If a branch is uncovered, the missing
test is one of: `set` present but not a dict, `default` present but not a dict,
or `values` empty after the collision filter. Add it — `# pragma: no cover` is
forbidden.

```bash
uv run ruff check .
```
```bash
uv run ruff format --check .
```
```bash
uv run mypy src tests tools
```

- [ ] **Step 9: Commit**

```bash
git add src/ddev_sync/operations/env.py tests/operations/test_env.py
git commit -F - <<'MSG'
feat(env): let a profile declare values a project may keep

`env.set` conflated two intents: values the sync must impose, and values
that are the project's own choice. `env.default` is the second - written
only when no candidate env file declares the key.

The presence test reuses `_ASSIGNMENT`, the regex `patch_keys` rewrites
by, so the two cannot disagree: `KEY = value` is a declaration, a
commented line is not, and an empty value is one. It spans every
candidate that exists rather than the written file alone, because
Symfony's Dotenv overlays `.env.local` on `.env` and a default written
for lack of looking at `.env` would override a configured value.

`set` wins a key declared in both. `append_block` rejects `default`,
having nowhere to put "only if absent". The early return now trips only
when neither mapping has anything, so a default-only profile is not
silently inert.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 2: Reject a malformed `default` at profile load

**Files:**
- Modify: `src/ddev_sync/profiles/schema.py` (`_validate_env`, lines 36-43)
- Test: `tests/profiles/test_schema.py`

**Interfaces:**
- Consumes: `validate_profile(data)` and `ProfileError`, both already in
  `src/ddev_sync/profiles/schema.py`; `STRATEGIES` imported there from
  `ddev_sync.operations.env`.
- Produces: nothing other tasks call.

Context the implementer needs: `_validate_env` today checks only that `env` is
a mapping and that `strategy` is known. There is **no allow-list of env
sub-keys**, so `default` already loads without any change — what is missing is
the rejection of the two shapes that cannot work.

- [ ] **Step 1: Write the failing tests**

Append to `tests/profiles/test_schema.py`, beside the existing env tests
(`test_a_valid_env_strategy_passes` at line 21 shows the `MINIMAL` fixture
style):

```python
def test_a_valid_env_default_passes() -> None:
    validate_profile(
        {**MINIMAL, "env": {"strategy": "patch_keys", "default": {"QUEUE_CONNECTION": "sync"}}}
    )


def test_an_env_default_that_is_not_a_mapping_is_rejected() -> None:
    with pytest.raises(ProfileError, match="env.default"):
        validate_profile({**MINIMAL, "env": {"default": "QUEUE_CONNECTION=sync"}})


def test_an_env_default_with_append_block_is_rejected() -> None:
    # `append_block` writes a marked block rather than reconciling keys, so
    # "only if absent" cannot be expressed through it. Rejected at load, where
    # the message can name both the key and the strategy.
    with pytest.raises(ProfileError, match="append_block"):
        validate_profile(
            {**MINIMAL, "env": {"strategy": "append_block", "default": {"CACHE_STORE": "file"}}}
        )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/profiles/test_schema.py -k env_default --no-cov -q`
Expected: FAIL — `DID NOT RAISE ProfileError` on the second and third.

- [ ] **Step 3: Extend the validator**

In `src/ddev_sync/profiles/schema.py`, `_validate_env` becomes:

```python
def _validate_env(env: Any) -> None:
    if env is None:
        return
    if not isinstance(env, dict):
        raise ProfileError("'env' must be a mapping")
    strategy = env.get("strategy", "patch_keys")
    if strategy not in STRATEGIES:
        raise ProfileError(f"env.strategy {strategy!r} is not one of {', '.join(STRATEGIES)}")
    default = env.get("default")
    if default is None:
        return
    if not isinstance(default, dict):
        raise ProfileError("'env.default' must be a mapping")
    if strategy == "append_block":
        raise ProfileError(
            "env.default cannot be used with env.strategy 'append_block': that strategy "
            "writes a marked block rather than reconciling keys, so there is no key to "
            "leave alone"
        )
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/profiles/test_schema.py -k env_default --no-cov -q`
Expected: 3 passed.

- [ ] **Step 5: Gates**

```bash
uv run pytest
```
```bash
uv run ruff check .
```
```bash
uv run ruff format --check .
```
```bash
uv run mypy src tests tools
```

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/profiles/schema.py tests/profiles/test_schema.py
git commit -F - <<'MSG'
feat(profiles): reject an env.default that cannot work

`_validate_env` has no allow-list of env sub-keys, so `env.default`
already loaded. What it lacked was the two rejections: a `default` that
is not a mapping, and a `default` under `strategy: append_block`, which
writes a marked block rather than reconciling keys and so has no key to
leave alone.

Rejected at load rather than mid-sync, where the message can name both
the key and the strategy. The operation keeps its own guard for the same
combination arriving from a project layer, which this validator never
sees.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 3: The laravel profile's key assignment

**Files:**
- Modify: `profiles/laravel.yaml` (the `env` block at lines 9-27, and the
  comment at line 46 that refers to `env.set.SESSION_DRIVER`)
- Test: `tests/profiles/test_laravel_symfony.py`

**Interfaces:**
- Consumes: `env.default` as Task 1 implemented it and Task 2 validates it.
- Produces: the profile shape Task 4's cell run exercises. `REDIS_HOST` is
  `ddev-${DDEV_SITENAME}-redis`; the three defaults are `QUEUE_CONNECTION:
  sync`, `SESSION_DRIVER: file`, `CACHE_STORE: file`.

- [ ] **Step 1: Write the failing test**

Append to `tests/profiles/test_laravel_symfony.py`. That file reaches a
profile as `REGISTRY.resolve("<name>", None).data["env"]` (see its line 92 for
the symfony case) - there is no `REGISTRY.load(...).env` accessor for profiles,
only for features:

```python
def test_the_driver_choices_are_defaults_and_the_targets_are_imposed() -> None:
    # The rule the spec settles on: impose a key when a foreign value could
    # reach outside the machine or contradict what the sync just did; default
    # it when a wrong value stays local. MAIL_MAILER is a driver choice and
    # still imposed, because a local app holding freshly imported production
    # addresses must not send real mail.
    env = REGISTRY.resolve("laravel", None).data["env"]
    assert env["default"] == {
        "QUEUE_CONNECTION": "sync",
        "SESSION_DRIVER": "file",
        "CACHE_STORE": "file",
    }
    assert env["set"]["REDIS_HOST"] == "ddev-${DDEV_SITENAME}-redis"
    for key in ("APP_ENV", "APP_URL", "DB_HOST", "DB_DATABASE", "MAIL_MAILER", "MAIL_HOST"):
        assert key in env["set"], f"{key} must stay imposed"
    for key in ("QUEUE_CONNECTION", "SESSION_DRIVER", "CACHE_STORE"):
        assert key not in env["set"], f"{key} must no longer be imposed"
```

`REGISTRY.resolve(name, None).data` is how that file's existing tests reach a
profile, verified at its line 92. If the file has moved on, use what it does -
it is the authority, not this plan.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/profiles/test_laravel_symfony.py -k driver_choices --no-cov -q`
Expected: FAIL — `KeyError: 'default'`.

- [ ] **Step 3: Move the keys and add the pin**

`profiles/laravel.yaml` lines 9-27 become:

```yaml
env:
  file: [.env]
  strategy: patch_keys
  # Imposed on every sync: a foreign value here could reach outside this
  # machine, or contradict what the sync has just done.
  set:
    APP_ENV: local
    APP_DEBUG: "true"
    APP_URL: "https://${DDEV_SITENAME}.ddev.site"
    DB_CONNECTION: mysql
    DB_HOST: "ddev-${DDEV_SITENAME}-db"
    DB_PORT: "3306"
    DB_DATABASE: "${SYNC_TARGET_DATABASE}"
    DB_USERNAME: db
    DB_PASSWORD: db
    MAIL_MAILER: smtp
    MAIL_HOST: localhost
    MAIL_PORT: "1025"
    # The connection target, pinned the way DB_HOST is. DDEV's Redis add-on
    # sets no environment variable at all - its compose file declares
    # `container_name: ddev-${DDEV_SITENAME}-redis` and
    # `hostname: ${REDIS_HOSTNAME:-redis}` and nothing else - so this collides
    # with nothing, and the container name is the half a project cannot rename
    # out from under us. A project whose Redis is elsewhere overrides this key;
    # that is the accepted cost of not letting a production .env reach
    # production's Redis from a developer's machine.
    REDIS_HOST: "ddev-${DDEV_SITENAME}-redis"
  # The project's own choices. Written only when the project declares nothing,
  # so a Horizon installation keeps its queue and a Redis-backed cache or
  # session survives a sync. Getting these wrong costs the developer a loud
  # local failure and nothing outside the machine, which is what puts them
  # here rather than above.
  default:
    QUEUE_CONNECTION: sync
    SESSION_DRIVER: file
    CACHE_STORE: file
```

- [ ] **Step 4: Fix the comment that now points at the wrong key**

`profiles/laravel.yaml:46` reads `# the profile's own default - see
env.set.SESSION_DRIVER above);` inside the sanitize rules' commentary. Change
`env.set.SESSION_DRIVER` to `env.default.SESSION_DRIVER`. Read the whole
sentence before editing: it argues about the `sessions` table being optional,
and the argument is unchanged — only the key's address moved.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/profiles/test_laravel_symfony.py -k driver_choices --no-cov -q`
Expected: PASS.

- [ ] **Step 6: Run the whole unit suite, which is where the fallout lands**

```bash
uv run pytest
```
Expected: other tests may fail, and each failure is a claim about the old
behaviour that has to be re-read rather than re-pointed. Likely sites:
`tests/profiles/test_all_profiles.py` (any assertion over every profile's
`env.set`), `tests/engine/test_plan.py` (a rendered plan naming the keys),
`tests/cli/test_init.py`. Fix each by asking what the test was proving, not by
moving the string.

Then the gates:
```bash
uv run ruff check .
```
```bash
uv run ruff format --check .
```
```bash
uv run mypy src tests tools
```

- [ ] **Step 7: Commit**

```bash
git add profiles/laravel.yaml tests/profiles/test_laravel_symfony.py
git commit -F - <<'MSG'
feat(profiles): stop imposing laravel's queue, cache and session drivers

QUEUE_CONNECTION, SESSION_DRIVER and CACHE_STORE move from `env.set` to
`env.default`, so a project that configured them keeps them. The measured
harm: every sync including the db group rewrote QUEUE_CONNECTION to
`sync`, which takes a real Horizon project's queue out of use and runs
its jobs inline, with nothing said in the output.

REDIS_HOST joins `env.set` as `ddev-${DDEV_SITENAME}-redis`. Keeping the
driver without pinning the target would leave a .env copied from
production pointing at production's Redis from a developer's machine.
DDEV's Redis add-on sets no environment variable, so nothing collides;
the container name is used over `redis` because `REDIS_HOSTNAME` is
user-overridable.

MAIL_MAILER stays imposed although it is a driver choice: a local app
holding freshly imported production addresses must not send real mail.
That is the rule - impose what could reach outside the machine or
contradict the sync, default what stays local - and "driver versus
target" is not.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 4: The matrix reads a written `.env`

**Files:**
- Modify: `tests/matrix/cells.py` (a new `EnvMarker` dataclass, a new
  `Cell.env_markers` field, and `mxfeathorizon`'s entry)
- Modify: `tests/matrix/test_matrix.py` (one new test)
- Modify: `tests/matrix/test_feature_registry.py` (one minimum assertion)
- Modify: `tests/matrix/cells/mxfeathorizon/ddev-sync.yaml` (drop the override)
- Modify: `tests/matrix/cells/mxfeathorizon/install.sh` (the comments that
  explain the override and the old `APP_ENV` reasoning)
- Test: the cell run itself

**Interfaces:**
- Consumes: `Cell`, `FeatureCheck`, `work_dir(cell.name)` and the `stack` /
  `cell` fixtures, all already in `tests/matrix/`.
- Produces: `EnvMarker(key: str, expected: str, kind: Literal["imposed",
  "kept", "defaulted"])` and `Cell.env_markers: tuple[EnvMarker, ...] = ()`.

Context the implementer needs: **no matrix test reads any `.env` today.** The
last branch's final review established that the env step's effect was evidenced
only by a hand reading of a kept tree, which leaves no trace and turns nothing
red on regression. This task closes that, and the assertion it needs is exactly
the one that proves Task 3.

- [ ] **Step 1: Add the declaration**

In `tests/matrix/cells.py`, beside `FlushMarker` and `IndexMarker`:

```python
@dataclass(frozen=True)
class EnvMarker:
    """One key the project's `.env` must carry after a sync, and who wrote it.

    Three kinds, because the env step makes three different promises and a
    test that could not tell them apart would pass on the wrong one:

    - `imposed`: the profile's `env.set` carries the key, so the value must be
      the profile's whatever the project said.
    - `kept`: the project declared the key itself and the profile only
      `env.default`s it, so the project's value must survive the sync.
    - `defaulted`: the project declared nothing, so the profile's
      `env.default` value must be there.
    """

    key: str
    expected: str
    kind: Literal["imposed", "kept", "defaulted"]
```

`tests/matrix/cells.py` imports nothing from `typing` today, so add
`from typing import Literal` in the import block. Add the field to `Cell`,
after `services`:

```python
    env_markers: tuple[EnvMarker, ...] = ()
```

- [ ] **Step 2: Declare the markers on `mxfeathorizon`**

In that cell's entry, after `services=("redis",)`:

```python
        env_markers=(
            # The whole point of this cell's existence after the env change:
            # `install.sh` writes QUEUE_CONNECTION=redis, the profile only
            # defaults it, so the project's value has to survive the sync. The
            # first run of this cell lost its planted job to the old behaviour.
            EnvMarker(key="QUEUE_CONNECTION", expected="redis", kind="kept"),
            # Declared by nothing in the recipe, so the profile's default is
            # what must be there - the other half of the promise.
            EnvMarker(key="CACHE_STORE", expected="file", kind="defaulted"),
            # Imposed, and the control: if the step did not run at all, this
            # fails while the two above would still read as "project kept it".
            EnvMarker(key="DB_HOST", expected="ddev-mxfeathorizon-db", kind="imposed"),
            EnvMarker(key="REDIS_HOST", expected="ddev-mxfeathorizon-redis", kind="imposed"),
        ),
```

- [ ] **Step 3: Write the assertion**

In `tests/matrix/test_matrix.py`, beside the other feature-effect tests:

```python
def test_the_env_step_imposed_what_it_must_and_kept_what_it_should(
    stack: Stack, cell: Cell
) -> None:
    # The claim: the env step writes what the profile imposes, leaves what the
    # project declared, and fills what neither did. Until this test, nothing in
    # the matrix read a `.env` at all - the evidence for the step's effect was
    # a hand reading of a kept tree, which leaves no trace and turns nothing
    # red on regression.
    #
    # `.env` rather than the profile's candidate list: no cell creates a
    # `.env.local`, so that list resolves to `.env` for every cell that
    # declares markers, and mirroring `_find_env_file`'s search here would
    # duplicate the logic under test.
    if not cell.env_markers:
        pytest.skip(f"{cell.name} asserts no env keys")
    path = work_dir(cell.name) / ".env"
    assert path.is_file(), f"{cell.name} declares env markers but has no .env after the sync"
    declared = dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    for marker in cell.env_markers:
        assert marker.key in declared, (
            f"{marker.key} is absent from .env, and the cell says it should be "
            f"{marker.kind} as {marker.expected!r}"
        )
        assert declared[marker.key] == marker.expected, (
            f"{marker.key} is {declared[marker.key]!r}, not the {marker.kind} "
            f"{marker.expected!r}"
        )
```

- [ ] **Step 4: Keep the assertion from passing vacuously**

In `tests/matrix/test_feature_registry.py`, beside its other minimum tests:

```python
def test_some_cell_declares_env_markers_of_each_kind() -> None:
    # Against a registry where every `env_markers` is empty, the assertion in
    # `test_matrix.py` skips for every cell and proves nothing. Each kind is
    # required separately because they are three different promises: imposed,
    # kept, and defaulted.
    kinds = {marker.kind for cell in CELLS for marker in cell.env_markers}
    assert kinds == {"imposed", "kept", "defaulted"}, (
        f"only {sorted(kinds)} are asserted anywhere; the env step's other promises "
        "have no cell behind them"
    )
```

- [ ] **Step 5: Run the unit suite before touching the cell**

```bash
uv run pytest
```
Expected: pass. The matrix tests are marked and deselected here; this run only
proves the registry still imports and the new minimum passes.

- [ ] **Step 6: Drop the override the change makes unnecessary**

In `tests/matrix/cells/mxfeathorizon/ddev-sync.yaml`, delete the whole `env:`
block and the comment paragraph above it that begins "`QUEUE_CONNECTION:
redis`, overriding the profile." Nothing else in that file changes.

Then read `tests/matrix/cells/mxfeathorizon/install.sh` from the top and
correct every sentence that describes the old behaviour. At least these, and
read for others rather than trusting this list:

- The `APP_ENV` paragraph (lines 6-20) credits "the same profile step credited
  below for `CACHE_STORE`" and explains that `patch_keys` appends the key. That
  is still true of `APP_ENV`, which stays in `env.set` — but any sentence
  saying the profile *sets* `CACHE_STORE` on every sync is now describing a
  default, and a default only lands because the recipe does not write the key.
- Any sentence that explains why `ddev-sync.yaml` carries an override.

- [ ] **Step 7: Run the cell**

```bash
DDEV_SYNC_MATRIX_KEEP=1 uv run pytest -m matrix -k mxfeathorizon --no-cov -vv -rA
```
Expected: every test passed or skipped, nothing failed, and the new env test
among the passes. The cell passing **without** its override is the evidence
for Task 3: the profile no longer overwrites the project's queue driver.

If `QUEUE_CONNECTION` comes back as `sync`, the profile change did not take
effect for this run — check that the image was rebuilt from this tree, because
the matrix runs the image rather than the checkout.

- [ ] **Step 8: Run one unrelated Laravel cell for regression**

```bash
uv run pytest -m matrix -k mxlaravel --no-cov -vv -rA
```
Expected: pass. `mxlaravel` writes its own `.env` and declares no env markers;
it is here to show the profile change did not break the stock path.

- [ ] **Step 9: Gates and commit**

```bash
uv run pytest
```
```bash
uv run ruff check .
```
```bash
uv run ruff format --check .
```
```bash
uv run mypy src tests tools
```

```bash
git add tests/matrix/cells.py tests/matrix/test_matrix.py tests/matrix/test_feature_registry.py tests/matrix/cells/mxfeathorizon
git commit -F - <<'MSG'
test(matrix): assert what the env step imposes, keeps and defaults

No matrix test read a `.env` before this one. The last branch's final
review established that the env step's effect was evidenced only by a
hand reading of a kept tree - which leaves no trace and turns nothing red
on regression - and the assertion that closes that gap is the same one
that proves the profile change.

Three kinds, because the step makes three different promises and a test
that could not tell them apart would pass on the wrong one: a key the
profile imposes, a key the project declared and the profile must leave
alone, and a key nobody declared that the default fills.

`mxfeathorizon` drops the `env: set: QUEUE_CONNECTION: redis` override it
needed to be measurable at all. The cell passing without it is the
evidence: its recipe writes QUEUE_CONNECTION=redis and the sync no longer
takes it away.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 5: The rows, and the two follow-up items

**Files:**
- Modify: `profiles/VERIFIED.md` (the `laravel` row, line 28, and the `horizon`
  row, line 49)
- Modify: `docs/plans/2026-10-12-service-feature-verification-follow-ups.md`
  (items 1 and 2)

**Interfaces:**
- Consumes: the two cell runs from Task 4 and their output.
- Produces: nothing.

- [ ] **Step 1: Re-read what the rows claim**

Both describe the old behaviour. The `laravel` row's env sentence was corrected
once already on the previous branch and now says the keys were read back by a
hand reading of a kept tree. The `horizon` row carries the trap as a warning to
the reader. Read both in full before writing: the rest of each row is evidence
from runs this task does not repeat, and must not be touched.

- [ ] **Step 2: Rewrite the `laravel` row's env sentences**

Replace the `env` `patch_keys` discussion with what the new run shows, in the
file's voice. Required content, and nothing beyond it:

- The step is now asserted by a matrix test rather than by a hand reading:
  `test_the_env_step_imposed_what_it_must_and_kept_what_it_should` reads the
  written `.env` after a sync.
- `DB_HOST` and `REDIS_HOST` are imposed and were observed at their imposed
  values; `CACHE_STORE` was written by `env.default` because the recipe
  declares it nowhere; `QUEUE_CONNECTION` was declared by the recipe and
  survived.
- The keys that moved, and that the row's old claim — that
  `QUEUE_CONNECTION`/`CACHE_STORE` are "never read back" — is now false in both
  directions: they are read back, and one of them is no longer imposed at all.
- **Not run:** `strategy: append_block` with a `default` (rejected at load, so
  no sync reaches it), a project carrying `.env.local` (no cell creates one),
  and `REDIS_HOST` against a project whose Redis is not the DDEV service.

- [ ] **Step 3: Rewrite the `horizon` row's trap paragraph**

It currently warns that the profile takes a Horizon project's queue out of use
unless the project overrides `QUEUE_CONNECTION`. That is fixed. The paragraph
becomes a record: what the behaviour was, that the cell needed an override to
be measurable, that the override is gone, and that the cell passing without it
is the evidence. Keep the half that is still true and not about this change:
`horizon:clear` resolves its connection from the first `horizon.defaults`
supervisor rather than from `queue.default`, so it was never the command that
the patch broke.

- [ ] **Step 4: Close follow-up items 1 and 2**

In `docs/plans/2026-10-12-service-feature-verification-follow-ups.md`, mark
items 1 and 2 closed in the same shape the other closed items use — keep the
reasoning, add what closed it and where the evidence lives. Item 1 is the queue
driver; item 2 is `CACHE_STORE`/`SESSION_DRIVER`. Name the spec and this plan.

- [ ] **Step 5: Gates**

```bash
uv run pytest
```
```bash
uv run ruff check .
```
```bash
uv run ruff format --check .
```
```bash
uv run mypy src tests tools
```

- [ ] **Step 6: Commit**

```bash
git add profiles/VERIFIED.md docs/plans/2026-10-12-service-feature-verification-follow-ups.md
git commit -F - <<'MSG'
docs(profiles): record the env step's effect from a run, not a hand reading

The laravel row's env claim was corrected once already and still rested
on a hand reading of a kept tree. It now rests on a matrix assertion that
reads the written `.env`, and the row says which keys were imposed, which
survived from the project, and which the default filled.

The horizon row's trap paragraph becomes a record of what was fixed. What
stays is the half this change does not touch: `horizon:clear` resolves
its connection from the first `horizon.defaults` supervisor, so it was
never the command the patch broke - the harm was to the project.

Follow-up items 1 and 2 are closed, with their reasoning kept.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Out of scope

- `APP_DEBUG`, and any key not named in Task 3.
- `symfony` and `shopware6` key assignments. They gain the mechanism and keep
  their behaviour; they pin no driver choices today.
- Reporting which keys the env step changed.
- `horizon:clear` clearing only the `default` queue, the redundant
  `filament:upgrade` entry, and how a sync reports a tolerated failure. Each is
  a separate decision about what a feature promises.

## Final check before handing the branch on

- [ ] `uv run pytest` green; `ruff check`, `ruff format --check`,
  `mypy src tests tools` clean, each run on its own line.
- [ ] `DDEV_SYNC_MATRIX_KEEP=1 uv run pytest -m matrix -k "mxfeathorizon or mxlaravel" --no-cov -vv -rA`
  green in one run.
- [ ] `grep -rn "QUEUE_CONNECTION" profiles tests/matrix/cells` shows no
  project-level override left behind.
- [ ] Linear history, no merges, every commit carrying the
  `Co-Authored-By` line.
- [ ] `git status` clean. Nothing pushed.
