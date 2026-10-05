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
- **The sync sidecar will mount the Docker socket.** Framework commands run in the
  project's own web container via `docker exec`, which requires
  `/var/run/docker.sock` inside the sidecar. Anything that can talk to that socket
  can start a privileged container on the host. The alternative — installing the
  engine into the web image — was rejected because it means a local image build on
  every developer's first start.

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
