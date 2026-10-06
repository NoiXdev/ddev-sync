# The `deployer:` connection block — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a sync target declare `deployer: {host: <alias>}` instead of repeating host, user, port and path, and resolve those four values at run time from the project's own Deployer.

**Architecture:** The feature splits along the line this codebase already draws — a pure half and a thin I/O edge. `config/deployer.py` parses `dep config --format=json` into a value object and does every piece of validation and derivation; it executes nothing. `transport/deployer.py` is the only part that runs anything: it builds the argv, runs it in the project's web container through the existing `web_command`, and memoises the result for the run. `config/targets.py` takes a callable and never learns what a container is. Everything downstream of `ResolvedTarget` — ssh, rsync, dump, sanitize, the plan renderer — is untouched, because `ResolvedTarget` is already the boundary.

**Tech Stack:** Python 3.13, pydantic, click, pytest, uv. Deployer 7 on the remote side of the design (read-only, via `dep config`).

**Source document:** `docs/specs/2026-10-06-deployer-connection-block-design.md`. Read it. It carries the decisions and the reasons; this plan carries the code.

## Global Constraints

- `uv run pytest` must pass at **100 % line and branch coverage**. `# pragma: no cover` is **forbidden** — `exclude_lines` is deliberately empty, so a pragma cannot be used to reach the number.
- `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` (strict over `src`, `tests`, `tools`) must all be clean.
- **`subprocess` is confined to `src/ddev_sync/transport/runner.py`**, enforced by an AST-parsing test. Nothing you add may import or call it elsewhere.
- Code, comments and commit messages in **English**. Comments explain *why*, in full sentences.
- Conventional Commits; every message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Git history is linear, fast-forward only. **Do not push** — the repository owner approves every push.
- TDD: the failing test first, every time. The reviewer will look for RED/GREEN evidence.
- A `deployer:` block is **strict**: it is the only source for `host`, `user`, `port` and `path`. There is never a fallback to a literal value — not on a failed resolution, not on a missing key.

## Three corrections to the spec

The spec was written before this plan read the code. Three of its statements do not survive contact with it, and the plan implements the corrected version. Amend the spec in Task 5.

> **Superseded in part.** The *decision* below stands, but its stated reason —
> that a stopped project must be able to list — was measured and found false:
> DDEV starts the project for any service command. See Amendment 1 in the design
> document for the reason that survives. The paragraphs below are the
> pre-implementation record and are left unedited.

**1. `ddev sync targets` must not resolve.** The spec says the listing "shows the resolved endpoint". `render_targets` (`src/ddev_sync/cli/render.py:132-146`) reads the *collected spec* and performs no I/O at all; `cli/commands.py:46` calls it with `collect_targets(layers)` and never builds a `ResolvedTarget`. Making it resolve would mean shelling into the web container to list targets, so a stopped project could not list its own configuration. The listing shows the **declaration** instead. Resolution surfaces in the dry-run plan and in `doctor`, both of which already do work.

**2. There is no `doctor` row.** The spec gives `doctor` a conditional `deployer config` row. `doctor_command` (`cli/commands.py:51`) receives an already-built `RunContext`, and resolution happens inside `build_context` — so a failure raises before `doctor` is reached, and the row could only ever say `OK`. A `bool` field whose `False` branch is unreachable is dead code that no test can legitimately cover under the 100 % branch rule. Task 4 carries the full reasoning and what replaces it.

**3. The `extends` chain needs a precedence rule, not a rejection.** The spec says literal values "beside" a `deployer:` block are rejected, without saying whether that means the same block or anywhere in the chain. This codebase already answers the equivalent question for `host` versus `ssh_alias` in `config/targets.py:137-142`: when both appear in a chain, whichever the *stronger* (later) link set wins, and the weaker is dropped. `deployer:` is a third way of saying "where to connect", so it follows the same rule. Rejection applies only when **one** spec carries both.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/ddev_sync/config/deployer.py` | **New, pure.** `DeployerConnection` value object; parse `dep config` JSON; select the host; read the four keys; derive the layout. Executes nothing. |
| `src/ddev_sync/transport/deployer.py` | **New, the only I/O.** Build the `dep` argv, run it in the web container, return stdout or raise with dep's stderr. Memoise per run. |
| `src/ddev_sync/config/models.py` | **Modify.** `DeployerRef` model; `TargetSpec.deployer`; the same-spec rejection. |
| `src/ddev_sync/config/targets.py` | **Modify.** Chain precedence for `deployer`; substitute the resolved values in `_build`. |
| `src/ddev_sync/cli/context.py` | **Modify.** Accept a runner, build the resolver, pass it to `resolve_target`. |
| `src/ddev_sync/cli/commands.py` | **Modify.** Pass `SubprocessRunner()` into `build_context`. |
| `src/ddev_sync/cli/render.py` | **Modify.** Render the declaration in `render_targets`; the new `doctor` row. |
| `src/ddev_sync/operations/preflight.py` | **Modify.** `deployer_ok` / `deployer_detail` on `PreflightReport`. |
| `tests/config/test_deployer.py` | **New.** The pure parser, against a real captured fixture. |
| `tests/config/fixtures/dep-config-t3-13-ssg.json` | **New.** The real `dep config` output, including its null port and error object. |
| `tests/transport/test_deployer.py` | **New.** The I/O edge, with the existing fake runner. |

---

### Task 1: The schema and its strict validation

A `deployer:` block parses, forbidden combinations are rejected by name, and a target that needs resolution without a resolver fails with a message that says so. No resolution yet — this task is reviewable on its own because every rejection is observable without running anything.

**Files:**
- Modify: `src/ddev_sync/config/models.py`
- Modify: `src/ddev_sync/config/targets.py:117-165`
- Test: `tests/config/test_targets.py`

**Interfaces:**
- Produces: `DeployerRef` (pydantic model, field `host: str | None = None`); `TargetSpec.deployer: DeployerRef | None = None`; and `resolve_target(name, collected, *, resolve_deployer=None)` where `resolve_deployer` is `Callable[[str | None], DeployerConnection] | None`. Task 3 supplies the callable; Task 2 defines `DeployerConnection`.

- [ ] **Step 1: Write the failing tests for the schema and the same-spec rejections**

Add to `tests/config/test_targets.py`. Use the module's existing helper for building layers — read the top of the file and follow it rather than inventing a second style.

```python
def test_a_deployer_block_parses_with_an_alias() -> None:
    collected = collect_targets(
        [_layer("global", {"targets": {"prod": {"deployer": {"host": "web-1"}, "stage": "production"}}})]
    )
    assert collected["prod"].spec.deployer is not None
    assert collected["prod"].spec.deployer.host == "web-1"


