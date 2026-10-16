# Docker sync provider — endpoint implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the other half of the docker provider — the endpoint container a
stack publishes, the compose template that places it, the proof that the engine
syncs through the real image, and the release path for a second artifact.

**Architecture:** The endpoint is an sshd with `rsync` and a MariaDB client and
nothing else. It is reached through the Docker host by ProxyJump, which plan 1
already resolves a `provider: docker` target into. Nothing in `src/ddev_sync`
has to change: this plan builds the far side of a seam that already exists, and
proves it twice — once against the engine on the host (integration) and once
against the engine in a container with a real framework (matrix).

**Tech Stack:** Alpine 3.21, OpenSSH 9.9, Docker Compose, GitHub Actions,
pytest.

**Spec:** [docs/specs/2026-10-16-docker-sync-provider-design.md](../specs/2026-10-16-docker-sync-provider-design.md)

**Predecessor:** [docs/plans/2026-10-16-docker-sync-provider-engine.md](2026-10-16-docker-sync-provider-engine.md)
— the engine side, complete and on `main` (`0558ce8..c97addb`).

---

## What was measured before this plan, and what it changed

Each line was run, not recalled. Four of them change what the spec says.

1. **`alpine:3.21` carries every binary the endpoint needs**, at
   `openssh-server=9.9_p2-r0`, `openssh-keygen=9.9_p2-r0`, `rsync=3.5.0-r0`,
   `mariadb-client=11.4.12-r0`. The digest resolved on the day is
   `sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507`.
2. **`mariadb-client` provides `mysqldump` and `mysql`** under those names on
   3.21, as well as `mariadb-dump`/`mariadb`. The engine runs the first two
   over ssh, so the compatibility names are what matter, and they are pinned
   by a test rather than assumed — Alpine is free to drop them.
3. **Alpine already ships an account called `sync`** (`sync:x:5:0`), and plan 1
   fixed the endpoint's user to `sync` (`config/models.py::ENDPOINT_USER`).
   `adduser … sync` therefore fails with "user 'sync' in use"; the image has to
   `deluser sync` first. uid/gid 33 are free on 3.21.
4. **sshd runs happily as a non-root uid** — it binds a high port, accepts a
   key login and gives the session that uid. But a container started with a
   `user:` the image was *not* built for dies at once: `user: "1000:1000"`
   against an image whose account is uid 33 prints `No user exists for uid
   1000` and exits 255. An sshd cannot serve a uid that has no passwd entry,
   and only root inside the container can write one.
   **So the spec's `user: "33:33"` cannot be the mechanism** (decision: see
   "Deviations" below).
5. **A named volume inherits the image's ownership** on first mount, so
   persisting host keys needs no chown — and with the entrypoint running as
   root, nothing about the host key depends on the session uid at all.
6. **`network_mode: "service:<jump>"` models a published port exactly.** The
   endpoint then has no address of its own, the jump reaches it on
   `127.0.0.1:22`, and the endpoint still resolves other compose services by
   name — which is how the spec's "no database credentials of its own, the
   service name resolves" claim is testable at all.
7. **A listener check needs no extra binary:** state `0A` on port `0016` in
   `/proc/net/tcp`. The endpoint gets a healthcheck without a fourth package.
8. **Nothing in `src/` calls `scp_download_command`** (only
   `tests/transport/test_ssh.py` and one comment). Modern `scp` speaks SFTP,
   so the endpoint would need `openssh-sftp-server` for it; it is left out,
   and the consequence is written down rather than discovered.

## Deviations from the spec, for review

- **The endpoint container runs as root and takes the session uid as
  configuration** (`DDEV_SYNC_UID`/`DDEV_SYNC_GID`, defaulting to `33`),
  instead of the spec's `user: "33:33"`. Measurement 4 is why: `user:` can only
  ever name the one uid the image was built for, and any other value kills the
  container with a message that says nothing about mounts. What the spec wanted
  from `user:` — a session that can read mounts owned by the application's uid
  — is exactly what this delivers, for any uid. The container has no socket, no
  added capabilities, read-only payload mounts and a loopback-only port, so
  what is given up is container-local root in a container with nothing to
  take over.
- **The endpoint offers an ed25519 host key only.** The spec says
  `/etc/ssh/ssh_host_*`; one modern algorithm is enough for the engine's own
  client and makes "the key survived" a single-file question.
