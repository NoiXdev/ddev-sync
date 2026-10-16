# Docker sync provider design

**Goal.** Let a target whose production runs in Docker be synced without
giving every developer root on the production host — by reaching an
SSH-speaking endpoint container that lives inside the stack, through the host,
as an ordinary unprivileged user.

**Shape in one sentence.** `provider: docker` is resolved into the jump-host
target the engine can already express, so the provider disappears before any
command is built.

## Context, measured

Facts this design rests on. Each was read, not recalled.

- **The remote seam is three functions.** Everything the engine does on the far
  side goes through `transport/ssh.py`: `ssh_command`, `scp_download_command`
  and `rsync_pull_command`. The operations above them never mention ssh;
  `db.dump` asks for "run `mysqldump` over there and give me stdout"
  (`operations/database.py:285`), and nothing more.
- **Jump hosts are already threaded through all three.** `_jump_flag` is
  called from the ssh prefix (`transport/ssh.py:69`), from the scp command
  (`:87`) and from the `-e` option rsync is given (`:116`). A target with a
  jump host therefore works identically for commands, downloads and file
  transfers.
- **…but a jump host has never actually connected.** `jump_host` appears in
  five test files — `tests/config/test_models.py`,
  `tests/config/test_targets.py`, `tests/engine/test_plan.py`,
  `tests/operations/test_preflight.py`, `tests/transport/test_ssh.py` — and
  every one of them asserts the **argv**. It appears nowhere under
  `tests/integration/` or `tests/matrix/`. The path this design depends on is
  known to build the right command line and is not known to work.
- **The engine refuses a changed host key.** `SSH_CONNECTION_OPTIONS` sets
  `StrictHostKeyChecking=accept-new`: first contact is trusted, a changed key
  is refused.
- **The preflight probe is a shell one-liner.** `_probe` runs
  `sh -c 'if [ -d <path> ]; then echo …; fi'` over ssh
  (`operations/preflight.py:213-225`). It needs `sh` on the far side and
  nothing else.
- **Free space is measured locally.** `_free_bytes` is
  `shutil.disk_usage(context.project.root).free`
  (`operations/preflight.py:329-330`) — it is about the volume the dump lands
  on, not the remote. No provider affects it.
- **There are three credential sources**, `("env", "explicit", "wp_cli")`
  (`operations/database.py:30`), and `table_prefix` — which resolves
  `${WP_TABLE_PREFIX}` in the WordPress profile's sanitize rules — "is only
  ever set by the `wp_cli` source" (`operations/database.py:42-46`).
- **Docker socket access is host root.** The add-on's own
  `docker-compose.ddev-sync.yaml` says of the socket it mounts locally: "This
  is host-root-equivalent access and is documented as such in SECURITY.md."
  The same property would hold on a production host.
- **The integration suite already stands up an sshd.** Its compose file brings
  up `linuxserver/openssh-server`, two `mariadb:11` containers and an
  `alpine:3.21` stand-in for the web container.

## Goals

1. A developer who may sync needs **no Docker rights and no privileged access**
   on the production host — only ordinary SSH to it.
2. No new protocol and no new authentication scheme.
3. `provider: server` keeps today's behaviour exactly; existing configurations
   are untouched.
4. The provider is resolved away: no `if provider == …` in `operations/`,
   `transport/`, the executor or the reporter.
5. Swarm, Kubernetes and a host without SSH become **further providers** later,
   not a rewrite.

## Non-goals

- Deploying the endpoint container. It is placed in the stack by whoever
  deploys the stack, which is the person who already has that privilege.
- Managing a tunnel. ProxyJump is the tunnel.
- Supporting Swarm or Kubernetes now. The seam is shaped so they fit; nothing
  is built for them.
- WordPress's `wp_cli` credential source under this provider — see decision 7.

## Decisions

### 1. The endpoint speaks SSH rather than an API

The container runs sshd. The engine's existing `ssh_command`,
`rsync_pull_command` and `scp_download_command` then work against it verbatim.

*Why.* An API would need a protocol, an authentication scheme, and a version
handshake between the engine and a container sitting in production. A dumb
endpoint ages well: there is no wire format to keep in step, and `authorized_keys`
is an authentication scheme the operators already run.

*Cost if wrong.* Running sshd in a container is a well-worn shape, but it does
mean a host key and a user database inside the image — see decisions 5 and 6,
which exist because of this choice.

### 2. The engine reaches it through ProxyJump, not a managed tunnel

A `docker` target resolves to: jump host = the Docker host, reached as the
developer's ordinary user; destination = `127.0.0.1:<sync_port>`, which `-J`
resolves **on the jump host**, where the stack publishes the endpoint.

*Why.* The alternative is for ddev-sync to open and own an `ssh -L` process
whose lifetime spans the sync: a background process, a free-port search, and
teardown on failure and on interrupt. ProxyJump needs none of it, and the
engine already emits `-J` for all three transport shapes.

*Cost if wrong.* The measured gap above: `-J` has never actually connected in
this project. That is what decision 9's integration work is for, and it should
be done early rather than discovered late.