def test_a_deployer_block_may_be_empty_as_null_or_as_a_mapping() -> None:
    # `deployer:` with nothing after it parses as None, `deployer: {}` as a
    # mapping. Both mean "the single host, whatever it is called", so both
    # have to survive into a DeployerRef with no alias.
    for raw in (None, {}):
        collected = collect_targets(
            [_layer("global", {"targets": {"prod": {"deployer": raw, "stage": "production"}}})]
        )
        spec = collected["prod"].spec
        assert spec.deployer is not None
        assert spec.deployer.host is None


def test_deployer_beside_a_literal_connection_key_is_rejected_by_name() -> None:
    for key, value in (("host", "web-1"), ("user", "deploy"), ("port", 2222), ("path", "/srv/")):
        with pytest.raises(TargetError) as error:
            collect_targets(
                [
                    _layer(
                        "global",
                        {"targets": {"prod": {"deployer": {}, key: value, "stage": "production"}}},
                    )
                ]
            )
        assert key in str(error.value)
        assert "deployer" in str(error.value)


def test_deployer_beside_layout_or_ssh_alias_is_rejected() -> None:
    for key, value in (("layout", "plain"), ("ssh_alias", "prod")):
        with pytest.raises(TargetError) as error:
            collect_targets(
                [
                    _layer(
                        "global",
                        {"targets": {"prod": {"deployer": {}, key: value, "stage": "production"}}},
                    )
                ]
            )
        assert key in str(error.value)


def test_deployer_beside_a_jump_host_is_allowed() -> None:
    # Deployer carries no canonical field for a bastion, so there is nothing
    # for the block to supply and no conflict to have. Rejecting it would put
    # this feature out of reach for every jump-host target.
    collected = collect_targets(
        [
            _layer(
                "global",
                {
                    "targets": {
                        "prod": {
                            "deployer": {},
                            "jump_host": "bastion.example",
                            "jump_host_user": "jump",
                            "stage": "production",
                        }
                    }
                },
            )
        ]
    )
    assert collected["prod"].spec.jump_host == "bastion.example"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/config/test_targets.py -k deployer -v`
Expected: FAIL — `TargetSpec` has no `deployer` field, so pydantic rejects the key and the messages do not match.

- [ ] **Step 3: Add the model and the same-spec validator**

In `src/ddev_sync/config/models.py`, beside `TargetSpec`:

```python
class DeployerRef(BaseModel):
    """A reference to a host in the project's own Deployer configuration.

    `host` is Deployer's *host alias*, which is not necessarily a hostname: a
    project may name its hosts `production` and `staging`. It may be omitted
    when `dep config` reports exactly one host.
    """

    model_config = ConfigDict(extra="forbid")

    host: str | None = None
```

Add the field to `TargetSpec`, and the rejection as a validator on it:

```python
    deployer: DeployerRef | None = None
```

```python
    @model_validator(mode="before")
    @classmethod
    def _deployer_block_accepts_an_empty_value(cls, data: Any) -> Any:
        # `deployer:` with no value parses as None, which pydantic would then
        # read as "the field is absent" - indistinguishable from a target that
        # never mentioned Deployer at all. The two mean different things, so
        # the null is normalised to an empty ref before validation.
        if isinstance(data, dict) and "deployer" in data and data["deployer"] is None:
            data = {**data, "deployer": {}}
        return data

    @model_validator(mode="after")
    def _deployer_owns_the_connection(self) -> "TargetSpec":
        if self.deployer is None:
            return self
        owned = [
            name
            for name in ("host", "user", "port", "path", "layout", "ssh_alias")
            if getattr(self, name) is not None
        ]
        if owned:
            raise ValueError(
                f"a 'deployer:' block owns the connection, so {', '.join(owned)} "
                "must not be set beside it; remove them, or remove the block"
            )
        return self
```

Import `ConfigDict` and `model_validator` from pydantic if the module does not already — check the existing imports rather than adding a duplicate.

**On `exclude_none=True` in `_flatten`:** `config/targets.py:129` dumps each spec with `exclude_none=True`, so an *empty* `DeployerRef` would survive (it is a model, not `None`) while its `host=None` is dropped inside it. That is the behaviour this task wants; do not change that call.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/config/test_targets.py -k deployer -v`
Expected: PASS, all five.

- [ ] **Step 5: Write the failing tests for the chain and for the missing resolver**

