# Docker Sync Provider, Engine Side — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a target declare `provider: docker` and have it resolve into the
jump-host shape the engine can already express, so a sync reaches an
SSH-speaking endpoint inside a Docker stack without anyone holding Docker
rights on the production host.

**Architecture:** The provider is a *resolution-time* translation. `_build` in
`config/targets.py` already turns declared shapes into connection fields — a
`deployer:` block and an `ssh_alias` both rewrite the flattened mapping there —
and `provider: docker` is the same kind of rewrite: the declared host becomes
the jump host, and the destination becomes the endpoint behind it. Below that
line nothing knows a provider exists.

**Tech Stack:** pydantic models, pytest, the existing `transport/ssh.py` jump
flag, the existing integration compose stack.

**Spec:** [docs/specs/2026-10-16-docker-sync-provider-design.md](../specs/2026-10-16-docker-sync-provider-design.md)

**This is plan 1 of 2.** Out of scope here, and in the spec's own words
"decision 10": the endpoint image, the compose template, host-key persistence,
the matrix cell and the release pipeline. Plan 1 is complete and useful
without them — the integration suite stands in for the endpoint with the
`linuxserver/openssh-server` image it already runs.

## Global Constraints

- 100 % line AND branch coverage on `src/ddev_sync`, enforced by
  `--cov-fail-under=100` in `pyproject.toml`'s `addopts`, with `branch = true`.
- `# pragma: no cover` must not work: `exclude_lines` is deliberately empty.
- ruff: `line-length = 100`, `select = ["E","F","I","UP","B","SIM","RUF","ANN"]`,
  `ignore = ["ANN401"]`. Every function, tests included, carries full
  annotations.
- mypy `strict = true` over `src/ddev_sync`, `tests`, `tools`.
- `addopts` carries `-q`, which cancels a single `-v`; use `-vv` for per-test
  lines. Integration tests are deselected by default and run with
  `-m integration --no-cov`.
- Conventional Commits; every commit message ends with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Everything code-side in English.
- Commit, never push.
- **No `if provider == …` below the resolution.** If a task finds itself
  wanting one in `operations/`, `transport/`, the executor or the reporter,
  that is the signal the translation is in the wrong place — stop and say so.

## Review Focus

Five conditions the spec implies and whose absence would bite someone. Each
has a test in the task that owns it.

1. **`provider: docker` with no `sync_port`.** Port would silently default to
   22 and the sync would connect to the *host's* sshd as the endpoint user —
   a confusing authentication failure at best, the wrong machine at worst.
   → Task 2.
2. **`provider: docker` with `jump_host` already set.** The provider wants to
   set the jump; a second one is the two-hop chain the spec defers. Refuse
   rather than silently overwrite what the author wrote. → Task 2.
3. **`provider: docker` together with `ssh_alias`.** The alias must become the
   *jump*, not the destination — it names the Docker host. → Task 1.
4. **A path that exists but cannot be read.** This is the uid mismatch the
   spec calls the likeliest misconfiguration, and today's probe answers only
   "is it a directory". → Task 3.
5. **`provider: server` is untouched.** Every existing configuration must
   resolve byte-identically, or this plan has broken every user to serve a new
   one. → Task 1.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/ddev_sync/config/models.py` (modify) | `Provider` enum; `provider`/`sync_port` on `TargetSpec`; `provider` on `ResolvedTarget` |
| `src/ddev_sync/config/targets.py` (modify, `_build` at `:284`) | The translation and the four target-level refusals |
| `src/ddev_sync/engine/plan.py` (modify) | The one refusal only the plan can see: `wp_cli` under `docker` |
| `src/ddev_sync/operations/preflight.py` (modify, `_probe` at `:213`) | Third probe outcome: present but unreadable |
| `src/ddev_sync/cli/render.py` (modify, `render_doctor` at `:163`) | The unreadable row, and endpoint wording for a docker target |
| `tests/integration/docker-compose.yaml` (modify) | A second sshd in front of the existing one |
| `tests/integration/test_jump_host.py` (create) | The first real `-J` connection in this project |

---

## Task 1: The provider translation

**Files:**
- Modify: `src/ddev_sync/config/models.py`, `src/ddev_sync/config/targets.py:284-330`
- Test: `tests/config/test_targets.py`, `tests/config/test_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, and Tasks 2-4 rely on these names:
  - `Provider(StrEnum)` in `config/models.py` with members `SERVER = "server"`
    and `DOCKER = "docker"`.
  - `ENDPOINT_USER = "sync"` in `config/models.py` — the account the endpoint
    image offers. Fixed, not configurable; the spec says why.
  - `TargetSpec.provider: Provider = Provider.SERVER` and
    `TargetSpec.sync_port: int | None = None`.
  - `ResolvedTarget.provider: Provider = Provider.SERVER`, carrying a comment
    that it exists for human-facing text only and that no command builder may
    read it.

