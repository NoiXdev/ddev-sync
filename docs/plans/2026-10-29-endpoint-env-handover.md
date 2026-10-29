# Endpoint env handover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `provider: docker` sync reads its database credentials from variables the stack hands the endpoint at runtime, with no `.env` on disk.

**Architecture:** `endpoint/entrypoint.sh` writes the variables named in `DDEV_SYNC_EXPORT_ENV` into `/run/ddev-sync/app.env` (marker line plus verbatim `NAME=value` lines, mode 0400, owned by the session uid). `operations/database.py` probes that absolute path as the last candidate under `provider: docker` and reads it with its own verbatim parser. `init`, the matrix cell `mxdocker` and the user docs follow.

**Tech Stack:** POSIX sh (busybox, Alpine) in the endpoint; Python engine with pytest (100 % coverage gate, `uv run pytest`); integration and matrix suites against real containers; noix-docs (Astro/Starlight) in `~/_dev/noix-docs`.

**Spec:** `docs/specs/2026-10-29-endpoint-env-handover-design.md`

## Global Constraints

- Handover file path: `/run/ddev-sync/app.env`. Marker, exactly the first line: `# ddev-sync endpoint handover v1`.
- Endpoint variable: `DDEV_SYNC_EXPORT_ENV`, names separated by whitespace or commas. Unset or empty: no file is written, and nothing else changes.
- Name rule: `[A-Za-z_][A-Za-z0-9_]*`, and the name must not start with `DDEV_SYNC_`.
- Unset listed variable: refuse to start. Set but empty: written as empty. A value containing `\n` or `\r`: refuse to start.
- The file is rewritten at every start, owner `${uid}:${gid}`, mode `0400`. The entrypoint logs the count, never a value.
- Values arrive byte for byte: no trimming, no unquoting, no comment handling.
- Under `provider: docker` the handover path is the **last** candidate, after the configured or default names joined onto the data root. Never under `provider: server`.
- The handover parser is used whenever the path read equals `/run/ddev-sync/app.env`. A missing or different marker is a hard error, never a fallback to `_parse_env`.
- `_parse_env` and the parsing of existing `.env` files stay unchanged.
- New docs slugs: `endpoint-export-env-name-invalid`, `endpoint-export-env-variable-unset`, `endpoint-export-env-value-has-a-newline`, `endpoint-handover-file-unreadable`. Guide anchor: `credentials-from-the-stacks-environment`.
- Code, comments, docs and commits in English. Conventional commits with the footer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit locally only; never push.
- Public texts (docs pages) name no implementation language or library.

## Review Focus

- A password with `'"#=$`, a leading space and a trailing space, as the *last* line of the file, must reach `RemoteCredentials` unchanged. The trailing newline of the last line must not be confused with the value. Tests in Task 1 and Task 2.
- `DDEV_SYNC_EXPORT_ENV` with a name like `A;rm -rf /` or `$(id)` must be refused before any expansion. The entrypoint expands names with `eval`, so validation comes first. Test in Task 1.
- The same name listed twice, or a comma with no space (`DB_HOST,DB_PORT`), must produce one line per name with no error. Test in Task 1.
- A target with `provider: docker` **and** a mounted `.env` must still read the `.env`, and the handover file is never read. Test in Task 2.
- A file at the handover path with Windows line endings or no marker gives the `endpoint-handover-file-unreadable` error, not "variable not set". Test in Task 2.

---

### Task 1: Endpoint writes the handover file

**Files:**
- Modify: `endpoint/entrypoint.sh` (new block after the authorized-keys checks, before the host key)
- Modify: `endpoint/docker-compose.ddev-sync-endpoint.yaml` (commented example)
- Modify: `src/ddev_sync/docs.py` (three slugs under `# docker-endpoint`, after `no-authorized-keys`)
- Test: `tests/integration/test_endpoint_image.py`

**Interfaces:**
- Produces: the file contract from Global Constraints, which Task 2 reads. The `More:` links in the entrypoint use the three entrypoint slugs. `tests/test_docs.py::test_every_entry_is_used` finds them in the shell file.