```python
def test_a_stronger_layers_deployer_block_beats_an_inherited_literal_host() -> None:
    # `host`, `ssh_alias` and now `deployer` are three ways to say the same
    # thing. targets.py already resolves host-versus-alias by letting the
    # stronger link win, and this follows that rule rather than inventing a
    # second one.
    collected = collect_targets(
        [
            _layer("global", {"targets": {"base": {"host": "old.example", "user": "deploy",
                                                   "path": "/srv/", "stage": "production"}}}),
            _layer("project", {"targets": {"prod": {"extends": "base", "deployer": {"host": "web-1"}}}}),
        ]
    )
    target = resolve_target("prod", collected, resolve_deployer=_fake_resolver())
    assert target.host == "resolved.example"


def test_an_inherited_deployer_block_loses_to_a_stronger_literal_host() -> None:
    collected = collect_targets(
        [
            _layer("global", {"targets": {"base": {"deployer": {"host": "web-1"},
                                                   "stage": "production"}}}),
            _layer("project", {"targets": {"prod": {"extends": "base", "host": "new.example",
                                                    "user": "deploy", "path": "/srv/"}}}),
        ]
    )
    target = resolve_target("prod", collected)
    assert target.host == "new.example"


def test_a_deployer_target_without_a_resolver_says_so() -> None:
    collected = collect_targets(
        [_layer("global", {"targets": {"prod": {"deployer": {}, "stage": "production"}}})]
    )
    with pytest.raises(TargetError) as error:
        resolve_target("prod", collected)
    assert "deployer" in str(error.value).lower()
```

Add the fake resolver beside them:

```python
def _fake_resolver(**overrides: object) -> Callable[[str | None], DeployerConnection]:
    """A resolver that answers without running anything.

    The real one shells into the web container; every test about *targets* is
    about what happens to the answer, not about getting it.
    """

    def resolve(alias: str | None) -> DeployerConnection:
        values: dict[str, object] = {
            "host": "resolved.example",
            "user": "deploy",
            "port": None,
            "path": "/srv/app/",
            "layout": "deployer",
        }
        values.update(overrides)
        return DeployerConnection(**values)  # type: ignore[arg-type]

    return resolve
```

- [ ] **Step 6: Run them and watch them fail**

Run: `uv run pytest tests/config/test_targets.py -k "deployer and (stronger or inherited or without)" -v`
Expected: FAIL — `resolve_target` takes no `resolve_deployer`, and `DeployerConnection` does not exist yet. Task 2 defines it; for now add it as a `@dataclass(frozen=True)` stub in `config/deployer.py` with the five fields so this task's tests can run, and Task 2 fills the module out around it.

- [ ] **Step 7: Implement the chain rule and the substitution**

In `config/targets.py`, extend the existing index tracking in `_flatten` — it already tracks `host_index` and `alias_index`:

```python
    deployer_index: int | None = None
```

track it in the loop alongside the others, and extend the precedence block:

```python
    # `host`, `ssh_alias` and `deployer` are three alternative ways to say
    # "where to connect". Whichever the stronger (later) link set wins, and
    # the others are dropped - a weaker layer must never silently shadow a
    # stronger one's explicit choice, in any direction.
    strongest = max(
        (index, key)
        for index, key in (
            (host_index, "host"),
            (alias_index, "ssh_alias"),
            (deployer_index, "deployer"),
        )
        if index is not None
    )[1] if any(
        index is not None for index in (host_index, alias_index, deployer_index)
    ) else None
    for key in ("host", "ssh_alias", "deployer"):
        if strongest is not None and key != strongest:
            flattened.pop(key, None)
```

Replace the existing two-way `ssh_alias`/`host` block with this three-way one, keeping its comment's reasoning. **Note:** dropping `host` when `deployer` is strongest also drops a `user`/`port`/`path` the base supplied — which is correct and is what strict means, but say so in a comment, because it is the one place where a value disappears without being named in an error.

Then in `_build`, before the alias handling:

```python
    deployer = flattened.pop("deployer", None)
    if deployer is not None:
        if resolve_deployer is None:
            raise TargetError(
                f"target {name!r} (from {source}) has a 'deployer:' block, which has to be "
                "resolved from the project's own Deployer - and nothing here can do that. "
                "This is a bug in ddev-sync, not in your configuration."
            )
        alias = deployer.get("host") if isinstance(deployer, dict) else None
        connection = resolve_deployer(alias)
        flattened["host"] = connection.host
        flattened["user"] = connection.user
        flattened["path"] = connection.path
        flattened["layout"] = connection.layout
        # Omitted, never set to None: `setdefault("port", 22)` below does not
        # replace a key that is present with the value None, so passing the
        # null through would leave port unset, `_ssh_prefix` would omit `-p`
        # entirely, and this target would build a different ssh command than
        # an identical literal one. See the design document.
        if connection.port is not None:
            flattened["port"] = connection.port
```

and thread `resolve_deployer` through `resolve_target`'s signature into `_build`.

- [ ] **Step 8: Run the whole config suite**

Run: `uv run pytest tests/config -q`
Expected: PASS, with the new tests included and nothing else disturbed.

- [ ] **Step 9: Commit**