- [ ] **Step 1: Write the failing resolution test**

In `tests/config/test_targets.py`:

```python
def test_a_docker_target_resolves_to_the_jump_host_shape() -> None:
    resolved = resolve_for(
        {"provider": "docker", "host": "prod.example", "user": "deploy",
         "sync_port": 2222, "path": "/data/", "stage": "production"}
    )
    assert (resolved.jump_host, resolved.jump_host_user, resolved.jump_host_port) == (
        "prod.example", "deploy", 22
    )
    assert (resolved.host, resolved.user, resolved.port) == ("127.0.0.1", "sync", 2222)
    assert resolved.provider is Provider.DOCKER
```

Use the file's existing helper for building a resolved target from one
mapping; add `resolve_for` only if none exists.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/config/test_targets.py -vv -k docker_target_resolves`
Expected: FAIL — `TargetSpec` forbids extra keys, so `provider` is rejected.

- [ ] **Step 3: Add `Provider`, `ENDPOINT_USER` and the three fields**

In `config/models.py`, beside `Stage` and `Layout`.

- [ ] **Step 4: Translate in `_build`, after the alias branch and before the `missing` check**

`_build` already rewrites `flattened` for `deployer` and `ssh_alias`; this is
the third such rewrite and belongs in the same place. Translating *after* the
alias branch is what makes Review Focus 3 fall out: whatever ended up in
`host`/`user` is the Docker host, alias or not, and that is what becomes the
jump.

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/config/test_targets.py -vv -k docker_target_resolves`
Expected: PASS.

- [ ] **Step 6: Pin Review Focus 3 — the alias becomes the jump**

```python
def test_a_docker_target_with_an_ssh_alias_jumps_through_the_alias() -> None:
    resolved = resolve_for(
        {"provider": "docker", "ssh_alias": "prod", "sync_port": 2222,
         "path": "/data/", "stage": "production"}
    )
    assert resolved.jump_host == "prod"
    assert resolved.jump_host_user is None
    assert (resolved.host, resolved.port) == ("127.0.0.1", 2222)
    assert resolved.via_ssh_alias is False
```

`via_ssh_alias` is about the *destination*, which is an address and not an
alias — assert it, because the alias branch sets it and the translation has to
take it back.

- [ ] **Step 7: Pin Review Focus 5 — a server target is untouched**

```python
def test_a_server_target_resolves_exactly_as_before() -> None:
    fields = {"host": "web01.example", "user": "deploy", "port": 22,
              "path": "/srv/app/", "stage": "production"}
    resolved = resolve_for(fields)
    assert (resolved.host, resolved.user, resolved.port) == ("web01.example", "deploy", 22)
    assert resolved.jump_host is None
    assert resolved.provider is Provider.SERVER
```

- [ ] **Step 8: Assert the argv the translation ultimately produces**

In `tests/transport/test_ssh.py`, so the chain from configuration to command
line is pinned in one place:

```python
def test_a_docker_target_builds_a_jumped_ssh_command() -> None:
    argv = ssh_command(docker_target(), ["sh", "-c", "true"])
    assert "-J" in argv
    assert argv[argv.index("-J") + 1] == "deploy@prod.example:22"
    assert "sync@127.0.0.1" in argv
    assert argv[argv.index("-p") + 1] == "2222"
```

- [ ] **Step 9: Run the whole gate**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Expected: all pass, 100 % coverage.

- [ ] **Step 10: Commit**

```bash
git add src/ddev_sync/config tests/config tests/transport
git commit -m "feat(config): resolve a docker target into the jump-host shape"
```

The message body says what the translation is and why it lives in `_build`:
the engine already reaches a host behind a host, and ProxyJump resolves the
final hop on the jump host, which is exactly where the stack publishes the
endpoint.

---

## Task 2: The refusals

**Files:**
- Modify: `src/ddev_sync/config/targets.py` (`_build`), `src/ddev_sync/engine/plan.py`
- Test: `tests/config/test_targets.py`, `tests/engine/test_plan.py`

**Interfaces:**
- Consumes: `Provider`, `TargetSpec.sync_port` from Task 1.
- Produces: nothing later tasks read.

**The spec lists three refusals; this task implements five.** The two extra
ones — a docker target with no `sync_port`, and one whose `jump_host` is
already set — come from this plan's Review Focus rather than from the spec,
because each is a configuration that would otherwise connect somewhere
plausible and wrong rather than fail. They are additions to the spec's intent,
not departures from it, and they belong in the same commit as the three it
names.

Four refusals raise `TargetError` from `_build`, because the target carries
everything they judge. The fifth raises `ConfigError` from `build_plan`,
because `database.remote_credentials` lives in the merged config, which
`_build` never sees. That split is the reason this task touches two files, and
the commit message should say so.