- [ ] **Step 1: Write the failing integration tests** in `tests/integration/test_endpoint_image.py`. Use the existing `endpoint`, `start_in_foreground`, `keypair` and `ssh` helpers, and pass `DDEV_SYNC_AUTHORIZED_KEYS` so the endpoint starts.

```python
TRICKY = " pa'ss\"#=$ word "

def test_without_an_export_list_no_handover_file_is_written(endpoint_image, tmp_path):
    # with endpoint(...) as container:
    #   docker exec container test -e /run/ddev-sync/app.env  -> returncode != 0

def test_the_listed_variables_reach_the_session_verbatim(endpoint_image, tmp_path):
    # environment: DDEV_SYNC_EXPORT_ENV="DB_HOST,DB_PASSWORD DB_HOST", DB_HOST="db",
    #              DB_PASSWORD=TRICKY, UNLISTED="nope"
    # ssh(key, "cat /run/ddev-sync/app.env").stdout ==
    #   "# ddev-sync endpoint handover v1\nDB_HOST=db\nDB_PASSWORD=" + TRICKY + "\n"
    # (the duplicate is written once; UNLISTED and DDEV_SYNC_AUTHORIZED_KEYS are absent)

def test_the_handover_file_belongs_to_the_session_and_nobody_else(endpoint_image, tmp_path):
    # DDEV_SYNC_UID=1234, DDEV_SYNC_GID=1234, one exported variable
    # docker exec container stat -c '%u:%g %a' /run/ddev-sync/app.env -> "1234:1234 400"

def test_an_empty_value_is_handed_over_as_empty(endpoint_image, tmp_path):
    # DB_PASSWORD="" listed -> file contains the line "DB_PASSWORD=\n"

@pytest.mark.parametrize(("environment", "named", "slug"), [
    ({"DDEV_SYNC_EXPORT_ENV": "DB_HOST"}, "DB_HOST", "endpoint-export-env-variable-unset"),
    ({"DDEV_SYNC_EXPORT_ENV": "1BAD"}, "1BAD", "endpoint-export-env-name-invalid"),
    ({"DDEV_SYNC_EXPORT_ENV": "$(id)"}, "$(id)", "endpoint-export-env-name-invalid"),
    ({"DDEV_SYNC_EXPORT_ENV": "DDEV_SYNC_UID"}, "DDEV_SYNC_UID", "endpoint-export-env-name-invalid"),
    ({"DDEV_SYNC_EXPORT_ENV": "DB_PASSWORD", "DB_PASSWORD": "a\nb"}, "DB_PASSWORD",
     "endpoint-export-env-value-has-a-newline"),
    ({"DDEV_SYNC_EXPORT_ENV": "DB_PASSWORD", "DB_PASSWORD": "a\rb"}, "DB_PASSWORD",
     "endpoint-export-env-value-has-a-newline"),
])
def test_a_bad_export_list_stops_the_endpoint(endpoint_image, tmp_path, environment, named, slug):
    # start_in_foreground(..., environment={keys, **environment})
    # returncode != 0; named in stderr; f"#{slug}" in stderr; the value "a" "b" never in stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -m integration tests/integration/test_endpoint_image.py -k "handover or export or empty_value" -p no:cacheprovider --no-cov`
Expected: FAIL. The file does not exist, and the bad lists start the endpoint.

- [ ] **Step 3: Implement the block in `endpoint/entrypoint.sh`**

Split `DDEV_SYNC_EXPORT_ENV` on commas and whitespace, then drop duplicates, keeping the first position. Validate **every** name against the name rule and the `DDEV_SYNC_` prefix **before** any `eval`. Check that each name is set (`eval "is_set=\${$name+x}"`). Then read the value with `eval`, reject `\n`/`\r` with `case`, and write it with `printf '%s=%s\n'`. Write to a temp file under `umask 077` in `/run/ddev-sync`, then `chown "${uid}:${gid}"`, `chmod 400` and `mv` it into place. Remove any old `app.env` first, so an endpoint restarted without the list leaves no stale file. Each refusal goes through `refuse` with its slug URL. The newline message names the variable, never the value. Log `say "handing over N variables to the sync session"`.

