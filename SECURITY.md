# Security

We take the security of ddev-sync seriously. Thank you for reporting vulnerabilities
responsibly.

This tool reads production credentials, dumps production databases over ssh and
writes them onto a developer's machine. A bug here does not corrupt a web page — it
leaves real customer data somewhere it should not be. Please treat findings
accordingly.

## Reporting a vulnerability

Please report security issues **privately** by email to **security@noix.dev** — not
through public GitHub issues. You can also
[open a private security advisory](https://github.com/NoiXdev/ddev-sync/security/advisories/new)
if you prefer to stay on GitHub.

To help us respond quickly, please include:

- a description of the vulnerability and its potential impact,
- step-by-step reproduction instructions (proof of concept),
- the affected version/commit and environment.

**Redact before you send.** Production hostnames, usernames, passwords and anything
out of a database dump are not needed to reproduce a report, and we would rather not
receive them.

## Process

- **Initial response:** typically within 3 business days.
- We confirm receipt, assess the report and keep you updated on progress.
- Once a fix is ready, we coordinate disclosure with you. On request, we credit you as
  the reporter.

Please give us reasonable time to fix the issue before any details are made public.

## Supported versions

Security updates are provided for the `main` branch.

## Known exposures, accepted on purpose

These are documented rather than fixed, so that you can judge whether they matter in
your environment. Reporting them again is not necessary; reporting a way to make one
of them worse is very welcome.

- **The dump's password is visible in the remote host's process list.** `mysqldump`
  is invoked with `--password=` over ssh, so anyone able to read `/proc` on the
  production host during the dump can see it. Fixing it properly means writing a
  temporary `--defaults-extra-file` on the remote, which reintroduces the remote
  temporary file that streaming removed. Revisit if a target environment makes
  process-list exposure unacceptable.
- **The sync sidecar mounts the Docker socket.** Framework commands run in the
  project's own web container via `docker exec`, which requires
  `/var/run/docker.sock` inside the sidecar. Anything that can talk to that socket
  can start a privileged container on the host. The alternative — installing the
  engine into the web image — was rejected because it means a local image build on
  every developer's first start.

  The socket's group is discovered at every project start, from inside a
  container, rather than assumed: the sidecar is added to whichever group owns the
  socket where it will see it. On Docker Desktop for Mac, the only setup
  exercised, that group is root (gid 0) inside the VM, so the sidecar is granted
  root-group access to the daemon there, not merely membership of a `docker`
  group.

- **A sync endpoint grants a shell beside production, and read *and write* on
  production's database.** Under `provider: docker` the stack publishes a small
  endpoint container and whoever holds a key in its `authorized_keys` gets a shell in
  it. That shell sits on the production stack's network, reads production's own `.env`
  from a mounted file, and the image carries a MariaDB client - so "may sync" means
  "may change the production database", not only "may read it". The read-only mounts
  stop the *files* being written and nothing more, and the engine's own code only ever
  pulls; the limit is the key list, not the container.

  What the design does remove is the privilege that was previously needed to sync from
  a Docker host at all: no Docker socket, no membership of the `docker` group, no root
  on the host. That privilege is spent once, at deploy time, by whoever already has
  it. What it does not do is make a key-holder's reach smaller than a database client
  pointed at production.

  Three further properties follow from the same design and are worth knowing before
  you deploy one:

  - **The container runs as root inside itself**, because an sshd can only serve a uid
    that has a passwd entry and only root can write one - the session's uid arrives as
    `DDEV_SYNC_UID` and the login runs as that. The container has no Docker socket, no
    added capabilities, read-only payload mounts and a port published on the host's
    loopback address only, so this is container-local root with nothing to take over.
    It is still root: drop capabilities in your own compose file if your environment
    expects it.
  - **One account for every developer.** The endpoint has a single `sync` account by
    design, so the only attribution of a sync is the key fingerprint in the endpoint's
    sshd log. Revocation is per key, which is the part that matters; "who synced" is
    answerable only from those logs.
  - **Rate limiting is deliberately off** (`PerSourcePenalties no`). Every developer
    reaches the endpoint through the same Docker host, so OpenSSH's per-source
    penalties would let one stale key's retries lock out the whole team for minutes.
    Keys and a loopback-only port are what protect the endpoint; nothing throttles a
    client that already holds a key.

- **An unknown ssh host is trusted the first time it is seen.** Every ssh, scp and
  rsync the engine runs sets `StrictHostKeyChecking=accept-new`, so a host that is not
  yet in `known_hosts` is accepted without asking. Whoever answers on the very first
  connection to a host is trusted from then on: if that first connection is
  intercepted, the interceptor's key is the one remembered, and nothing here can tell.
  This was chosen over ssh's default, which asks, because the add-on runs the engine
  in a container with no terminal and an empty `known_hosts`, so asking means the
  first connection to any host simply fails. Under the add-on `known_hosts` persists,
  so the exposure is one silent acceptance per host, ever.

  A host whose key *changes* is still refused, and that is the part worth keeping:
  `accept-new` is not `no`. `StrictHostKeyChecking=no`, usually paired with
  `UserKnownHostsFile=/dev/null`, would also accept a changed key, which is exactly
  what a man-in-the-middle presents to a host you have connected to before. For a tool
  that transfers production databases and reads the production database's credentials
  over the same channel, that protection is not worth one saved confirmation, and the
  setting must not be loosened to `no`. A refused change surfaces in `doctor` as
  `Host key verification failed.` and needs a human to decide whether the key was
  rotated or the host is not what it claims to be.

  A jump host is covered the same way. OpenSSH's own `-J` would start the bastion's
  ssh without these options, so the engine builds that hop itself, as a
  `ProxyCommand` carrying them: the bastion's key is trusted on first use and refused
  when it changes, exactly like the target's.

  Nothing is ever asked, either. Every connection runs with `BatchMode=yes`, so a
  password, a key passphrase or a second factor is a refusal that `doctor` reports,
  not a question waiting on a terminal behind the sync's own output. Keys come from
  the ssh agent; a key that needs a passphrase typed in must be added to the agent
  first.

  Each run opens one connection per target and shares it: the first ssh becomes a
  master, and every later ssh, scp and rsync to that target runs inside it, the way
  Deployer's `ssh_multiplexing` does. A server that rate-limits SSH logins would
  otherwise cut a sync off part-way. The master lives on for 60 seconds after its
  last use. Its socket is a way into the remote shell for anyone who can open it, so
  it lives in `/tmp/ddev-sync-<uid>/`, which the engine creates as `0700` and refuses
  to use when it is not a directory only you can enter.

  There is no setting to make this stricter, and none to turn it off. If you need
  the first connection to a host verified, add its key to `known_hosts` yourself
  before the first sync.

## What sanitize rules do and do not guarantee

Sanitize rules are what keep production data from living on a laptop: they truncate
sessions and queues, anonymise user tables and reset passwords after the import. They
are declared per project type, and a rule that was never run against a real
installation can silently do nothing — a `requires_table` naming a table that does
not exist, or a command that was never a command.

[`profiles/VERIFIED.md`](profiles/VERIFIED.md) records, per profile variant and per
rule, what has actually been exercised against a real installation and what has not.
Read it before trusting a profile with a production database. A profile whose rules
are listed as unverified is not a security guarantee.