- [ ] **Step 1: Write all five failing tests**

In `tests/config/test_targets.py`, each asserting `TargetError` and a
substring of the message that names the way out:

```python
def test_a_docker_target_without_a_sync_port_is_refused() -> None: ...
def test_a_docker_target_with_an_explicit_jump_host_is_refused() -> None: ...
def test_a_docker_target_with_a_deployer_layout_is_refused() -> None: ...
def test_a_sync_port_without_the_docker_provider_is_refused() -> None: ...
```

In `tests/engine/test_plan.py`:

```python
def test_wp_cli_credentials_are_refused_under_the_docker_provider() -> None: ...
```

Each message must name the offending combination and what to do instead: for
`wp_cli`, that `env` and `explicit` work and that WordPress's
`${WP_TABLE_PREFIX}` has no other source — a reader who only learns "refused"
will try the placeholder next and fail deeper.

- [ ] **Step 2: Run them and watch all five fail**

Run: `uv run pytest tests/config/test_targets.py tests/engine/test_plan.py -vv -k "refused"`
Expected: FAIL — nothing refuses anything yet.

- [ ] **Step 3: Implement the four target refusals in `_build`**

Before the translation, so a refused target is never half-rewritten.

- [ ] **Step 4: Implement the `wp_cli` refusal in `build_plan`**

Beside the existing configuration refusals there, which raise `ConfigError`.

- [ ] **Step 5: Run them**

Run: `uv run pytest tests/config/test_targets.py tests/engine/test_plan.py -vv -k "refused"`
Expected: PASS, five tests.

- [ ] **Step 6: Run the whole gate, then commit**

```bash
git add src/ddev_sync tests
git commit -m "feat(config): refuse the docker combinations that cannot work"
```

---

## Task 3: The doctor tells a readable path from an unreadable one

**Files:**
- Modify: `src/ddev_sync/operations/preflight.py` (`_probe` at `:213`, `PreflightReport`), `src/ddev_sync/cli/render.py` (`render_doctor` at `:163`)
- Test: `tests/operations/test_preflight.py`, `tests/cli/test_render.py`

**Interfaces:**
- Consumes: `ResolvedTarget.provider` from Task 1.
- Produces: `PreflightReport.path_readable: bool | None`, `None` when the
  connection itself failed and the question was never asked.

Today the probe answers one question: is `path` a directory. Under the docker
provider the likeliest misconfiguration is a `user:` whose uid does not match
the volume's owner, which looks identical from outside — the directory is
there and nothing can be read out of it.

The probe is a single `sh -c` round trip, so the third outcome goes **into
that same string** rather than into a second connection. It helps a `server`
target just as much: an unreadable deploy path has always failed later and
deeper.

- [ ] **Step 1: Write the failing probe test**

In `tests/operations/test_preflight.py`, scripting the fake runner to return
the new marker:

```python
def test_a_present_but_unreadable_path_is_reported_as_such() -> None:
    report = PreflightOperation().check(context_with_probe_output(_UNREADABLE))
    assert report.remote_path_ok is True
    assert report.path_readable is False
```