- [ ] **Step 4: Add the slugs to `src/ddev_sync/docs.py`** in the order `endpoint-export-env-name-invalid`, `endpoint-export-env-variable-unset`, `endpoint-export-env-value-has-a-newline`. Also add the commented example in the template. Show `DDEV_SYNC_EXPORT_ENV` with values passed via `environment:` from stack variables and via `env_file:`, beside the `.env` mount, which now reads "one of two ways". Keep `tests/test_endpoint_template.py` green: the example stays commented.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -m integration tests/integration/test_endpoint_image.py -p no:cacheprovider --no-cov && uv run pytest`
Expected: all PASS, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add endpoint/ src/ddev_sync/docs.py tests/integration/test_endpoint_image.py
git commit -m "feat(endpoint): hand listed environment variables to the sync session"
```

### Task 2: Engine reads the handover file

**Files:**
- Modify: `src/ddev_sync/operations/database.py` (`remote_env_candidates` neighbourhood, `_fetch_first_env_file`, `_from_remote_env`)
- Modify: `src/ddev_sync/docs.py` (`endpoint-handover-file-unreadable` after the Task 1 slugs)
- Test: `tests/operations/test_db_dump.py`

**Interfaces:**
- Consumes: the file contract from Task 1.
- Produces, in `operations/database.py`:
  - `HANDOVER_PATH: str = "/run/ddev-sync/app.env"`
  - `HANDOVER_MARKER: str = "# ddev-sync endpoint handover v1"`
  - `remote_env_paths(context: ExecutionContext) -> list[str]`: the data-root-joined candidates, plus `HANDOVER_PATH` last when `context.target.provider == Provider.DOCKER`. Task 3 uses it.
  - `parse_handover(contents: str, path: str) -> dict[str, str]`

- [ ] **Step 1: Write the failing tests** in `tests/operations/test_db_dump.py`. Extend `make_context` with `provider: Provider = Provider.SERVER`, and use the Laravel `env_map` (`DB_*`) in a local config.

```python
HANDOVER = "# ddev-sync endpoint handover v1\nDB_DATABASE=acme\nDB_USERNAME=acme\nDB_HOST=db\nDB_PORT=3306\nDB_PASSWORD= pa'ss\"#=$ word \n"

def test_under_docker_the_handover_file_is_the_last_candidate(tmp_path):
    # probe script: index("/srv/app/.env ]") < index("/run/ddev-sync/app.env")

def test_under_server_the_handover_file_is_never_probed(tmp_path):
    # "/run/ddev-sync/app.env" not in probe

def test_the_handover_file_is_read_verbatim(tmp_path):
    # runner stdout = "/run/ddev-sync/app.env\n" + HANDOVER
    # credentials.password == " pa'ss\"#=$ word "; credentials.host == "db"

def test_an_empty_handover_value_is_reported_as_not_set(tmp_path):
    # DB_PASSWORD= -> OperationError, doc_link endswith "#variable-missing-in-the-remote-env-file"

@pytest.mark.parametrize("body", ["DB_HOST=db\n", "# ddev-sync endpoint handover v2\nDB_HOST=db\n",
                                  "# ddev-sync endpoint handover v1\r\nDB_HOST=db\r\n",
                                  "# ddev-sync endpoint handover v1\nnot an assignment\n", ""])
def test_a_handover_file_that_is_not_one_is_refused(tmp_path, body):
    # OperationError naming the path; doc_link endswith "#endpoint-handover-file-unreadable"

def test_a_configured_env_file_naming_the_handover_path_uses_the_handover_parser(tmp_path):
    # database.env_file: ["/run/ddev-sync/app.env"], stdout echoes that path + HANDOVER
    # password keeps its spaces

def test_a_mounted_env_file_still_wins_under_docker(tmp_path):
    # stdout = "/srv/app/.env\n" + plain .env -> parsed by _parse_env as today

def test_the_missing_file_message_under_docker_names_the_export_list(tmp_path):
    # probe fails -> message contains "/run/ddev-sync/app.env" and "DDEV_SYNC_EXPORT_ENV",
    # doc stays "no-environment-file-on-the-remote"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/operations/test_db_dump.py -p no:cacheprovider --no-cov`