### 3. The provider disappears at resolution

`provider` and `sync_port` are read while the target is resolved; what comes
out is an ordinary `ResolvedTarget` carrying a jump host. Everything downstream
is unchanged.

*Why.* A provider that leaks into the operations is unreadable by the third
provider. One that ends at resolution is a translation with a single test
point.

*The one narrow exception.* `ResolvedTarget` also carries `provider`, used
**only in text a human reads**: the doctor must be able to say "the sync
endpoint did not answer" rather than naming the host, which may have answered
perfectly well. No command builder may read it, and the field's comment says
so.

### 4. The endpoint carries three binaries, and not a fourth

`sshd`, `rsync`, `mariadb-client`. That is the server side of what the engine
invokes, and it is exactly what the preflight probe, the dump and the files
step require.

**Not `docker-cli`**, deliberately: a container standing in production must
have nothing to say to the daemon, or the privilege this design removes has
merely moved. **Not `rclone`**: S3 is pulled by the developer's side, which the
endpoint is not part of. **Not PHP or wp-cli** — see decision 7.

*Why not reuse the engine image with a second entrypoint.* It carries
`docker-cli` and `rclone`. A thing that runs permanently in production should
carry as little as possible.

### 5. The host key must survive a redeploy

The endpoint persists `/etc/ssh/ssh_host_*` in a small named volume.

*Why.* With `StrictHostKeyChecking=accept-new`, a regenerated host key is
**refused**, not re-trusted. Without this, the first redeploy breaks the sync
for every developer at once, with a message that reads like an attack.

*Cost if wrong.* A named volume per stack, which is the cheapest thing in this
document.

### 6. There is no mode in which the endpoint accepts anyone

Authorised keys are read from the `DDEV_SYNC_AUTHORIZED_KEYS` environment
variable **and**, when present, from a mounted file. Both, as a union — not a
choice, not a flag. With neither, the container exits at startup with a message
saying why.

*Why.* Two sources were considered alternatives during the design; they are
not. They are two places to keep the same list, and composing them removes the
need to pick. What was explicitly rejected is a switch — or a second image —
that turns authentication off: such a state ends up in production by accident
and is invisible in `docker ps`. Security-relevant behaviour must not depend on
which tag was pulled.

*Defence in depth, separately.* The compose template publishes the port on
`127.0.0.1` of the host only, so the endpoint is reachable solely through an
SSH forward. That is a property of the template, not an alternative to keys.

### 7. WordPress's `wp_cli` source is refused under this provider

`provider: docker` together with `database.remote_credentials: wp_cli` is a
configuration error, with a message naming `env` and `explicit`.

*Why.* wp-cli needs PHP and a WordPress installation; the endpoint has
neither and must not grow them.

*The part that is worse, and must be said.* `wp_cli` is also the only source of
`table_prefix`, which resolves `${WP_TABLE_PREFIX}` in the WordPress profile's
sanitize rules. So the gap is not only credentials: a WordPress project whose
sanitize rules use that placeholder cannot be sanitized under this provider at
all. **WordPress is therefore not supported by `provider: docker` in this
version, and the refusal says so** rather than failing later and deeper.

*The way out, deliberately not taken here.* The endpoint already mounts
`wp-config.php`, which is the very file wp-cli would read, and under Docker it
is production's own — which is the reason the existing code gives for
preferring wp-cli over a local read. Reading the prefix from that mount is a
small, legitimate change, and it is a separate decision with its own design.

### 8. Two further refusals and one meaning that shifts

`provider: docker` with `layout: deployer` is refused: a deployer layout
resolves `releases/current` on the far side, and the endpoint has no such
structure. This is a judgement call — the combination is almost certainly a
mistake, and a clear configuration error beats a confusing runtime one.

A `sync_port` without `provider: docker` is refused as well — it can only
mean the author believed the provider was set, and silently ignoring it would
sync the wrong thing from the wrong place.

`path` means **the path inside the endpoint**, not on the host. That is the one
place where a reader's intuition from `provider: server` is wrong, so the
doctor names it rather than leaving it to be inferred.

`host` and `user` keep meaning "the machine you connect to" — which under this
provider is the first hop rather than the last. The alternative was a nested
`docker:` block that avoids the ambiguity at the cost of more to type; the flat
form was chosen because the Docker host is the only machine a human thinks
about, and the container behind it is infrastructure rather than a destination.

### 9. The jump-host path is exercised for real before anything rests on it

The integration suite gains a second sshd in front of the existing one, and a
pull that runs through both hops.

*Why.* It closes the measured gap at the top of this document, it costs a few
lines in a compose file that already exists, and the `linuxserver/openssh-server`
image already in that file is a serviceable stand-in for the endpoint before
the real image exists — from the engine's point of view an endpoint is an sshd
with rsync and mysqldump.

### 10. The endpoint image does not join the release pipeline before a matrix cell exists

*Why.* Yesterday's first rehearsal of the release pipeline found a defect that
nobody had predicted, in a workflow that had been reviewed four times. A second
production artifact earns the same path, not a shorter one.