`remote_path_ok` stays true on purpose: the path *is* there, and conflating
the two is what makes the current message misleading.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/operations/test_preflight.py -vv -k unreadable`
Expected: FAIL — no `path_readable`, no `_UNREADABLE`.

- [ ] **Step 3: Extend the probe to three outcomes**

Add `_UNREADABLE` beside `_OK` and `_NO_PATH`, and extend the shell one-liner
so it distinguishes "not a directory", "a directory that cannot be listed" and
"fine". Keep the existing guard that an unexpected marker raises — a fourth
outcome must stay a bug, not a silent pass.

- [ ] **Step 4: Add the field to `PreflightReport` and run the test**

Run: `uv run pytest tests/operations/test_preflight.py -vv -k unreadable`
Expected: PASS.

- [ ] **Step 5: Write the failing render tests**

In `tests/cli/test_render.py`:

```python
def test_the_doctor_reports_an_unreadable_path() -> None: ...
def test_the_doctor_names_the_endpoint_for_a_docker_target() -> None: ...
def test_the_doctor_says_a_docker_path_is_inside_the_endpoint() -> None: ...
```

The second asserts that the ssh row speaks of the sync endpoint rather than
the host: when the endpoint is silent the host usually answered perfectly
well, and naming the host sends a reader to debug the wrong machine.

The third covers the spec's one deliberate break with a reader's intuition:
under `provider: docker`, `path` is the path **inside the endpoint**, not on
the host. The deploy-path row says so, because someone staring at a correct
`/data/` while `/srv/app/` sits on the host will otherwise conclude the tool
is broken.

- [ ] **Step 6: Run them, implement the rows, run again**

Run: `uv run pytest tests/cli/test_render.py -vv -k "unreadable or endpoint"`
Expected: FAIL, then PASS.

The unreadable row is omitted when `path_readable` is `None`, matching the
rule `render_doctor` already documents for every probe that did not run.

- [ ] **Step 7: Check the golden files**

`tests/cli/golden/` holds rendered output. Run the suite, and if a golden file
changed, read the diff before accepting it — a changed doctor table for an
ordinary server target is either an improvement you meant or a regression you
did not.

- [ ] **Step 8: Run the whole gate, then commit**

```bash
git add src/ddev_sync tests
git commit -m "feat(preflight): tell a missing path from an unreadable one"
```

---

## Task 4: Connect through a jump host, for real

**Files:**
- Modify: `tests/integration/docker-compose.yaml`
- Create: `tests/integration/test_jump_host.py`

**Interfaces:**
- Consumes: nothing. It exercises `transport/ssh.py`'s jump flag, which
  predates this plan.
- Produces: nothing.

**Why this task is in this plan at all.** `jump_host` appears in five test
files — `tests/config/test_models.py`, `tests/config/test_targets.py`,
`tests/engine/test_plan.py`, `tests/operations/test_preflight.py`,
`tests/transport/test_ssh.py` — and every one of them asserts the **argv**. It
appears nowhere under `tests/integration/` or `tests/matrix/`. The whole
provider rests on a path that is known to build the right command line and is
not known to connect.

**What it proves, and what it does not.** It proves the hop: that `ssh -J`
reaches a second host and that a pull works through it. It does **not** prove
the docker translation end to end — the stack's inner host is reachable as
`remote:2222` from the jump container, not as `127.0.0.1`, so a literal
`provider: docker` target would not match this topology. Task 1 Step 8 pins
the translation's argv; the matrix cell in plan 2 does the real thing. Say
this in the test's module docstring, so nobody later mistakes it for coverage
it does not give.

- [ ] **Step 1: Add the `jump` service**

A second `linuxserver/openssh-server` in front of `remote`, with the same
`PUBLIC_KEY_FILE` and a published port of its own. It needs no `DOCKER_MODS`:
it forwards a connection and runs nothing. Give it a healthcheck in the shape
the file already uses — the comment there explains that neither image ships
one and that `--wait` is otherwise satisfied instantly.

- [ ] **Step 2: Bring the stack up and confirm the hop by hand**

```bash
docker compose -f tests/integration/docker-compose.yaml up --wait
ssh -o StrictHostKeyChecking=accept-new -i tests/integration/.keys/id_ed25519 \
    -J deploy@127.0.0.1:<jump port> -p 2222 deploy@remote true; echo "exit=$?"
```

Expected: `exit=0`. Do this before writing the test: if the topology is wrong,
you want to find out here rather than inside a fixture.

- [ ] **Step 3: Write the integration test**

`tests/integration/test_jump_host.py`, marked `@pytest.mark.integration` and
using the suite's existing `stack`, `ssh_key` and `known_host` fixtures. One
test: build a `ResolvedTarget` with `jump_host` pointing at the published jump
port and `host`/`port` at the inner service, then pull a fixture file with
`rsync_pull_command` and assert its contents arrived.

rsync rather than a bare `ssh`: `_rsync_ssh_option` builds its own `-e` string
and splits it on spaces, which is the one place the jump flag could be
mangled without any unit test noticing.

- [ ] **Step 4: Run it**

Run: `uv run pytest -m integration --no-cov -vv -k jump_host`
Expected: PASS.

- [ ] **Step 5: Prove it is load-bearing**

Remove `-J` from the command the test builds — pass a target with no jump host
while still addressing the inner service — and confirm the test fails with a
connection error rather than passing by some other route. Restore it. Report
what the failure said.

- [ ] **Step 6: Run the integration suite whole**

Run: `uv run pytest -m integration --no-cov -vv`
Expected: every test passes. The new service must not disturb the existing
ones; if `docker compose up --wait` now takes materially longer, say so in the
report — the file's own comments show that this has been a problem before.

- [ ] **Step 7: Commit**

```bash
git add tests/integration
git commit -m "test(integration): connect through a jump host for the first time"
```

---

## Verification

The gate for this plan is `uv run pytest`, `uv run ruff check .`,
`uv run ruff format --check .` and `uv run mypy`, plus
`uv run pytest -m integration --no-cov`, which CI runs as its own job and
which is where Task 4 lives.

## Out of scope

The endpoint image, the compose template, host-key persistence, the matrix
cell and the release pipeline — plan 2. WordPress under `provider: docker`,
which the spec refuses here and leaves to its own design. Swarm, Kubernetes, a
host without SSH, and a second hop in front of the Docker host.