Expected: FAIL. `Provider` is not threaded through, the handover path is not probed, and `parse_handover` is undefined.

- [ ] **Step 3: Implement**

- `remote_env_paths` replaces the inline `context.remote_data(...)` list in `_fetch_first_env_file`. A candidate that is already absolute (it starts with `/`) is taken as is, not joined. That covers a configured `database.env_file` naming the handover path.
- `_from_remote_env` calls `parse_handover` when `path == HANDOVER_PATH`, otherwise `_parse_env` as today.
- `parse_handover` splits on `"\n"` only. It requires the first line to equal the marker. It drops exactly one trailing empty element, so the file must end in `"\n"`, and empty input fails the marker check. Every other line must contain `=` and must not contain `"\r"`. The value is everything after the first `=`.
- Under docker, the "no environment file found" message adds one sentence: set `DDEV_SYNC_EXPORT_ENV` on the endpoint to hand the stack's variables over. The existing slug is kept. That troubleshooting entry links the guide section (Task 5), which keeps `docs.py` a troubleshooting-only registry. This is a ruling on the spec's "points to the new docs section".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest`
Expected: all PASS, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/ddev_sync/operations/database.py src/ddev_sync/docs.py tests/operations/test_db_dump.py
git commit -m "feat(database): read credentials an endpoint hands over from its environment"
```

### Task 3: `init` names the handover

**Files:**
- Modify: `src/ddev_sync/cli/init.py:705-736` (`_offer_remote_env_file`)
- Modify: `src/ddev_sync/cli/prompts.py:164` (`choose_remote_env_file`)
- Test: `tests/cli/test_init.py`, `tests/cli/test_prompts.py`

**Interfaces:**
- Consumes: `remote_env_paths`, `HANDOVER_PATH` (Task 2).
- Produces: `choose_remote_env_file(looked_for: list[str], present: list[str], hint: str | None = None) -> str | None`. The hint, when given, is appended to the question text. The choices are unchanged.

- [ ] **Step 1: Write the failing tests**
  - `test_prompts.py`: with `hint="X"` the question text ends with `X`. Without it, the text is unchanged.
  - `test_init.py`: for a `provider: docker` target with a red credentials row, the prompt receives `looked_for` containing `/run/ddev-sync/app.env`, and a hint containing `DDEV_SYNC_EXPORT_ENV` and `#credentials-from-the-stacks-environment`. The choices do **not** contain the handover path. For a server target, no hint is given and there is no handover path.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_init.py tests/cli/test_prompts.py -p no:cacheprovider --no-cov`
Expected: FAIL

- [ ] **Step 3: Implement.** `looked_for` comes from `remote_env_paths(execution)`. The hint is set only under docker and links `https://docs.noix.dev/ddev-sync/guide/docker-endpoint/#credentials-from-the-stacks-environment`.