```bash
git add src/ddev_sync/config/models.py src/ddev_sync/config/targets.py src/ddev_sync/config/deployer.py tests/config/test_targets.py
git commit -m "$(cat <<'MSG'
feat(config): accept a `deployer:` block on a target and guard what it owns

A block declares that this target's host, user, port and path come from the
project's own Deployer, so setting any of them beside it is a contradiction
and is rejected by name. `layout` goes the same way, because it is derived
from Deployer's own output rather than stated.

A jump host is deliberately still allowed: Deployer has no canonical field for
a bastion, so there is nothing for the block to supply and no conflict to
have, and rejecting it would put this feature out of reach for every
jump-host target.

Across an `extends` chain the block is a third way of saying "where to
connect", so it follows the rule already there for host versus ssh_alias:
whichever the stronger link set wins and the others are dropped.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 2: The pure parser

Turn `dep config --format=json` output into a `DeployerConnection`. No I/O, and the fixture is real output from a real project, which is the whole point — an invented fixture has neither a null port nor an error object, and the implementation would be written against a shape that does not occur.

**Files:**
- Create: `src/ddev_sync/config/deployer.py` (fill out the stub from Task 1)
- Create: `tests/config/test_deployer.py`
- Create: `tests/config/fixtures/dep-config-t3-13-ssg.json`

**Interfaces:**
- Consumes: nothing from Task 1 but the `DeployerConnection` stub it placed here.
- Produces: `DeployerConnection` (frozen dataclass: `host: str`, `user: str | None`, `port: int | None`, `path: str`, `layout: str`) and `parse_dep_config(payload: str, alias: str | None) -> DeployerConnection`, raising `DeployerError` on anything unusable. `DeployerError` subclasses the project's base error — check `src/ddev_sync/errors.py` and follow how `ConfigError` and `TargetError` are declared.

- [ ] **Step 1: Save the real fixture**

Create `tests/config/fixtures/dep-config-t3-13-ssg.json` with exactly this content. It is real output, trimmed to the keys that matter, and every oddity in it is load-bearing:

```json
{
  "web-node-1.noidee.network": {
    "alias": "web-node-1.noidee.network",
    "hostname": "web-node-1.noidee.network",
    "remote_user": "ssg-kevelaer",
    "port": null,
    "deploy_path": "/home/ssg-kevelaer/htdocs/deployer",
    "current_path": "/home/ssg-kevelaer/htdocs/deployer/current",
    "release_path": {
      "error": "The \"release_path\" (/home/ssg-kevelaer/htdocs/deployer/release) does not exist."
    },
    "shared_path": null,
    "bin/php": "/usr/bin/php8.4"
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/config/test_deployer.py`:

```python
"""The pure half of the Deployer integration: parsing, selecting, deriving.

The fixture is real `dep config --format=json` output from a TYPO3 13 project
deployed with Deployer 7.5. Three things in it are why it is real and not
invented: `port` is JSON null rather than absent, `release_path` resolved to an
error object while the command itself exited zero, and `shared_path` is null
even though the remote plainly has a shared directory.
"""

import json
from pathlib import Path

import pytest

from ddev_sync.config.deployer import DeployerError, parse_dep_config

FIXTURE = Path(__file__).parent / "fixtures" / "dep-config-t3-13-ssg.json"


def _fixture() -> str:
    return FIXTURE.read_text()


def _payload(**overrides: object) -> str:
    """The fixture's single host with some keys replaced."""
    data = json.loads(_fixture())
    host, cfg = next(iter(data.items()))
    return json.dumps({host: {**cfg, **overrides}})


def test_it_reads_the_four_connection_values_from_real_output() -> None:
    connection = parse_dep_config(_fixture(), None)
    assert connection.host == "web-node-1.noidee.network"
    assert connection.user == "ssg-kevelaer"
    assert connection.path == "/home/ssg-kevelaer/htdocs/deployer"


def test_a_null_port_becomes_none_and_never_the_string_none() -> None:
    connection = parse_dep_config(_fixture(), None)
    assert connection.port is None


def test_an_explicit_port_is_read_as_an_integer() -> None:
    connection = parse_dep_config(_payload(port=2222), None)
    assert connection.port == 2222


def test_the_layout_is_derived_from_current_path() -> None:
    assert parse_dep_config(_fixture(), None).layout == "deployer"


def test_a_current_path_that_is_not_deploy_path_plus_current_is_plain() -> None:
    payload = _payload(current_path="/home/ssg-kevelaer/htdocs/deployer")
    assert parse_dep_config(payload, None).layout == "plain"


def test_an_unusable_current_path_fails_rather_than_guessing_a_layout() -> None:
    # Guessing wrong sends every file path to the wrong place, so this is one
    # of the cases that must not be tolerated.
    payload = _payload(current_path=None)
    with pytest.raises(DeployerError) as error:
        parse_dep_config(payload, None)
    assert "layout" in str(error.value)


def test_an_error_object_in_a_read_key_fails_with_deployers_own_text() -> None:
    payload = _payload(deploy_path={"error": "The \"deploy_path\" is not set."})
    with pytest.raises(DeployerError) as error:
        parse_dep_config(payload, None)
    assert "deploy_path" in str(error.value)
    assert "is not set" in str(error.value)


def test_an_error_object_in_an_unread_key_is_ignored() -> None:
    # `release_path` in the fixture is exactly this, and it must not stop a
    # resolution that does not need it.
    assert parse_dep_config(_fixture(), None).host == "web-node-1.noidee.network"


def test_a_named_alias_that_is_present_is_used() -> None:
    connection = parse_dep_config(_fixture(), "web-node-1.noidee.network")
    assert connection.host == "web-node-1.noidee.network"


def test_a_named_alias_that_is_absent_lists_what_was_found() -> None:
    with pytest.raises(DeployerError) as error:
        parse_dep_config(_fixture(), "staging")
    assert "staging" in str(error.value)
    assert "web-node-1.noidee.network" in str(error.value)


def test_several_hosts_without_a_named_alias_lists_them_all() -> None:
    data = json.loads(_fixture())
    host, cfg = next(iter(data.items()))
    payload = json.dumps({host: cfg, "staging.example": cfg})
    with pytest.raises(DeployerError) as error:
        parse_dep_config(payload, None)
    assert "staging.example" in str(error.value)
    assert host in str(error.value)


def test_no_hosts_at_all_says_so() -> None:
    with pytest.raises(DeployerError) as error:
        parse_dep_config("{}", None)
    assert "no hosts" in str(error.value).lower()


def test_output_that_is_not_json_fails_with_what_was_received() -> None:
    with pytest.raises(DeployerError) as error:
        parse_dep_config("Deployer 7.5.12\nUsage: dep [options]", None)
    assert "Usage: dep" in str(error.value)


def test_json_that_is_not_a_mapping_of_hosts_fails() -> None:
    with pytest.raises(DeployerError) as error:
        parse_dep_config("[1, 2, 3]", None)
    assert "mapping" in str(error.value)


def test_a_missing_hostname_falls_back_to_the_alias_key() -> None:
    # Deployer omits `hostname` when a host is declared by alias only. The key
    # the output is indexed by is then the only name available, and it is the
    # one Deployer itself would connect to.
    payload = _payload(hostname=None)
    assert parse_dep_config(payload, None).host == "web-node-1.noidee.network"


def test_a_missing_remote_user_is_allowed_and_leaves_ssh_to_decide() -> None:
    payload = _payload(remote_user=None)
    assert parse_dep_config(payload, None).user is None
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/config/test_deployer.py -v`
Expected: FAIL — `parse_dep_config` and `DeployerError` do not exist.

- [ ] **Step 4: Implement the parser**

Write `src/ddev_sync/config/deployer.py`. The shape below is the contract; fill in the bodies.

```python
"""Parse `dep config --format=json` into a sync target's connection details.

Nothing here executes anything - `transport/deployer.py` runs the command and
hands the text over. That split is the same one `transport/ssh.py` makes, and
it is what lets every shape this parser has to survive be a string in a test
instead of a project on a disk.

What the real output forces, all three verified against a TYPO3 13 project
deployed with Deployer 7.5:

- `port` arrives as JSON null rather than absent, so "not set" is a value that
  has to be recognised rather than a key that is missing.
- A single key's value can be an error object - `{"error": "..."}` - while the
  command itself exits zero. So a value that is neither a string nor null is
  not a crash and not usable: for a key this parser reads it is a failure with
  Deployer's own text, and for one it does not read it is ignored.
- `shared_path` is null even on a remote that plainly has a shared directory,
  which is why the layout is derived from `current_path` and `deploy_path`
  alone.
"""

import json
from dataclasses import dataclass
from typing import Any

from ddev_sync.errors import DdevSyncError


class DeployerError(DdevSyncError):
    """Deployer could not supply a usable connection."""


@dataclass(frozen=True)
class DeployerConnection:
    """The four connection values, plus the layout read off the remote's shape."""

    host: str
    user: str | None
    port: int | None
    path: str
    layout: str


def parse_dep_config(payload: str, alias: str | None) -> DeployerConnection:
    """Read one host's connection out of `dep config --format=json` output."""
    hosts = _hosts(payload)
    name, config = _select(hosts, alias)
    host = _text(config, "hostname") or name
    path = _required_text(config, "deploy_path")
    return DeployerConnection(
        host=host,
        user=_text(config, "remote_user"),
        port=_port(config),
        path=path,
        layout=_layout(config, path),
    )
```

Each helper has one job and one failure message:

- `_hosts(payload)` — `json.loads`, failing with the received text (truncated to something a terminal can hold) when it is not JSON, and with "mapping" when the top level is not one.
- `_select(hosts, alias)` — no hosts at all; an alias that is absent, listing every alias found; no alias with several hosts, listing them all; otherwise the single host or the named one.
- `_text(config, key)` — a string, or `None` for a null or a missing key. An error object raises.
- `_required_text(config, key)` — as `_text`, but `None` raises.
- `_port(config)` — `None` for null or missing; an `int` otherwise; a non-integer raises.
- `_layout(config, path)` — `"deployer"` when `current_path` equals `path` joined with `current`, else `"plain"`. An unusable `current_path` raises, naming the layout as what could not be determined. Join with care: `deploy_path` may or may not end in a slash, and `posixpath.join` is the right tool — these are remote paths and must not pick up the host's separator.

When an error object appears in a key this parser reads, include the error's own text; the shape is `{"error": "..."}` but treat a missing `error` key defensively rather than crashing on Deployer's internals.

Add `DeployerError` to `src/ddev_sync/errors.py` if the project's convention is to declare errors centrally — read that file first and follow it.

- [ ] **Step 5: Run them and watch them pass**

Run: `uv run pytest tests/config/test_deployer.py -v`
Expected: PASS, all seventeen.

- [ ] **Step 6: Check coverage of this module alone**

Run: `uv run pytest tests/config/test_deployer.py --cov=ddev_sync.config.deployer --cov-report=term-missing -q`
Expected: 100 %, no missing lines and no partial branches. If a branch is uncovered, it is a shape the parser handles that no test describes — add the test rather than removing the branch, unless the branch is genuinely unreachable, in which case remove it.

- [ ] **Step 7: Commit**

```bash
git add src/ddev_sync/config/deployer.py src/ddev_sync/errors.py tests/config/test_deployer.py tests/config/fixtures/dep-config-t3-13-ssg.json
git commit -m "$(cat <<'MSG'
feat(config): parse Deployer's resolved configuration into a connection

Pure, and tested against real output rather than an imagined shape. Three
things in that output decided the implementation: a port that arrives as JSON
null instead of absent, a key whose value is an error object while the command
itself exits zero, and a null `shared_path` on a remote that plainly has one -
which is why the layout is derived from `current_path` alone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 3: The I/O edge, and wiring it in

Run `dep config` in the web container, memoise it for the run, and make `build_context` able to hand the resolver to `resolve_target`. After this task a hand-written `deployer:` block actually works.

**Files:**
- Create: `src/ddev_sync/transport/deployer.py`
- Create: `tests/transport/test_deployer.py`
- Modify: `src/ddev_sync/cli/context.py:32-43` (signature) and `:83-85` (the call)
- Modify: `src/ddev_sync/cli/commands.py` (pass a runner)
- Test: `tests/cli/test_context.py`

**Interfaces:**
- Consumes: `DeployerConnection` and `parse_dep_config` from Task 2; `resolve_target(..., resolve_deployer=...)` from Task 1.
- Produces: `make_deployer_resolver(project: DdevProject, runner: CommandRunner) -> Callable[[str | None], DeployerConnection]`. `build_context` gains `runner: CommandRunner | None = None`.

- [ ] **Step 1: Write the failing tests for the edge**

The fake runner is `FakeCommandRunner` in `src/ddev_sync/transport/fake.py` — it is a real `CommandRunner` that records instead of executing, and an **unscripted command raises** `UnscriptedCommand`, so a test cannot silently accept a command it did not expect. Its interface: `.script(argv, *, returncode=0, stdout="", stderr="")` for an exact match, `.script_prefix(prefix, ...)` for a prefix, `.calls` for the argument vectors in order, `.invocations` for the full records.

`web_command` wraps an argv as `["docker", "exec", "-u", "<uid>:<gid>", "-w", <workdir>, <container>, *argv]`, and the uid comes from the running user — so assert on the tail and on the container's presence, never on the whole vector.

Create `tests/transport/test_deployer.py`:

```python
"""The one part of the Deployer integration that runs anything."""

import json
from pathlib import Path

import pytest

from ddev_sync.config.deployer import DeployerError
from ddev_sync.transport.deployer import make_deployer_resolver
from ddev_sync.transport.fake import FakeCommandRunner

PAYLOAD = json.dumps(
    {
        "web-1": {
            "hostname": "web-1.example",
            "remote_user": "deploy",
            "port": None,
            "deploy_path": "/srv/app",
            "current_path": "/srv/app/current",
        },
        "staging": {
            "hostname": "staging.example",
            "remote_user": "deploy",
            "port": None,
            "deploy_path": "/srv/staging",
            "current_path": "/srv/staging/current",
        },
    }
)

VENDOR = ("vendor/bin/dep", "config", "--format=json")
ON_PATH = ("dep", "config", "--format=json")


def _project():
    """A project fixture; follow whatever tests/ already uses for DdevProject."""
    raise NotImplementedError  # replace with the existing helper


def test_it_runs_dep_in_the_web_container_from_the_project_root() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(_project(), runner)

    resolve(None) if False else resolve("web-1")

    argv = runner.calls[0]
    assert argv[:2] == ("docker", "exec")
    assert "/var/www/html" in argv
    assert any(part.startswith("ddev-") and part.endswith("-web") for part in argv)
    assert argv[-3:] == VENDOR


def test_it_falls_back_to_a_dep_on_the_path_when_vendor_bin_is_absent() -> None:
    # Finding the tool is not the same as substituting a value, which is why
    # this is the one alternative the strict rule allows.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stderr="not found")
    resolve = make_deployer_resolver(_project(), runner)

    with pytest.raises(DeployerError):
        resolve("web-1")

    assert [call[-3:] for call in runner.calls] == [VENDOR, ON_PATH]


def test_a_recipe_that_fails_does_not_fall_through_to_the_second_candidate() -> None:
    # Exit 127 means the interpreter could not find the program. Any other
    # non-zero means dep ran and its own evaluation failed - trying the next
    # candidate would run a broken recipe twice and report the wrong cause.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=1, stderr="PHP Fatal error in deploy.php")
    resolve = make_deployer_resolver(_project(), runner)

    with pytest.raises(DeployerError) as error:
        resolve("web-1")

    assert "deploy.php" in str(error.value)
    assert len(runner.calls) == 1


def test_neither_candidate_present_names_both_places_it_looked() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], returncode=127, stderr="not found")
    resolve = make_deployer_resolver(_project(), runner)

    with pytest.raises(DeployerError) as error:
        resolve("web-1")

    assert "vendor/bin/dep" in str(error.value)
    assert "PATH" in str(error.value)


def test_the_result_is_resolved_once_and_then_reused() -> None:
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(_project(), runner)

    first = resolve("web-1")
    second = resolve("web-1")

    assert first == second
    assert len(runner.calls) == 1


def test_a_second_alias_is_served_from_the_same_single_invocation() -> None:
    # One `dep config` returns every host, and the command evaluates the
    # project's own recipe - so asking for a second alias must not be a second
    # chance to get a different answer inside one run.
    runner = FakeCommandRunner()
    runner.script_prefix(["docker", "exec"], stdout=PAYLOAD)
    resolve = make_deployer_resolver(_project(), runner)

    assert resolve("web-1").host == "web-1.example"
    assert resolve("staging").host == "staging.example"
    assert len(runner.calls) == 1
```

Replace `_project()` with the project helper the test suite already has — grep `tests/` for how other transport tests build a `DdevProject` and use that, rather than writing a second one. Delete the `resolve(None) if False else` oddity in the first test; it is there only so the example reads unambiguously, and the real call is `resolve("web-1")`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/transport/test_deployer.py -v`
Expected: FAIL — `make_deployer_resolver` does not exist.

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/transport/test_deployer.py -v`
Expected: FAIL — `make_deployer_resolver` does not exist.

- [ ] **Step 4: Implement the edge**

```python
"""Ask the project's own Deployer where it deploys.

The only part of this feature that runs anything. It runs in the project's web
container, not on the host and not in the sync sidecar: the sidecar carries no
PHP by design, so `vendor/bin/dep` is visible from it but not executable, and
the host's PHP is not the one the project pinned. Framework commands already
run there for the same reason.

The result is memoised for the life of the resolver, which is one run. A single
`dep config` returns every host, so a second target asking for a second alias
is served from the same invocation - and `dep config` evaluates the project's
recipe, so repeating it would be both slow and a second chance to get a
different answer inside one sync.
"""
```

`make_deployer_resolver(project, runner)` returns a closure over a one-slot cache holding the parsed payload text. On the first call it tries `vendor/bin/dep`, then `dep`, each wrapped by `web_command(project, [...], workdir=Path("/var/www/html"))`; on a non-zero exit from the *first* candidate it must distinguish "not found" from "ran and failed" — a `dep` that exists and whose recipe is broken must not cause a silent fall through to the second candidate. Use the runner's `CommandResult` and `CommandFailed` as the rest of the codebase does, and read `transport/runner.py` for which it raises when.

Then `parse_dep_config(payload, alias)` per call, so two aliases share one invocation.

- [ ] **Step 5: Run them and watch them pass**

Run: `uv run pytest tests/transport/test_deployer.py -v`
Expected: PASS.

- [ ] **Step 6: Wire it into `build_context` with a failing test first**

```python
def test_build_context_resolves_a_deployer_target_through_the_runner() -> None:
    ...   # a FakeRunner answering `dep config`, a project fixture with a
          # `deployer:` block, asserting the resulting target's host
```

Then add `runner: CommandRunner | None = None` to `build_context`, build the resolver when it is given, and pass it:

```python
    target = resolve_target(
        _pick_target_name(target_name, user_layers),
        collect_targets(user_layers),
        resolve_deployer=(
            None if runner is None else make_deployer_resolver(project, runner)
        ),
    )
```

In `cli/commands.py`, pass `SubprocessRunner()` at every `build_context` call. There is already one at `commands.py:155` for the `ExecutionContext`; **use one instance for both** rather than constructing a second, so a single run has a single runner.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS at 100 % coverage.

- [ ] **Step 8: Commit**

```bash
git add src/ddev_sync/transport/deployer.py src/ddev_sync/cli/context.py src/ddev_sync/cli/commands.py tests/transport/test_deployer.py tests/cli/test_context.py
git commit -m "$(cat <<'MSG'
feat(transport): resolve a deployer target by asking the project's Deployer

`dep config --format=json` runs in the project's web container, which is where
its PHP and its pinned Deployer are - the sync sidecar has no PHP at all, so
vendor/bin/dep is visible from there but not executable.

Memoised for the run. One `dep config` returns every host, so a second target
is served from the same invocation, and since the command evaluates the
project's own recipe, repeating it would be a second chance to get a different
answer inside one sync.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 4: Making it legible

A `deployer:` target has to read as a deliberate choice in both places that list targets. Neither the dry run nor `doctor` needs code: the dry-run header reads the resolved target, which is now resolved, and `doctor` is covered below.

**Files:**
- Modify: `src/ddev_sync/cli/render.py:132-146` (`render_targets`)
- Modify: `src/ddev_sync/cli/commands.py:30-41` (`describe_targets`)
- Test: `tests/cli/test_render.py`, `tests/cli/test_commands.py`

**Interfaces:**
- Consumes: `DeployerRef` on `TargetSpec` (Task 1).
- Produces: nothing later tasks depend on.

**Why there is no `doctor` row, against what the spec says.** The spec gives `doctor` a conditional `deployer config` row. It cannot work, and the plan drops it:

`doctor_command` (`cli/commands.py:51`) *receives* an already-built `RunContext`. Resolution happens inside `build_context`, and a failure raises `TargetError` — so `doctor_command` is never reached. The row could therefore only ever say `OK`, a `bool` field whose `False` branch is dead code, which under this repository's 100 % branch-coverage rule is a branch no test can legitimately cover. A test asserting the failure row would be asserting an unreachable state.

What the row would have added beyond that is provenance — which alias and which binary answered — and that needs a new `RunContext` field threaded into preflight purely to print one line. The resolved endpoint is already visible in the dry-run header, and the declaration in the listing. So: no row, no `PreflightReport` change, and a resolution failure stays what strict means — a hard error with a reason, before anything else runs.

- [ ] **Step 1: Write the failing tests for both listings**

In `tests/cli/test_render.py`:

```python
def test_render_targets_shows_a_deployer_block_as_a_declaration() -> None:
    # The listing does no I/O and must not start to: a stopped project has to
    # be able to list its own configuration. So this shows what was declared,
    # not what it resolves to - the dry-run header shows the resolution.
    collected = {
        "prod": CollectedTarget(
            spec=TargetSpec(deployer=DeployerRef(host="web-1"), stage="production"),
            source="~/.ddev/ddev-sync/config.yaml",
        )
    }
    line = render_targets(collected)
    assert "from deployer: web-1" in line
    assert "?" not in line


def test_render_targets_shows_a_deployer_block_with_no_alias() -> None:
    # Without the branch this renders `?:?`, which reads like a broken
    # configuration rather than a deliberate one.
    collected = {
        "prod": CollectedTarget(
            spec=TargetSpec(deployer=DeployerRef(), stage="production"),
            source="~/.ddev/ddev-sync/config.yaml",
        )
    }
    line = render_targets(collected)
    assert "from deployer" in line
    assert "?" not in line
```

In `tests/cli/test_commands.py`, for the picker — follow the file's existing way of writing a config into a tmp path:

```python
def test_describe_targets_shows_a_deployer_block_as_a_declaration(tmp_path: Path) -> None:
    # `describe_targets` feeds the interactive target picker and carries the
    # same `spec.host or "?"` pattern as the renderer, so it needs the same
    # branch - otherwise the picker offers "?:?" as a choice.
    ...  # write a global config with a `deployer: {host: web-1}` target
    described = describe_targets(project_root, home)
    assert described["prod"] == "from deployer: web-1"
```

Replace the `...` with the file's own fixture idiom before running.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/cli/test_render.py tests/cli/test_commands.py -k deployer -v`
Expected: FAIL — both produce `?:?`.

- [ ] **Step 3: Implement the branch in both places**

The two sites build the same string from the same three fields. Rather than writing the branch twice, extract one small helper and call it from both — `render.py` is the natural home, and `commands.py` already imports from it.

```python
def describe_endpoint(spec: TargetSpec) -> str:
    """One line saying where a target points, for a listing or a picker.

    A `deployer:` block is shown as the declaration it is. Resolving it would
    mean running `dep config` in the web container, and both callers are
    expected to work on a stopped project - a listing that cannot run without
    the project running is not a listing.
    """
    if spec.deployer is not None:
        alias = "" if spec.deployer.host is None else f": {spec.deployer.host}"
        return f"from deployer{alias}"
    host = spec.ssh_alias or spec.host or "?"
    user = "" if spec.ssh_alias or not spec.user else f"{spec.user}@"
    return f"{user}{host}:{spec.path or '?'}"
```

Then `render_targets` uses it for the endpoint column, and `describe_targets` returns it directly. Deleting the duplicated expression from both is part of this step — leaving one behind is how they drift.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/cli -q`
Expected: PASS, with the existing listing tests unchanged — the helper must produce byte-identical output for every literal target, including the `?` fallbacks.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS at 100 % coverage.

- [ ] **Step 6: Commit**

```bash
git add src/ddev_sync/cli/render.py src/ddev_sync/cli/commands.py tests/cli/test_render.py tests/cli/test_commands.py
git commit -m "$(cat <<'MSG'
feat(cli): show a deployer target as the declaration it is

Both places that list targets - `ddev sync targets` and the interactive picker
- built the endpoint from `spec.host or "?"`, so a target whose connection
comes from Deployer rendered as `?:?`: a deliberate choice shown as a mistake.

They show the declaration rather than the resolution on purpose. Resolving
means running `dep config` in the web container, and a listing that cannot run
on a stopped project is not a listing. The resolved endpoint is in the dry-run
header, which already does work.

The expression was duplicated in both callers; it is one helper now, because
two copies of the same three-field string are two copies to keep in step.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 5: Documentation, and the spec's three corrections

**Files:**
- Modify: `docs/specs/2026-10-06-deployer-connection-block-design.md`
- Modify: `ddev-sync/ddev-sync.example.yaml`
- Modify: in `/Users/noidee/_dev/noix-docs` (branch `ddev-sync`): `src/content/docs/ddev-sync/getting-started/configuration.md`

- [ ] **Step 1: Amend the spec**

Add a short "Amendments" section recording all three corrections from this plan's own header: that `targets` shows the declaration rather than the resolution, that there is no `doctor` row because a failed resolution aborts before `doctor` runs, and that the `extends` chain follows the existing stronger-link-wins rule with rejection only within one spec. A spec that silently disagrees with the code is worse than no spec.

- [ ] **Step 2: Show the block in the installed template**

`ddev-sync/ddev-sync.example.yaml` is installed into every project's `.ddev/`. Add the `deployer:` form as a commented alternative beside the literal `targets:` example, with one line on what it means. Keep it inside the header block that the install action strips — read `install.yaml`'s copy action first to see which lines survive into the user's own file, and check your addition lands on the right side of that boundary.

- [ ] **Step 3: Document it where targets are documented**

In `noix-docs`, `getting-started/configuration.md` is the page that explains the keys and the layer table. Add the `deployer:` form: what it does, that it is strict and never falls back, that resolution happens in the web container and therefore needs the project running, that `stage` is still required, and that a jump host may be combined with it. Mention that `dep config` evaluates the project's own recipe, which is also why a dry run of such a target is not entirely inert.

- [ ] **Step 4: Build the docs**

Run: `npm run build` in `/Users/noidee/_dev/noix-docs`
Expected: completes, and the link validator reports all internal links valid.

- [ ] **Step 5: Commit, separately per repository**

```bash
cd /Users/noidee/_dev/ddev-sync
git add docs/specs ddev-sync/ddev-sync.example.yaml
git commit -m "$(cat <<'MSG'
docs: record what the code taught the deployer-block design

Two of the spec's statements did not survive contact with the code: the target
listing does no I/O and must not start to, so it shows the declaration rather
than the resolution; and an `extends` chain needs the precedence rule this
codebase already has for host versus ssh_alias, not a flat rejection.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

```bash
cd /Users/noidee/_dev/noix-docs
git add src/content/docs/ddev-sync/getting-started/configuration.md
git commit -m "$(cat <<'MSG'
docs(ddev-sync): a target can take its connection from Deployer

What the block does, that it never falls back to a literal value, and that
resolution runs in the web container - so the project has to be running, and a
dry run of such a target evaluates the project's own recipe rather than being
entirely inert.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

## Verification checklist for the whole plan

- [ ] A hand-written `deployer:` block in `t3-13-ssg`'s global config resolves, and the dry-run plan and `ddev sync doctor` produce what the literal target produced, while `ddev sync targets` shows the declaration — that is the acceptance evidence, and it is available because the add-on was already exercised against a real production host
- [ ] `ddev sync targets` still works with the project **stopped**
- [ ] Every rejection in the spec's validation table produces a message naming the offending key
- [ ] `dep config` runs **once** per sync, asserted by a test counting invocations
- [ ] A `deployer:` target and an equivalent literal target build byte-identical ssh commands — the null-port trap
- [ ] `uv run pytest` at 100 % line and branch coverage; ruff, format and mypy clean
- [ ] `npm run build` clean in `noix-docs`
- [ ] Linear history, every commit Conventional with the `Co-Authored-By` footer, nothing pushed

## Not in this plan

- **`ddev sync init`.** Its own piece of work, with the checklist in §8 of `2026-10-05-ddev-sync-findings-from-the-matrix.md`. It will *offer* this block rather than reinvent it, and it also owns the engine's current habit of advising a command that does not exist.
- **A second deployment tool.** The block shape leaves room for one; nothing here prepares for it.
- **Importing database credentials.** Deployer does not have them — in the verified output `db_name`, `db_user` and `db_password` are unresolved interactive prompts.