- **No `README.md` change.** Operator instructions for placing the endpoint in
  a stack are user documentation and belong in `noix-docs`
  (`src/content/docs/<produkt>/`), which is outside this repository and
  outside this plan. The shipped config reference
  (`ddev-sync/ddev-sync.example.yaml`) does document the two keys.

## Global Constraints

- 100 % line **and** branch coverage on `src/ddev_sync`; `# pragma: no cover`
  is forbidden. This plan is not expected to change `src/` at all — if a task
  finds that it must, its coverage comes with it.
- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict`
  (over `src/ddev_sync`, `tests`, `tools`) stay clean. Line length 100.
- Unit suite: `uv run pytest`. Integration: `uv run pytest -m integration
  --no-cov`. Matrix: `uv run pytest -m matrix -k <cell> --no-cov -v`.
- Conventional Commits, footer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Everything code-side in English.
- apk packages pinned with `=`; base images pinned by digest.
- The endpoint carries `sshd`, `rsync` and a MariaDB client — **and not a
  fourth thing** (spec decision 4). No `docker-cli`, no `rclone`, no PHP.
- Spec decision 10: the release pipeline (Task 5) comes **after** a green
  matrix cell (Task 4). Do not reorder.

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has
a test in the task that owns it.

1. **An authorized-keys file that exists but holds no key** (blank lines,
   comments) with no environment variable either — must refuse to start, not
   start an endpoint nobody can enter and nothing can detect. *Task 1.*
2. **`DDEV_SYNC_UID=0` or a non-numeric value** — must refuse, rather than
   handing a session root or creating an account named after a typo. *Task 1.*
3. **A path that exists inside the endpoint but is unreadable by the session
   uid** — plan 1 built the doctor row for this; it has never met a real
   endpoint. *Task 3.*
4. **A redeployed stack whose host-key volume was dropped** — the key changes,
   the engine refuses (`accept-new`), and the message a developer sees must
   point at the volume rather than read like an attack. *Task 3.*
5. **Two stacks on one Docker host, both published on the same loopback
   port** — plan 1's `HostKeyAlias` is what keeps the second from being
   refused; nothing has tested it against two real endpoints. *Task 3.*

## File Structure

| File | Responsibility |
|---|---|
| `endpoint/Dockerfile` (create) | The endpoint image: three binaries, the `sync` account, the entrypoint |
| `endpoint/sshd_config` (create) | Keys only, one user, no forwarding, host key in the volume |
| `endpoint/entrypoint.sh` (create) | Compose `authorized_keys`, apply the uid, generate the host key once, exec sshd |
| `endpoint/docker-compose.ddev-sync-endpoint.yaml` (create) | The template an operator copies into their stack |
| `tests/integration/test_endpoint_image.py` (create) | What the image promises, as container runs |
| `tests/integration/test_docker_provider.py` (create) | The engine against the real endpoint, through a jump |
| `tests/integration/docker-compose.yaml` (modify) | An `endpoint` service in the jump's network namespace |
| `tests/integration/conftest.py` (modify) | Shared known-hosts fixture for both hops and the alias |
| `tests/jump-init/` (move from `tests/integration/jump-init/`) | One copy of the forwarding hook, two suites |
| `tests/matrix/docker-compose.yaml` (modify) | `jump` and `endpoint` behind profiles |
| `tests/matrix/cells.py` (modify) | The `mxdocker` cell; `verified_row` |
| `tests/matrix/cells/mxdocker/*` (create) | Its recipe, config, seed and remote tree |
| `tools/apply_verified_rows.py` (modify) | A cell that verifies a provider emits no profile row |
| `tools/set_version.py` (modify) | A sixth target: the template's image tag |
| `.github/workflows/ci.yaml` (modify) | Build and smoke the endpoint image on every push |
| `.github/workflows/release.yaml` (modify) | Pass the second image; smoke it after release |
| `../github-workflows/.github/workflows/create_docker_changelog_version_release.yaml` (modify) | An optional second image, same version, same tag rules |

---

## Task 1: The endpoint image

**Files:**
- Create: `endpoint/Dockerfile`, `endpoint/sshd_config`, `endpoint/entrypoint.sh`
- Test: `tests/integration/test_endpoint_image.py`
- Modify: `tests/integration/conftest.py`, `.github/workflows/ci.yaml`

**Interfaces:**
- Consumes: `ENDPOINT_USER = "sync"` from `src/ddev_sync/config/models.py` — the
  account name is fixed by the engine, not chosen here.
- Produces, for Tasks 2–5:
  - build context `endpoint/`, dockerfile `endpoint/Dockerfile`
  - account `sync`, sshd on port **22**
  - `DDEV_SYNC_AUTHORIZED_KEYS` (newline-separated keys) and the optional
    read-only mount `/etc/ddev-sync/authorized_keys`
  - `DDEV_SYNC_UID` / `DDEV_SYNC_GID`, both defaulting to `33`
  - host key at `/etc/ssh/keys/ssh_host_ed25519_key`, the directory being the
    volume mount point
  - a session-scoped fixture `endpoint_image() -> str` in
    **`tests/integration/conftest.py`** returning the tag
    `ddev-sync-endpoint:test`, built once per session. In the conftest rather
    than in the test module because Task 3 starts throwaway endpoints from it
    too, and a fixture in a sibling module is not reachable.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_endpoint_image.py`, `pytestmark =
pytest.mark.integration`. The `endpoint_image` fixture in `conftest.py` builds
the image (`docker build -t ddev-sync-endpoint:test endpoint/`) once per
session and fails the test if the build fails. A helper in the test module
starts a container with a given environment and mounts and removes it
afterwards; another runs one ssh command against it on a published loopback
port.

These tests must not depend on the suite's own stack: the endpoint is published
on `127.0.0.1` with a port of this module's own (`22024`), and each test's
container is removed in a `finally`.

```python
def test_it_refuses_to_start_with_no_authorized_key() -> None:
    # exit status is not 0 and the message names BOTH sources
    assert "DDEV_SYNC_AUTHORIZED_KEYS" in logs and "/etc/ddev-sync/authorized_keys" in logs

def test_a_file_holding_only_comments_is_not_a_key() -> None:
    # mounted file is "# nothing here\n\n", no environment variable: same refusal

def test_a_key_from_the_environment_and_a_key_from_the_file_both_get_in() -> None:
    # two separate keypairs, one per source; each logs in as `sync`

def test_the_session_runs_as_the_configured_uid() -> None:
    # DDEV_SYNC_UID=82 DDEV_SYNC_GID=82 -> `id -u`/`id -g` print 82

def test_a_uid_of_zero_is_refused() -> None:
    # DDEV_SYNC_UID=0 -> non-zero exit, the message names DDEV_SYNC_UID

def test_a_non_numeric_uid_is_refused() -> None:
    # DDEV_SYNC_UID=www-data -> non-zero exit, the message names DDEV_SYNC_UID

def test_the_host_key_survives_a_recreated_container() -> None:
    # same named volume: the public key read before equals the one read after

def test_it_carries_what_the_engine_invokes_on_the_far_side() -> None:
    # one `command -v` per name (busybox's builtin reads only its first
    # argument): sh, rsync, mysqldump, mysql are present
    # php, docker, rclone, ssh are absent

def test_password_authentication_is_refused() -> None:
    # ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password fails

def test_the_endpoint_does_not_forward_tcp() -> None:
    # it is a destination, not a jump: `ssh -W 127.0.0.1:22` through it is
    # refused ("administratively prohibited")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -m integration --no-cov tests/integration/test_endpoint_image.py`
Expected: FAIL — the build fails, `endpoint/Dockerfile` does not exist.

- [ ] **Step 3: Write `endpoint/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
FROM alpine:3.21@sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507

RUN apk add --no-cache \
      openssh-server=9.9_p2-r0 \
      openssh-keygen=9.9_p2-r0 \
      rsync=3.5.0-r0 \
      mariadb-client=11.4.12-r0

# Alpine ships its own `sync` account (uid 5, the classic `/bin/sync` shell);
# the engine connects as `sync` (config/models.py::ENDPOINT_USER), so it is
# replaced rather than worked around. The uid baked here is only a default -
# the entrypoint rewrites it from DDEV_SYNC_UID.
RUN deluser sync \
 && addgroup -g 33 sync \
 && adduser -D -H -u 33 -G sync -h /home/sync -s /sbin/nologin sync \
 && mkdir -p /etc/ssh/keys /etc/ddev-sync /run/ddev-sync \
 && chmod 700 /etc/ssh/keys /run/ddev-sync

COPY sshd_config /etc/ssh/sshd_config
COPY --chmod=0755 entrypoint.sh /usr/local/bin/ddev-sync-endpoint

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/ddev-sync-endpoint"]
```

Comments in the project's voice: why each package is here (one line each,
naming the engine code that invokes it over ssh), and why there is no
`openssh-sftp-server` (measurement 8).

- [ ] **Step 4: Write `endpoint/sshd_config`**

Exact directives: `Port 22`, `HostKey /etc/ssh/keys/ssh_host_ed25519_key`,
`AuthorizedKeysFile /run/ddev-sync/authorized_keys`, `AllowUsers sync`,
`PermitRootLogin no`, `PasswordAuthentication no`,
`KbdInteractiveAuthentication no`, `PermitEmptyPasswords no`,
`AllowTcpForwarding no`, `AllowAgentForwarding no`, `X11Forwarding no`,
`PermitTunnel no`, `PrintMotd no`, `LogLevel INFO`.

`UsePAM` is **not** in the file: Alpine's OpenSSH is built without it and
refuses the option ("Unsupported option UsePAM"), measured.

- [ ] **Step 5: Write `endpoint/entrypoint.sh`**

`#!/bin/sh` with `set -eu`, running as root, in this order. Every refusal
writes to stderr and exits `1`.

1. Read `DDEV_SYNC_UID` and `DDEV_SYNC_GID` (default `33` each). Refuse a value
   that is not a positive integer, and refuse `0`, naming the variable and the
   value: *"DDEV_SYNC_UID=0: the endpoint will not run its sessions as root; set
   it to the uid that owns the mounted application files."*
2. When the requested uid or gid differs from the account's, recreate the `sync`
   account with it (`deluser` / `delgroup` / `addgroup -g` / `adduser -D -H -u
   … -G sync`). Nothing is chowned: sshd reads the host key and the
   authorized-keys file as root, so only the passwd entry depends on the uid.
3. Compose `/run/ddev-sync/authorized_keys`, mode `0600`, owner root: the
   contents of `DDEV_SYNC_AUTHORIZED_KEYS` followed by
   `/etc/ddev-sync/authorized_keys` when that file exists. A **union**, not a
   choice (spec decision 6).
4. Refuse when the result holds no line that is neither blank nor a comment:
   *"no authorized keys: set DDEV_SYNC_AUTHORIZED_KEYS or mount a key file at
   /etc/ddev-sync/authorized_keys. The endpoint will not start without one."*
5. Generate `/etc/ssh/keys/ssh_host_ed25519_key` with `ssh-keygen -t ed25519 -N
   ''` when it is absent, and say which of the two happened on stdout
   ("generated a host key" / "reusing the host key from the volume") — a
   developer debugging a refused key needs that line.
6. `exec /usr/sbin/sshd -D -e`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -m integration --no-cov tests/integration/test_endpoint_image.py -v`
Expected: PASS, all ten.

- [ ] **Step 7: Gate the image in CI**

Add a job `endpoint-image` to `.github/workflows/ci.yaml`, modelled on the
existing `image` job: build `endpoint/` for `linux/amd64`, load it, then three
steps — it refuses to start with no keys; it carries `sh`, `rsync`,
`mysqldump`, `mysql` (one `command -v` each) and none of `php`, `docker`,
`rclone`; `sshd -t` accepts the config.

- [ ] **Step 8: Run the gates and commit**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy --strict src/ddev_sync tests tools && uv run pytest`
Expected: clean, 100 % coverage, unit suite green.

```bash
git add endpoint tests/integration/test_endpoint_image.py .github/workflows/ci.yaml
git commit -m "feat(endpoint): an sshd that carries only what the engine invokes"
```

---

## Task 2: The compose template, the version, the config reference

**Files:**
- Create: `endpoint/docker-compose.ddev-sync-endpoint.yaml`
- Create: `tests/test_endpoint_template.py`
- Modify: `tools/set_version.py`, `tests/test_set_version.py`,
  `tests/test_version_coherence.py`, `ddev-sync/ddev-sync.example.yaml`

**Interfaces:**
- Consumes: Task 1's environment variables, mount points and port.
- Produces: the template path above, carrying
  `image: noixdev/ddev-sync-endpoint:<semver>` as the single version-bearing
  line, in the form `tools/set_version.py` already writes for
  `docker-compose.ddev-sync.yaml`.

- [ ] **Step 1: Write the failing tests**

`tests/test_endpoint_template.py` (unit, no containers) parses the template
with the project's own `yamlfile` helper and asserts the properties the spec
argues for — each one a sentence from the spec that would otherwise be a
comment nobody can check:

```python
def test_the_endpoint_is_published_on_loopback_only() -> None:
    # every published port starts with "127.0.0.1:" - a bare "2222:22" would
    # put a production endpoint on every interface the host has

def test_every_payload_mount_is_read_only() -> None:
    # all volumes end in ":ro" except the host-key volume, which it must write

def test_the_host_key_is_persisted() -> None:
    # a named volume is mounted at /etc/ssh/keys

def test_the_template_names_no_user() -> None:
    # `user:` is absent: the entrypoint needs root, and a `user:` other than
    # the image's own uid exits 255 with "No user exists for uid ..."

def test_the_template_asks_for_authorized_keys() -> None:
    # DDEV_SYNC_AUTHORIZED_KEYS is in `environment`

def test_the_template_names_the_released_image_tag() -> None:
    # the tag equals version.json's version
```

In `tests/test_set_version.py`, one test that `rewrite()` writes the template's
tag, and in `tests/test_version_coherence.py` the template joins the files that
must already agree in the tree.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_endpoint_template.py tests/test_set_version.py tests/test_version_coherence.py --no-cov`
Expected: FAIL — the template does not exist; `set_version` has five targets.

- [ ] **Step 3: Write `endpoint/docker-compose.ddev-sync-endpoint.yaml`**

The spec's template, with measurement 4's correction and comments explaining
each line to the operator who copies it (what to change: the image tag is
written by the release, the two uids, the mounts, the published port). Service
name `ddev-sync`, `restart: unless-stopped`, `networks: [default]`,
`DDEV_SYNC_AUTHORIZED_KEYS` from the stack's environment, `DDEV_SYNC_UID`/
`DDEV_SYNC_GID` as `"33"`, `ports: ["127.0.0.1:2222:22"]`, the payload mounts
`:ro`, and `ddev_sync_hostkeys:/etc/ssh/keys`.

- [ ] **Step 4: Add the sixth target to `tools/set_version.py`**

One entry in `TARGETS`, anchored on the image reference
(`noixdev/ddev-sync-endpoint:(?P<version>…)`), with `_unchanged` as the form —
it is an image tag, so semver, not PEP 440. Extend the module docstring's
inventory from five files to six and say what breaks when it is stale: a
template naming a tag nobody published is a stack that cannot start.

- [ ] **Step 5: Document the keys in `ddev-sync/ddev-sync.example.yaml`**

A commented target beside the existing ones:

```yaml
# targets:
#   production:
#     provider: docker        # server (the default) | docker
#     host: prod.example      # the Docker host, as your ordinary user
#     user: deploy
#     sync_port: 2222         # where the stack publishes the sync endpoint
#     path: /data/            # the path inside the endpoint, not on the host
#     stage: production
```

With the three sentences a reader needs: `path` is the endpoint's, `host` and
`user` are the first hop, and WordPress is not supported under this provider
(spec decision 7).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest`
Expected: PASS, 100 % coverage.

- [ ] **Step 7: Commit**

```bash
git add endpoint/docker-compose.ddev-sync-endpoint.yaml tests/test_endpoint_template.py \
        tools/set_version.py tests/test_set_version.py tests/test_version_coherence.py \
        ddev-sync/ddev-sync.example.yaml
git commit -m "feat(endpoint): a compose template that publishes only on loopback"
```

---

## Task 3: The engine against the real endpoint

**Files:**
- Create: `tests/integration/test_docker_provider.py`
- Modify: `tests/integration/docker-compose.yaml`,
  `tests/integration/conftest.py`, `tests/integration/test_jump_host.py`
- Move: `tests/integration/jump-init/` → `tests/jump-init/`

**Interfaces:**
- Consumes: Task 1's image (built by compose from `../../endpoint`); plan 1's
  resolution (`provider`, `sync_port`, `host_key_alias`) and doctor rows.
- Produces: a `known_hosts_for` fixture in `conftest.py` that registers and
  scrubs an arbitrary list of `known_hosts` entries, replacing
  `test_jump_host.py`'s module-local fixture.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_pull_through_the_endpoint_brings_the_files_across() -> None:
    # rsync_pull_command against provider: docker - the file arrives by name
    # and by content, as in test_jump_host.py

def test_the_doctor_finds_the_endpoint_the_path_and_its_mounts() -> None:
    # PreflightReport: reachable is True, path_exists is True,
    # path_readable is True - the first real exercise of plan 1's row

def test_a_path_the_session_uid_cannot_read_is_named_as_unreadable() -> None:
    # Review Focus 3: a directory mode 0700 owned by another uid ->
    # path_readable is False, and the rendered advice names the uid

def test_a_dump_through_the_endpoint_reaches_the_database_by_service_name() -> None:
    # db.dump with remote_credentials: env, reading the .env the endpoint
    # mounts; the dump holds a seeded table name

def test_two_endpoints_on_one_loopback_port_are_told_apart() -> None:
    # Review Focus 5: two targets differing only in host_key_alias, both
    # connect; without the alias the second is "REMOTE HOST IDENTIFICATION
    # HAS CHANGED"

def test_a_replaced_host_key_is_refused_with_advice_about_the_volume() -> None:
    # Review Focus 4: scrub the alias entry, re-register a different key,
    # connect -> the failure names the host-key volume
```

The last two need a second endpoint and a regenerated key; both are served by
starting a throwaway endpoint container from Task 1's fixture rather than by
touching the suite's own stack.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -m integration --no-cov tests/integration/test_docker_provider.py`
Expected: FAIL — there is no `endpoint` service.

- [ ] **Step 3: Add the `endpoint` service to the integration stack**

In `tests/integration/docker-compose.yaml`:

```yaml
  endpoint:
    build:
      context: ../../endpoint
    # The jump's network namespace, which is what a port published on the
    # Docker host's loopback actually is: `127.0.0.1:22` on the jump is this
    # container, and this container still resolves `db` by service name.
    network_mode: "service:jump"
    environment:
      - DDEV_SYNC_UID=${DDEV_SYNC_UID:-33}
      - DDEV_SYNC_GID=${DDEV_SYNC_GID:-33}
    volumes:
      - ./.keys/id_ed25519.pub:/etc/ddev-sync/authorized_keys:ro
      - ./fixtures/remote:/srv/app:ro
      - endpoint-hostkeys:/etc/ssh/keys
    healthcheck:
      test: ["CMD-SHELL", "grep -qiE ':0016 [0-9A-F]+:0000 0A' /proc/net/tcp"]
      interval: 2s
      timeout: 5s
      retries: 30
```

Plus the `volumes:` block for `endpoint-hostkeys`. The authorized-keys **file**
source is used here deliberately: the environment variable is covered by Task
1, and this way both sources are exercised by a real sync between them.

- [ ] **Step 4: Move the forwarding hook and point both suites at it**

`git mv tests/integration/jump-init tests/jump-init`, and change the `jump`
service's mount to `../jump-init:/custom-cont-init.d:ro`. Task 4 mounts the
same directory; one copy of the hook, two suites.

- [ ] **Step 5: Lift the known-hosts fixture into `conftest.py`**

`known_hosts_for(*entries: str)` — scrub, register by `ssh-keyscan` where the
entry is reachable, yield, scrub again — and rewrite
`test_jump_host.py`'s fixture to use it. Same behaviour, including the lesson
already recorded there: `ssh-keygen -R remote` does not match `[remote]:2222`,
and the jump's own key must be *registered*, because the ProxyJump child does
not inherit the engine's `-o` options.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -m integration --no-cov -v`
Expected: PASS — the pre-existing tests plus the ten from Task 1 plus these
six.

- [ ] **Step 7: Commit**

```bash
git add tests/integration tests/jump-init
git commit -m "test(integration): sync through the endpoint the stack would publish"
```

---

## Task 4: The matrix cell

**Files:**
- Create: `tests/matrix/cells/mxdocker/{ddev-sync.yaml,install.sh,seed.sql,keep-data.txt}`
  and `tests/matrix/cells/mxdocker/remote/storage/app/public/matrix-upload.txt`
- Modify: `tests/matrix/docker-compose.yaml`, `tests/matrix/cells.py`,
  `tools/apply_verified_rows.py`, `tests/test_apply_verified_rows.py`
- Possibly modify: `tests/matrix/stack.py` (only if a new environment variable
  is needed — `MATRIX_KEYS_DIR` and `MATRIX_REMOTE_DIR` already exist)

**Interfaces:**
- Consumes: Task 1's image, Task 3's `tests/jump-init/`, and the existing
  `Cell` registry contract — a cell is a registry entry plus recipe files, not
  a new test.
- Produces: the cell name `mxdocker` and `Cell.verified_row: bool = True`.

**Why a cell at all** (spec): it is the only level where the session uid, the
database's service name and the persistent host key are answered *together*,
with a real framework and the engine itself in a container.

- [ ] **Step 1: Write the failing registry entry and recipe**

`Cell(name="mxdocker", profile="laravel", project_type="laravel",
docroot="public", php_image="php:8.3-cli", …)` — the same assertions as
`mxlaravel` (`required_tables=("users",)`, `password_column=("users",
"password")`, `truncated_tables=("sessions", "jobs", "failed_jobs")`,
`identity_column="name"`, `seeded_users=("Alice Hofer", "Bruno Kalb")`),
with `services=("jump", "endpoint")` and `verified_row=False`.

`cells/mxdocker/ddev-sync.yaml` declares the provider and reads its credentials
from the far side, which is the spec's claim that the endpoint carries none of
its own:

```yaml
version: 1
targets:
  production:
    provider: docker
    host: jump
    user: deploy
    port: 2222
    sync_port: 22
    path: /srv/app/
    stage: production
database:
  remote_credentials: env
```

`install.sh` is `mxlaravel`'s, plus the one thing this cell needs: a `.env` in
the remote tree naming `remote-db` as `DB_HOST`, which the endpoint resolves
because it shares the jump's network namespace.

- [ ] **Step 2: Run the cell to verify it fails**

Run: `uv run pytest -m matrix -k mxdocker --no-cov -v`
Expected: FAIL — compose has no `jump` or `endpoint` service.

- [ ] **Step 3: Add both services to the matrix stack**

`jump`: `linuxserver/openssh-server:latest` with the same four environment
variables the integration suite's jump uses, the keys mount, the
`../jump-init:/custom-cont-init.d:ro` hook, `profiles: [jump]`, and the
authorized-key healthcheck.
`endpoint`: built from `../../endpoint`, `network_mode: "service:jump"`,
`profiles: [endpoint]`, `${MATRIX_KEYS_DIR}/id_ed25519.pub` as the
authorized-keys file, `${MATRIX_REMOTE_DIR}:/srv/app:ro`, a host-key volume,
`DDEV_SYNC_UID`/`DDEV_SYNC_GID` set to `${MATRIX_UID}`/`${MATRIX_GID}` — the
uid that owns the files the harness wrote on the host, which is exactly the
misconfiguration the spec warns about, pinned to the right value here.

- [ ] **Step 4: Teach `apply_verified_rows` about a cell that verifies no profile**

`emit_row` for a cell with `verified_row=False` emits a single comment line
naming the cell and what it verifies instead of a table row. Without this the
cell would fill the `laravel` row and **overwrite** `mxlaravel`'s evidence with
a run that was not about the profile's commands — the same trap the tool's
existing comment describes for feature cells.

Tests in `tests/test_apply_verified_rows.py`:
```python
def test_a_provider_cell_emits_no_profile_row() -> None:
def test_apply_ignores_the_comment_a_provider_cell_emits() -> None:
```

- [ ] **Step 5: Run the cell to verify it passes**

Run: `uv run pytest -m matrix -k mxdocker --no-cov -v`
Expected: PASS. The run installs Laravel, raises the stack and syncs through
the endpoint; expect it to take as long as the other framework cells.

- [ ] **Step 6: Run the unit suite and the gates**

Run: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy --strict src/ddev_sync tests tools`
Expected: clean — `tests/matrix/test_feature_registry.py` and the other
registry tests parametrise over `CELLS`, so a new cell is checked statically
here too.

- [ ] **Step 7: Commit**

```bash
git add tests/matrix tools/apply_verified_rows.py tests/test_apply_verified_rows.py
git commit -m "test(matrix): sync a real framework through the docker provider"
```

---

## Task 5: The second image in the release pipeline

Spec decision 10: this task runs only once Task 4 is green. Two repositories —
`github-workflows` first, because the caller cannot reference inputs that do
not exist yet.

**Files:**
- Modify: `../github-workflows/.github/workflows/create_docker_changelog_version_release.yaml`,
  `../github-workflows/blueprints/release-docker.yaml`,
  `../github-workflows/README.md`
- Modify: `.github/workflows/release.yaml`, `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: the shared workflow's existing `steps.ver.outputs.tag` /
  `.next` / `.is-prerelease` and its Docker Hub login.
- Produces: inputs `extra_image` and `extra_image_dockerfile` (both default
  `""`), and the output `extra_image_digest`.

**Why an input rather than a second shared workflow:** the tag rules —
`latest=false`, the gated `latest`, `{{major}}.{{minor}}`, `{{major}}` — are a
decision this repository already made once, in one place. A separate workflow
would be a second implementation of it, and the second implementation is what
goes wrong. For the same reason the second image is built in the same job,
after the version is written and under the same `dry_run` gate.

- [ ] **Step 1: Add the two inputs and the build to the shared workflow**

Two `workflow_call` inputs with descriptions in the file's own voice
(`extra_image`: the Docker Hub repository name under the same namespace;
`extra_image_dockerfile`: its dockerfile path, context stays the repository
root). Then, directly after the existing `build` step, a pair of steps guarded
by `if: ${{ inputs.extra_image != '' }}`:

- `Compute the tags for the extra image` — `docker/metadata-action` at the
  pinned commit, identical `flavor`, `tags` and `labels` blocks as the main
  image, with `images: docker.io/${{ vars.DOCKERHUB_NAMESPACE }}/${{
  inputs.extra_image }}` and the title label `${{ inputs.extra_image }}`.
- `Build and push the extra image` — `docker/build-push-action` at the pinned
  commit, same `platforms`, `push`, `provenance`, `sbom` and cache settings,
  plus `file: ${{ inputs.extra_image_dockerfile }}`.

Add `extra_image_digest` to the job and workflow outputs beside
`image_digest`.

- [ ] **Step 2: Document it where a caller looks**

`blueprints/release-docker.yaml` gains the two inputs, commented out with one
line on when to set them; `README.md`'s entry for this workflow gains a
sentence. No version bump of the shared workflow is involved — callers pin
`@main`, as `release.yaml` already does.

- [ ] **Step 3: Write the failing caller test**

In `tests/test_release_workflow.py`, which already pins this workflow's job
graph:

```python
def test_the_release_passes_the_endpoint_image() -> None:
    # release.with == {... "extra_image": "ddev-sync-endpoint",
    #                      "extra_image_dockerfile": "endpoint/Dockerfile"}

def test_the_smoke_job_pulls_both_images() -> None:
    # the smoke matrix runs on ubuntu-latest and ubuntu-24.04-arm and names
    # DOCKERHUB_IMAGE as well as ddev-sync-endpoint
```

Run: `uv run pytest tests/test_release_workflow.py --no-cov`
Expected: FAIL — `release.yaml` passes neither input.

- [ ] **Step 4: Pass the inputs and smoke the published endpoint**

In `.github/workflows/release.yaml`: the two inputs on the `release` job, and
in `smoke` — which already runs on both architectures — three steps against
`docker.io/${{ vars.DOCKERHUB_NAMESPACE }}/ddev-sync-endpoint:${{
needs.release.outputs.version }}`: it pulls; it refuses to start with no
authorized key; it carries `sh`, `rsync`, `mysqldump`, `mysql` and none of
`php`, `docker`, `rclone`. The same three the CI job runs, now against the
artifact a user would pull, on the architecture they would pull it for.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest && uv run ruff check && uv run mypy --strict src/ddev_sync tests tools`
Expected: PASS, clean.

- [ ] **Step 6: Commit both repositories**

```bash
# in ../github-workflows
git commit -am "feat: build a second image alongside the released one"
# in ddev-sync
git add .github/workflows/release.yaml tests/test_release_workflow.py
git commit -m "ci: release the endpoint image with the engine"
```

- [ ] **Step 7: Rehearse before releasing**

Do not dispatch a release in this task. The last release pipeline change was
rehearsed with `dry_run: true` first, and that rehearsal is what found a defect
four reviews had missed. Report that the dry run is the next step and leave the
dispatch to the owner — a push to a shared branch and a publish are theirs.

---

## Out of scope, deliberately

- **The operator guide** (how to add the endpoint to an existing stack, how to
  distribute the authorized keys, how to roll the host key) — user
  documentation, so `noix-docs`, not this repository.
- **WordPress under `provider: docker`** — spec decision 7 refuses it, plan 1
  implemented the refusal, and reading `$table_prefix` from the mounted
  `wp-config.php` is its own design.
- **Swarm, Kubernetes, a host without SSH, a second hop in front of the Docker
  host** — further providers; the seam is shaped for them and nothing is built.
- **`ddev sync` emitting the compose snippet** — the spec leaves it open.
- **The `ssh_alias` port under `provider: docker`** — plan 1's deferred ruling
  stands: a declared port becomes the jump's port, and an alias's own port is
  not read.