- [ ] **Step 4: Run** `uv run pytest`. Expected: all PASS, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/ddev_sync/cli/init.py src/ddev_sync/cli/prompts.py tests/cli/
git commit -m "feat(init): point a docker target without an env file at the handover"
```

### Task 4: `mxdocker` syncs from the environment

**Files:**
- Delete: `tests/matrix/cells/mxdocker/remote-only/.env`
- Modify: `tests/matrix/docker-compose.yaml` (`endpoint` service `environment`)
- Modify: `tests/matrix/cells.py` (the `mxdocker` comment), `tests/matrix/cells/mxdocker/ddev-sync.yaml` (the comment)

**Interfaces:**
- Consumes: Tasks 1 and 2 end to end.

Ruling: switch the existing cell rather than add one. The mounted `.env` route stays covered by `tests/integration/test_docker_provider.py::test_a_dump_through_the_endpoint_reaches_the_database_by_service_name`, and a new Laravel cell costs a full framework install.

- [ ] **Step 1: Change the cell.** Add these to the matrix `endpoint` environment: `DDEV_SYNC_EXPORT_ENV=DB_HOST DB_PORT DB_DATABASE DB_USERNAME DB_PASSWORD`, `DB_HOST=remote-db`, `DB_PORT=3306`, `DB_DATABASE=acme_prod`, `DB_USERNAME=acme`, `DB_PASSWORD=s3cret` (the values of the deleted `.env`). Move the deleted file's explanation into the compose comment and the cell comments: the endpoint mounts no `.env` and gets the credentials from the stack's environment.

- [ ] **Step 2: Run the cell**

Run: `uv run pytest -m matrix tests/matrix -k mxdocker -p no:cacheprovider --no-cov`
Expected: PASS. If `test_matrix.py` asserts anything about `remote-only/` for this cell, update that assertion to the new fact; do not delete it.

- [ ] **Step 3: Run the integration suite for the endpoint and the provider**

Run: `uv run pytest -m integration tests/integration/test_docker_provider.py tests/integration/test_endpoint_image.py -p no:cacheprovider --no-cov`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add -A tests/matrix
git commit -m "test(matrix): sync mxdocker from credentials the stack hands the endpoint"
```

### Task 5: User docs

**Files (in `~/_dev/noix-docs`, branch `docs/endpoint-env-handover` off `develop`):**
- Modify: `src/content/docs/ddev-sync/guide/docker-endpoint.md`
- Modify: `src/content/docs/ddev-sync/reference/troubleshooting.md`
- Modify: `src/data/ddev-sync-doc-slugs.json`

**Interfaces:**
- Consumes: the four slugs from Tasks 1 and 2, and the guide anchor from Task 3.

- [ ] **Step 1: Add the slugs** to `src/data/ddev-sync-doc-slugs.json`, mirroring the order in `docs.py`.

- [ ] **Step 2: Run the anchor check to verify it fails**

Run: `npm run check`
Expected: FAIL, naming the four missing anchors.

- [ ] **Step 3: Write the pages**
  - Guide: a section `## Credentials from the stack's environment` (slug `credentials-from-the-stacks-environment`) with:
    - why a mounted `.env` is not always there;
    - a Portainer example, with stack variables passed to both services;
    - a compose example with `env_file:`;
    - the variable names per framework (Laravel `DB_*`, Symfony and Shopware `DATABASE_*`, TYPO3 `TYPO3_DB_*`);
    - "a mounted `.env` wins";
    - what the file holds and who can read it.

    The existing compose variants mention the option in one line each, where they show the `.env` mount.
  - Troubleshooting: one entry per new slug, under the docker-endpoint area, in the same order. Each says what the log line means and how to fix it. Update `no-environment-file-on-the-remote` to mention the handover under `provider: docker` and link the guide section. Update `variable-missing-in-the-remote-env-file` to mention that a listed but empty variable lands here.

  - Ruling: `reference/configuration` documents project keys, not endpoint variables, which live in the guide. So it gets no entry, even though the spec names it.
  - The anchor script checks only the troubleshooting page. The guide heading must slug to exactly `credentials-from-the-stacks-environment`, because the engine links it by absolute URL (Task 3). Check it in `dist/ddev-sync/guide/docker-endpoint/index.html`.

- [ ] **Step 4: Run the checks**

Run: `npm run check && npm run build && grep -c 'id="credentials-from-the-stacks-environment"' dist/ddev-sync/guide/docker-endpoint/index.html`
Expected: "all 52 ddev-sync anchors present", 0 errors, "All internal links are valid", and the grep prints `1`.

- [ ] **Step 5: Commit**

```bash
git add src/content/docs/ddev-sync src/data/ddev-sync-doc-slugs.json
git commit -m "docs(ddev-sync): hand credentials to the docker endpoint from the stack's environment"
```