## The configuration

```yaml
targets:
  production:
    provider: docker          # default: server, which is today's behaviour
    host: prod.example        # the Docker host, as an ordinary user
    user: deploy
    sync_port: 2222           # where the stack publishes the endpoint
    path: /data/              # the endpoint's path, not the host's
    stage: production
```

resolves to the shape the engine already supports:

```
jump_host: prod.example   jump_host_user: deploy   jump_host_port: 22
host: 127.0.0.1           user: sync               port: 2222
```

The inner user is **fixed by the image**, not configured: the endpoint has one
account and no reason to have a second, and a key that is in its
`authorized_keys` is by definition allowed in as that account. Making it a
setting would add a value every configuration must carry and no configuration
would ever vary.

A bastion **in front of** the Docker host would be a second hop. `-J` takes a
comma-separated chain, so the form does not preclude it; it is not built until
someone needs it.

## The endpoint container

One per stack, in the stack's own compose file. That boundary is the
authorisation: whoever can reach the endpoint of project A cannot reach project
B, because the stack already draws that line. A single endpoint per host
serving every stack was rejected for the opposite reason — it would be a master
key, and it would require rebuilding the per-project authorisation that the
stack gives for free.

```yaml
  ddev-sync:
    image: noixdev/ddev-sync-endpoint:<version>
    restart: unless-stopped
    user: "33:33"                        # the app service's uid
    networks: [default]                  # reaches `db` by service name
    ports:
      - "127.0.0.1:2222:22"              # only through an SSH forward
    environment:
      DDEV_SYNC_AUTHORIZED_KEYS: "${DDEV_SYNC_AUTHORIZED_KEYS}"
    volumes:
      - app_storage:/data/storage:ro
      - ./.env:/data/.env:ro
      - ddev_sync_hostkeys:/etc/ssh/keys
```

**Read-only mounts**: the sync only ever pulls, and that should be enforced by
the mount rather than left to the engine's good behaviour.

**No database credentials of its own.** The engine reads them from the `.env`
on the far side, and that file is in the volume the endpoint mounts. The host
named in it is the service name, which resolves because the endpoint is in the
stack's network. This works *because* of the per-stack boundary, not in spite
of it.

**The uid is the likeliest misconfiguration.** The mounted volumes are owned by
the application's uid, so `user:` must match. The doctor checks it; a failed
rsync halfway through is not where that should be discovered.

## What the engine gains

- Two configuration keys, `provider` and `sync_port`, and their resolution into
  the jump-host form.
- Three refusals at validation time: `wp_cli`, `layout: deployer`, and a
  `sync_port` without `provider: docker`.
- One field on `ResolvedTarget`, for human-facing text only.
- Doctor rows: the endpoint answered; the path exists inside it; **the endpoint
  can read its mounts**.

A doctor row that was planned and is **not** needed: whether the project's
files are a bind mount or a named volume. That question mattered only while the
design ran commands on the host. The endpoint sees its mount the same way
either way, so the question dissolved with the approach that raised it.

## What does not change

`operations/`, the three builders in `transport/ssh.py`, the executor, the
reporter, every profile, and the setup step — which runs in the **local** web
container and never touched the remote.

## Testing

**Unit.** The resolution, the three refusals, the doctor's wording. The
translation is where the whole provider lives, so this is where its coverage
lives.

**Integration.** Decision 9's second sshd, and a pull through both hops. This
is the highest-value test in the plan, because it closes a gap that predates
this work.

**Matrix.** A cell that raises a real stack — application volume, database,
endpoint — and syncs a real framework through it. It is the only level that
answers whether the file ownership, the database's service name and the
persistent host key work *together*, which are precisely the three things
flagged as "bites in week three".

## Rejected, and why — so these do not come back in three months

**`docker exec` over SSH, with no new container.** The smallest change by far:
the provider would wrap each remote command as
`ssh host 'docker exec -i <container> …'`, and nothing else would move. It was
rejected on privilege: Docker socket access is root on the host, and the docker
group is not a lesser form of root but root with an extra step. Under that
approach every developer who may sync may also take over the production host.
Under this one the privilege is spent once, at deploy time, by someone who
already holds it.

**Two images, "secure" and "open".** An image whose authentication is off will
eventually reach production by accident, and nothing in `docker ps` would show
it. Also a second artifact to build, publish and patch, for a behaviour nobody
deliberately wants.

**A mode flag that disables authentication.** The same defect in one line
instead of one tag, plus a doubling of what must be tested and understood.

**One endpoint per host.** See the per-stack section above.

**An HTTP API instead of sshd.** A protocol, an authentication scheme and a
version handshake with a container that lives in production — all to replace
three transport functions that already exist and already work.

## Open, and deliberately not decided here

- WordPress under `provider: docker`, by reading `$table_prefix` from the
  mounted `wp-config.php` (decision 7).
- Swarm, Kubernetes, and a host without SSH, each as a further provider.
- A second hop in front of the Docker host.
- Whether `ddev sync` should emit the compose snippet rather than leaving it to
  documentation.
