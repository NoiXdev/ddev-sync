"""Check that a sync can succeed before starting to move gigabytes.

One ssh round trip answers the two questions that matter for connectivity
alone - can we connect, and is the deploy path there - because two round
trips through a jump host is two chances for the user to wait.

`check()` (the `doctor` command) additionally probes two things that need no
ssh round trip: whether the web container answers a `docker exec` through the
mounted docker socket (one local command, skipped when the socket is absent or
unusable) and whether the forwarded ssh agent socket is usable by this user (a
filesystem check only).

When the sanitize step is also selected, the ssh side grows to up to three
round trips: the connectivity probe above, then (if needed) a `read_remote_
credentials` fetch - for a `wp_cli` source, resolving `${WP_TABLE_PREFIX}`
means asking production's wp-cli for it - and finally a `SHOW TABLES` that
checks every non-tolerated `requires_table` the profile's sanitize rules
declare, before the dump even starts. The credentials fetch is memoised on
`ExecutionContext.cache` (see its own docstring), so a sync that already
paid for it elsewhere in the same run does not pay twice. A missing table
costs nothing here (nothing has moved yet), but found only after the import
it means a sanitize rule silently never ran and the preceding steps in the
run already left production data behind.
"""

import os
import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddev_sync.config.models import Provider
from ddev_sync.operations.base import ExecutionContext, OperationError, redact
from ddev_sync.operations.database import (
    RemoteCredentials,
    read_remote_credentials,
    remote_failure,
    remote_tls_options,
)
from ddev_sync.operations.fake import parse_columns
from ddev_sync.operations.files import validate_path_pairs
from ddev_sync.operations.sanitize import SanitizeRule, active_rules
from ddev_sync.transport.container import web_command
from ddev_sync.transport.runner import CommandResult
from ddev_sync.transport.ssh import remote_endpoint, ssh_command

MIN_FREE_BYTES = 2 * 1024**3

# A module constant, not an inline literal: tests monkeypatch it rather than
# requiring a real socket on the machine running them.
DOCKER_SOCKET = Path("/var/run/docker.sock")

_OK = "ddev-sync-ok"
_NO_PATH = "ddev-sync-no-path"
# A directory that exists and cannot be listed. Its own outcome rather than
# a flavour of `_NO_PATH`: under `provider: docker` this is the likely
# misconfiguration - mounts owned by the application's uid and a `user:`
# that does not match - and telling someone their path is missing sends them
# to check the one thing they got right.
_UNREADABLE = "ddev-sync-unreadable"


@dataclass(frozen=True)
class PreflightReport:
    """What preflight found, for the doctor command to render.

    `sanitize_tables_ok` is `None` when the sanitize-table probe was not
    meaningfully run at all - either connectivity already failed (`ssh_ok`
    or `remote_path_ok` is `False`, so there is no remote database to query)
    or no sanitize rule requires a table in the first place. `doctor`
    renders no row for that case rather than a misleading OK; `True`/`False`
    mean the probe actually ran and found every required table present, or
    did not.

    `web_ok` and `agent_ok` default to `None` only so that a report built with
    the three original positional fields still renders; `doctor` renders no
    row for a `None`. Both probes always return a bool, so a report that
    `check()` produced never carries `None` there - `sanitize_tables_ok` is
    the field that genuinely can be. `web_ok` says
    whether a `web` step could reach the project's DDEV web container at all;
    `agent_ok` whether the forwarded ssh agent socket is usable by this user.

    `ssh_detail` is what the `ssh connection` row shows beside the verdict: the
    host when ssh connected, and when it did not, the host plus ssh's own
    reason on one line (see `_one_line_reason`). It defaults to empty like
    the other details, and `doctor` then falls back to the bare host, so a
    report built without it still renders a useful row.

    `credentials_ok` is `None` when the probe was not meaningfully run -
    connectivity already failed, so there is no remote to read from - and
    `True`/`False` when it ran. Unlike `sanitize_tables_ok` there is no second
    reason to skip it: this is the one probe that runs for *every* profile -
    `profiles/generic.yaml` has `sanitize.rules: []`, so the table probe
    never runs at all there, which is exactly the blind spot this field
    closes. When `credentials_ok` is `True`, `credentials_detail` names the
    database a sync would dump - built from `user`, `host`, `port` and `name`
    only, and deliberately carrying no password, because `render_doctor`
    writes it to the terminal unscrubbed. That guarantee is scoped to the
    success branch only: when `credentials_ok` is `False`, the detail is
    whatever the credential source's own `OperationError` said
    (`_check_credentials` below), collapsed to one line and bounded in
    length. For a `wp_cli` source that can carry the remote's raw `stderr`
    from a command chain that reads `DB_PASSWORD` - and nothing can redact a
    password out of it there, because at that point in the failure it was
    never successfully read in the first place.
    """

    ssh_ok: bool
    remote_path_ok: bool
    free_bytes: int
    sanitize_tables_ok: bool | None = None
    sanitize_tables_detail: str = ""
    # The docs slug of the error behind a failed sanitize row, when it has its
    # own (a certificate failure); `render_doctor` prefers it to the row's default.
    sanitize_tables_doc: str | None = None
    web_ok: bool | None = None
    web_detail: str = ""
    agent_ok: bool | None = None
    agent_detail: str = ""
    ssh_detail: str = ""
    credentials_ok: bool | None = None
    credentials_detail: str = ""
    # `None` when the probe never ran: connectivity failed, so the question
    # was not asked. `False` means the path is there and could not be listed.
    path_readable: bool | None = None

    @property
    def free_space_ok(self) -> bool:
        """Whether `free_bytes` clears the threshold `execute` insists on.

        A property on the report rather than the comparison written out at
        each call site: `cli/render.py` renders the row, `cli/commands.py`
        decides `doctor`'s exit code, and `execute` above aborts a sync -
        three readings of one threshold, which is how the exit code came to
        say "fine" about the very condition the row next to it said FAIL
        about and a real sync refuses outright.
        """
        return self.free_bytes >= MIN_FREE_BYTES


class PreflightOperation:
    """Verifies ssh access, the remote deploy path and local disk space."""

    def execute(self, context: ExecutionContext, params: Mapping[str, Any]) -> None:
        """Raise unless everything preflight checks is in order.

        `validate_path_pairs` runs first: it is local, network-free
        validation of `files.paths`, and it exists specifically so a bad
        entry - missing a key, or a `local` that escapes the project root -
        is caught before a single byte moves, not mid-transfer after earlier
        pairs have already rsynced.
        """
        validate_path_pairs(context)
        # Announced before the round trip, not after: a probe that is still
        # waiting is the one worth naming, and the next line appearing is how
        # a reader learns this one came back. `validate_path_pairs` above gets
        # none - it is local and instant, and a line for it would only teach
        # the reader to skip these.
        context.progress(f"reaching {remote_endpoint(context.target)}")
        report, stderr = self._probe(context)
        if not report.ssh_ok:
            message, slug = self._ssh_help(context, stderr)
            raise OperationError(message, doc=slug)
        if report.path_readable is False:
            raise OperationError(
                f"the path {context.target.path!r} exists on the far side but cannot be read. "
                "Under provider: docker this is usually a uid mismatch - set the endpoint's "
                "DDEV_SYNC_UID and DDEV_SYNC_GID to the ids that own the mounted volumes. "
                "Left to the sync it would surface in the middle of the file transfer instead.",
                doc="remote-path-is-not-readable",
            )
        if not report.remote_path_ok:
            raise OperationError(
                f"the deploy path {context.target.path!r} does not exist on "
                f"{context.target.host}; check the target's 'path' setting",
                doc="deploy-path-does-not-exist",
            )
        if report.free_bytes < MIN_FREE_BYTES:
            raise OperationError(
                f"only {report.free_bytes // 1024**2} MB free on the project volume; "
                f"a sync wants at least {MIN_FREE_BYTES // 1024**3} GB",
                doc="not-enough-free-space",
            )
        if bool(params.get("db_selected", False)):
            # Reported rather than raised by `check()`; here it must stop the
            # run, because everything after this reads the database. Gated on
            # the selection: a `files`-only sync never reads credentials, and
            # refusing it over them would break a run that was going to work.
            context.progress("reading the remote database credentials")
            ok, detail, doc = self._credentials_probe(context, report)
            if ok is False:
                # The slug the credential source's own error carried, so the
                # link stays the specific entry rather than the preflight area.
                raise OperationError(detail, doc=doc or "database")
        if bool(params.get("sanitize_selected", False)):
            context.progress("checking the tables the sanitize rules name")
            self._probe_sanitize_tables(context)

    def check(self, context: ExecutionContext) -> PreflightReport:
        """Run the probes and report, without raising on a bad result.

        Includes the credentials probe (`_check_credentials`) and the
        sanitize-table probe (`_check_sanitize_tables`, the same check
        `execute()` runs when sanitize is selected) - `doctor`'s whole job is
        "tell me whether a sync will work", so it must be able to report the
        failure modes that otherwise only surface by actually running a
        sync. `execute()`'s sanitize-table version raises because it runs
        mid-sync and must stop everything after it; these must not, because
        `check()` itself is documented never to raise.

        The credentials probe runs first so that, when it succeeds, the
        memoised credentials (`ExecutionContext.cache`) are already there for
        the table probe to reuse rather than fetching them a second time -
        either order is correct, this one just makes the shared read obvious
        to a reader.

        Also probes the two things a sync needs locally that the ssh probes
        cannot see: the web container (`_check_web_container`, one local
        `docker exec`, skipped when the docker socket is absent or unusable)
        and the forwarded ssh agent (`_check_ssh_agent`, a filesystem check).
        Both previously surfaced only mid-sync, after the dump was imported.
        """
        report, stderr = self._probe(context)
        credentials_ok, credentials_detail = self._check_credentials(context, report)
        sanitize_ok, sanitize_detail, sanitize_doc = self._check_sanitize_tables(context, report)
        web_ok, web_detail = self._check_web_container(context)
        agent_ok, agent_detail = self._check_ssh_agent()
        return PreflightReport(
            ssh_ok=report.ssh_ok,
            remote_path_ok=report.remote_path_ok,
            free_bytes=report.free_bytes,
            sanitize_tables_ok=sanitize_ok,
            sanitize_tables_detail=sanitize_detail,
            sanitize_tables_doc=sanitize_doc,
            web_ok=web_ok,
            web_detail=web_detail,
            agent_ok=agent_ok,
            credentials_ok=credentials_ok,
            credentials_detail=credentials_detail,
            agent_detail=agent_detail,
            ssh_detail=self._ssh_detail(context, report, stderr),
            # Carried from `_probe`'s report: this rebuild lists every field
            # by hand, so a new one is dropped unless it is named here.
            path_readable=report.path_readable,
        )

    def _probe(self, context: ExecutionContext) -> tuple[PreflightReport, str]:
        """Run the ssh probe once and return the report plus any stderr.

        The stderr is only meaningful when the probe failed to connect at
        all, but it is returned alongside the report - rather than stashed on
        `self` - so this object stays stateless: nothing here depends on
        `check` having been called first.
        """
        quoted_path = shlex.quote(context.target.path)
        # Three outcomes from one round trip rather than a second connection
        # for the readability question. `-r` and `-x` together are what
        # listing a directory needs: readable to see the names, executable to
        # reach what they name.
        probe = (
            f"if [ ! -d {quoted_path} ]; then echo {_NO_PATH}; "
            f"elif [ -r {quoted_path} ] && [ -x {quoted_path} ]; then echo {_OK}; "
            f"else echo {_UNREADABLE}; fi"
        )
        result = context.runner.run(ssh_command(context.target, ["sh", "-c", probe]))
        if not result.ok:
            return PreflightReport(False, False, self._free_bytes(context)), result.stderr
        output = result.stdout.strip()
        if output not in {_OK, _NO_PATH, _UNREADABLE}:
            raise OperationError(
                f"unexpected output from the remote probe on {context.target.host}: {output}"
            )
        return (
            PreflightReport(
                True,
                output in {_OK, _UNREADABLE},
                self._free_bytes(context),
                # `None`, not `False`, when the path is not there: the
                # question was never asked, and the field's contract says so.
                # Reported as False it would fire the readability refusal with
                # a message about uids for a path that simply does not exist.
                path_readable=None if output == _NO_PATH else output == _OK,
            ),
            "",
        )

    @staticmethod
    def _check_web_container(context: ExecutionContext) -> tuple[bool, str]:
        """Whether a `web` step could run at all. Never raises.

        Two failures, reported apart because the fixes differ: the socket is
        not mounted (a compose problem, in the add-on's own file), or it is
        mounted and the container is not there (the project is not running -
        `ddev start`). This is what `doctor` reports; a sync's own `execute()`
        does not probe it, so a missing container surfaces at the first step
        that needs it. Moving the probe into `execute()` needs a "does this
        plan contain a web step" signal that does not exist yet, and it is not
        reachable in practice until the compose file that mounts the socket
        ships.
        """
        if not DOCKER_SOCKET.exists():
            return False, (
                f"{DOCKER_SOCKET} is not mounted, so no command can reach "
                f"{context.project.web_container}"
            )
        if not os.access(DOCKER_SOCKET, os.R_OK | os.W_OK):
            # Mounted but unusable: on the host the socket is typically
            # root:docker, mode 0660, and a container user's group membership
            # does not carry over. The fix is the service's `group_add`, not
            # anything in this image - so the message has to say so, or this
            # looks like a missing container instead of a missing group.
            return False, (
                f"{DOCKER_SOCKET} is mounted but not usable by this user - the sync service "
                "needs the host's docker group (compose `group_add`)"
            )
        try:
            result = context.runner.run(
                web_command(context.project, ["true"], workdir=context.project.root)
            )
        except Exception as error:
            # `check()` is documented never to raise, and the `CommandRunner`
            # protocol promises nothing about exceptions: `SubprocessRunner`
            # happens to turn an `OSError` into a nonzero result, but another
            # implementation may raise anything. Catching broadly here keeps
            # that contract true by construction instead of depending on one
            # runner's internals - and a raise would cost `doctor` every other
            # row of its checklist.
            return False, f"could not ask {context.project.web_container}: {error}"
        if not result.ok:
            return False, (
                f"{context.project.web_container} did not answer: {result.failure_detail()}"
            )
        return True, f"{context.project.web_container} answers"

    @staticmethod
    def _check_ssh_agent() -> tuple[bool, str]:
        """Whether the forwarded ssh agent is usable by *this* user.

        The base design's section 12: `ddev-ssh-agent_socket_dir` holds two
        sockets, and the obvious one is the wrong one - `socket` is mode 0600
        owned by uid 501, `proxy-socket` is 0666. A sidecar running as a
        non-root user can only use the second. Asserting it here means a
        future DDEV release renaming either socket fails in `doctor`, naming
        the cause, instead of at the first dump with "Error connecting to
        agent".
        """
        configured = os.environ.get("SSH_AUTH_SOCK", "")
        if not configured:
            return False, "SSH_AUTH_SOCK is not set - run `ddev auth ssh` and restart the project"
        socket = Path(configured)
        if not socket.exists():
            return False, f"SSH_AUTH_SOCK points at {configured}, which does not exist"
        if not os.access(socket, os.R_OK | os.W_OK):
            return False, (
                f"{configured} is not readable and writable by this user - the sidecar needs "
                "the agent's `proxy-socket` (mode 0666), not `socket` (0600)"
            )
        return True, configured

    @staticmethod
    def _ssh_detail(context: ExecutionContext, report: PreflightReport, stderr: str) -> str:
        """What the `ssh connection` row says beside OK or FAIL.

        A failed probe used to show only the host, so a user whose real
        problem was `Host key verification failed.` went looking at their key.
        The host stays in front of the reason so the row still says which
        target it is about.
        """
        host = context.target.host
        if report.ssh_ok:
            return host
        return _clip(f"{host}: {_one_line_reason(stderr)}")

    @staticmethod
    def _ssh_help(context: ExecutionContext, stderr: str) -> tuple[str, str]:
        """The message for a failed ssh probe, and the docs entry that explains it."""
        if (
            "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr
            and context.target.provider is Provider.DOCKER
        ):
            # ssh is right to refuse and right to shout, but for this provider
            # the overwhelmingly likely cause is a redeploy that dropped the
            # endpoint's host-key volume - and nothing in ssh's own message
            # points there, because the address it names is the Docker host's
            # loopback rather than anything a reader can go and look at.
            return (
                "the sync endpoint's host key is not the one recorded for it. "
                "Under provider: docker a redeploy that dropped the endpoint's "
                "host-key volume looks exactly like this; check that the volume "
                "mounted at /etc/ssh/keys survived, then remove the stale "
                f"known_hosts entry.\n{stderr.strip()}",
                "host-key-changed",
            )
        if "administratively prohibited" in stderr:
            # ProxyJump opens its hop with stdio forwarding, which a hardened
            # sshd refuses with a message that explains nothing. Worth naming,
            # because the setting is on the machine being jumped through and
            # nothing else in the output points there.
            return (
                "the host in between refused to forward the connection. "
                "A jump host needs 'AllowTcpForwarding yes' in its sshd_config; "
                f"many hardened images ship 'no'.\n{stderr.strip()}",
                "jump-host-refuses-to-forward",
            )
        if "closed by remote host" in stderr or "Connection reset" in stderr:
            # Not a refusal ssh can explain: a server that accepts the TCP
            # connection and then drops it is, far more often than a broken
            # key, a ban for too many recent logins. Naming it saves a trip
            # through key and port checks that cannot help.
            return (
                f"{context.target.host} closed the connection. The server's protection "
                "against repeated logins (for example fail2ban) may have banned this "
                "address for a while; waiting usually helps. ddev-sync already reuses "
                f"one connection per run, so a single sync does not trigger it.\n{stderr.strip()}",
                "connection-closed-by-the-remote-host",
            )
        return (
            f"cannot reach {context.target.host} over ssh. "
            "Run 'ddev auth ssh' and try again, or check the target's host, "
            f"user and port.\n{stderr.strip()}",
            # The other changed-key variant: ssh's own warning is the message
            # (the text below it is unchanged), but the entry that explains it
            # is the host-key one, not the generic "cannot reach".
            "host-key-changed"
            if "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr
            else "cannot-reach-the-host-over-ssh",
        )

    @staticmethod
    def _free_bytes(context: ExecutionContext) -> int:
        return shutil.disk_usage(context.project.root).free

    @staticmethod
    def _probe_sanitize_tables(context: ExecutionContext) -> None:
        """Abort before the dump if a required sanitize table is missing.

        `active_rules` is the same selection `SanitizeOperation` itself uses, so
        a `requires_table` containing a placeholder (e.g.
        `${WP_TABLE_PREFIX}users`) arrives here already resolved to the real
        name - checked against the real name, not the literal placeholder
        text. The actual table comparison lives in
        `_first_missing_table_problem`, shared with `check()`'s non-raising
        form (`_check_sanitize_tables`) below, so the two can never quietly
        disagree about what counts as missing.
        """
        rules = active_rules(context)
        required = _required_tables(rules)
        if not required and not _fake_rules(rules):
            return
        tables = _remote_tables(context)
        problem = _first_missing_table_problem(context, required, tables)
        if problem is not None:
            raise OperationError(problem, doc="required-table-is-missing")
        problem = _column_problem(context, rules, tables)
        if problem is not None:
            raise OperationError(problem, doc="column-named-by-a-fake-rule-is-missing")

    @classmethod
    def _check_credentials(
        cls, context: ExecutionContext, report: PreflightReport
    ) -> tuple[bool | None, str]:
        """`_credentials_probe` without the docs slug, for `check()`."""
        ok, detail, _ = cls._credentials_probe(context, report)
        return ok, detail

    @staticmethod
    def _credentials_probe(
        context: ExecutionContext, report: PreflightReport
    ) -> tuple[bool | None, str, str | None]:
        """Whether the configured credential source actually yields credentials.

        The non-raising form, like `_check_sanitize_tables` below: `check()`
        is documented never to raise, and `doctor` renders its whole
        checklist only after `check()` returns, so an `OperationError`
        escaping here would cost the user the ssh, deploy-path and
        disk-space rows too.

        Guarded on connectivity for the same reason the table probe is. An
        `explicit` source could technically be judged without ssh - it reads
        the merged configuration and makes no network call - but a green
        credentials row under a red ssh row sends the reader at the wrong
        problem, and diagnosis here is sequential.

        This is the one probe that runs for *every* profile. The table probe
        returns early when no rule requires a table, which left
        `profiles/generic.yaml` - `sanitize.rules: []` and
        `database.remote_credentials: explicit` - with a green `doctor` and a
        sync that died at `db.dump`.

        On success the detail is built from the non-secret fields only.
        `render_doctor` writes it straight to the terminal and does no
        scrubbing of its own; `context.secrets` protects the verbose command
        log, not this string. On failure the detail is the credential
        source's own `OperationError`, which carries no equivalent guarantee -
        see `PreflightReport`'s own docstring for why `_from_wp_cli`'s can
        embed an unredactable password-bearing `stderr`.
        """
        if not (report.ssh_ok and report.remote_path_ok):
            return None, "", None
        source = context.config("database.remote_credentials", default="env")
        try:
            credentials = read_remote_credentials(context)
        except OperationError as error:
            # Not `_clip`: that cuts from the front and keeps the tail, right
            # for ssh's stderr (unbounded machine noise where the verdict is
            # at the end) but wrong here - these are authored messages whose
            # diagnosis is the first sentence, e.g. `_from_config`'s
            # "database.credentials is missing ...". `_bounded_front` (below)
            # keeps that sentence instead. Collapsed to one line first
            # because `_from_wp_cli`'s detail can embed the remote's raw,
            # possibly multi-line `stderr`, which would otherwise break the
            # checklist's alignment. The sibling probe below
            # (`_check_sanitize_tables`) returns `str(error)` unclipped and
            # un-collapsed: its messages are single-line and authored by this
            # module, not reflecting arbitrary remote output.
            return False, _bounded_front(" ".join(str(error).split())), error.doc
        where = f"{credentials.user}@{credentials.host}:{credentials.port}/{credentials.name}"
        return True, f"{source}: {where}", None

    @staticmethod
    def _check_sanitize_tables(
        context: ExecutionContext, report: PreflightReport
    ) -> tuple[bool | None, str, str | None]:
        """The non-raising form of `_probe_sanitize_tables`, for `doctor`.

        Returns `(None, "", None)` when there is nothing meaningful to report:
        either connectivity already failed - `ssh_ok`/`remote_path_ok` above
        already cover that, and there is no remote database left to query -
        or no sanitize rule requires a table at all. Otherwise runs the same
        probe `execute()` runs, reporting every `OperationError` it can raise
        rather than letting one propagate, because `check()` is documented to
        never raise - and `doctor` renders its whole checklist only after
        `check()` returns, so a raise here would cost the user the ssh,
        deploy-path and disk-space rows as well.

        `active_rules` is inside the guarded block, not just `_remote_tables`:
        it validates every rule (a rule missing 'name' is rejected there) and
        expands `${...}` at parse time, so an unresolvable `${WP_TABLE_PREFIX}`
        - a `wp_cli` prefix lookup that fails, or a credentials source that
        has no prefix to read at all - fails before any table is listed.
        """
        if not (report.ssh_ok and report.remote_path_ok):
            return None, "", None
        try:
            rules = active_rules(context)
            required = _required_tables(rules)
            if not required and not _fake_rules(rules):
                return None, "", None
            tables = _remote_tables(context)
            problem = _first_missing_table_problem(context, required, tables)
            if problem is None:
                # Inside the guard: a failed column query or a malformed
                # `columns` mapping must be reported, not raised (see above).
                problem = _column_problem(context, rules, tables)
        except OperationError as error:
            return False, str(error), error.doc
        if problem is not None:
            return False, problem, None
        plural = "table" if len(required) == 1 else "tables"
        return True, f"{len(required)} required {plural} present", None


_MAX_DETAIL = 200

# Lines ssh prints on stderr that are never the reason a connection failed:
# - `Warning:` is the notice `accept-new` prints on the first contact with a
#   host ("Permanently added ..."). When the connection then fails for another
#   reason it is the first thing on stderr, and reporting it as the cause would
#   send the user the wrong way once more.
# - `**` starts OpenSSH 10.1+'s three-line post-quantum notice, printed for
#   every connection to a server that offers no post-quantum key exchange - so
#   the image's 10.3 prints it ahead of a real `Permission denied`.
_NOISE_PREFIXES = ("Warning:", "**")

# What ssh prints in place of an address for a connection that went through a
# proxy (`-J`). The line carries no information and, being last, would push the
# real verdict out of the two lines `_one_line_reason` keeps.
_PROXY_PLACEHOLDER = "UNKNOWN port 65535"


def _one_line_reason(stderr: str) -> str:
    """Condense ssh's stderr into the one line that names why it failed.

    ssh's verdict is at the end: its last line, or last two when the cause
    is split in them. A changed host key prints a dozen lines of banner and
    advice that end `Host key for H has changed and you have requested strict
    checking.` and `Host key verification failed.` - and it is the first of
    those that tells a changed key from a jump host's unknown one, which ends
    in the second alone. Anything earlier is context that a checklist row has
    no room for; the full text is still what a failed sync reports.

    Lines that are never the cause (`_NOISE_PREFIXES`, `_PROXY_PLACEHOLDER`)
    are dropped first.
    """
    lines = [line.strip() for line in stderr.splitlines()]
    kept = [
        line
        for line in lines
        if line and not line.startswith(_NOISE_PREFIXES) and _PROXY_PLACEHOLDER not in line
    ]
    if not kept:
        return "ssh failed without saying why"
    return " ".join(kept[-2:])


def _clip(text: str) -> str:
    """Cap a detail so a pathological stderr cannot wreck `doctor`'s layout.

    Cuts from the front: the verdict is at the end of ssh's output, and it is
    the part that must survive.
    """
    if len(text) <= _MAX_DETAIL:
        return text
    return "..." + text[-(_MAX_DETAIL - 3) :]


def _bounded_front(text: str) -> str:
    """Cap a detail at `_MAX_DETAIL`, keeping the FRONT.

    The mirror of `_clip` above, not a reuse of it: `_clip` cuts from the
    front and keeps the tail, which is right for ssh's stderr (the verdict is
    its last line) but was wrong for a credentials failure, whose diagnosis
    is its opening sentence - reinstating `_clip` there once ate exactly that
    sentence, which is why this is a separate function instead of a shared
    one.
    """
    if len(text) <= _MAX_DETAIL:
        return text
    return text[: _MAX_DETAIL - 3] + "..."


def _required_tables(rules: list[SanitizeRule]) -> list[tuple[str, str]]:
    """Every (rule name, table) pair a non-tolerated rule needs.

    Reads both declaration forms uniformly: a `sql` rule's own
    `requires_table`, or an `operation` rule's `table` param (e.g.
    `reset_passwords`, `clear_mail_config`) - the latter already declares its
    table implicitly, so it needs no separate field.
    """
    pairs: list[tuple[str, str]] = []
    for rule in rules:
        if rule.tolerate_missing_table:
            continue
        table = _rule_table(rule)
        if table is not None:
            pairs.append((rule.name, table))
    return pairs


def _rule_table(rule: SanitizeRule) -> str | None:
    if rule.requires_table is not None:
        return rule.requires_table
    table = rule.params.get("table")
    return str(table) if table else None


def _first_missing_table_problem(
    context: ExecutionContext, required: list[tuple[str, str]], tables: set[str]
) -> str | None:
    """The message for the first required table that is missing, or `None`.

    Shared by `_probe_sanitize_tables` (which raises it) and
    `_check_sanitize_tables` (which reports it without raising) so the two
    can never describe the same missing table two different ways.
    """
    lowered = {table.lower(): table for table in tables}
    for rule_name, table in required:
        if table in tables:
            continue
        actual = lowered.get(table.lower())
        if actual is not None:
            # Exists, but under a different letter case. MySQL/MariaDB
            # with lower_case_table_names=1 (the default on macOS and
            # Windows servers) would resolve this rule's SQL fine
            # regardless - so reporting this the same as "does not
            # exist" would be wrong, and would abort syncs that were
            # actually going to work. But on a case-sensitive server
            # (the Linux default) the rule's literal, case-sensitive
            # table reference genuinely will fail at run time with the
            # database already imported - so this still has to abort,
            # just with the honest diagnosis instead of a misleading
            # "not found": name both spellings so a reader can tell
            # which one is real.
            return (
                f"sanitize rule {rule_name!r} requires table {table!r}, but the "
                f"remote database has it as {actual!r} - only the letter case "
                "differs. Nothing was downloaded or imported. If this server folds "
                "table name case, the rule would work as-is; if it does not "
                "(the common case on Linux), it will fail for real. Change the "
                f"rule's declared table name to match the exact case ({actual!r})."
            )
        remedy = (
            " This project reads its credentials with 'wp_cli', so the table "
            "name came from WordPress's own `$table_prefix` - if that is what "
            "is wrong, set 'database.wp_table_prefix' in .ddev/ddev-sync.yaml "
            "to the prefix production really uses."
            if context.config("database.remote_credentials", default="env") == "wp_cli"
            else ""
        )
        return (
            f"sanitize rule {rule_name!r} requires table {table!r}, which does "
            f"not exist on {context.target.host}'s database "
            f"({len(tables)} table(s) found). Nothing was downloaded or "
            "imported. Either this project's table is named differently - "
            "override it in .ddev/ddev-sync.yaml - or, if the table is "
            "genuinely optional for this project, add "
            "'tolerate_missing_table: true' to the rule."
        ) + remedy
    return None


def verify_credentials(context: ExecutionContext) -> None:
    """Prove that the credentials `context` resolves actually authenticate.

    Public, unlike the private probes this module otherwise keeps to itself:
    `cli/init.py`'s credentials override needs to prove a candidate
    `explicit` source really connects before writing it to disk, and the
    previous task reached through `_show_tables_result`'s underscore-prefixed
    name to do that, which review flagged - this gives it a real seam
    instead.

    Runs the same `SHOW TABLES` round trip `_remote_tables` uses to verify
    sanitize rules before a dump (`_show_tables_result`, shared by both), but
    this is not that check: it exists to catch a complete-looking but wrong
    password, which `read_remote_credentials` itself cannot - the `explicit`
    source only checks that five values were supplied, not that the remote
    accepts them. A password typed by hand is far more likely to be a typo
    than an empty field, which is the one failure that check already catches.
    """
    credentials, result = _show_tables_result(context)
    if not result.ok:
        stderr = redact(result.stderr, credentials.password).strip()
        raise remote_failure(
            f"the database credentials did not authenticate on {context.target.host}: {stderr}",
            stderr,
        )


def _remote_tables(context: ExecutionContext) -> set[str]:
    credentials, result = _show_tables_result(context)
    if not result.ok:
        stderr = redact(result.stderr, credentials.password).strip()
        raise remote_failure(
            "could not list tables on the remote database to verify sanitize rules "
            f"before the dump: {stderr}",
            stderr,
        )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _show_tables_result(context: ExecutionContext) -> tuple[RemoteCredentials, CommandResult]:
    """Run `SHOW TABLES` over the credentials `context` resolves, raw.

    Shared by `_remote_tables` (which reads the table names back) and
    `verify_credentials` (which only cares whether the command succeeded at
    all) - one ssh round trip, one place that builds the argv and runs it,
    so the two callers can never quietly disagree about how the probe itself
    is made.
    """
    credentials = read_remote_credentials(context)
    argv = _show_tables_argv(credentials, remote_tls_options(context))
    result = context.runner.run(ssh_command(context.target, argv))
    return credentials, result


def _remote_columns(context: ExecutionContext, tables: list[str]) -> set[tuple[str, str]]:
    """Every (table, column) pair `tables` has on the remote, in one query.

    Same credentials, TLS options and argv shape as the `SHOW TABLES` probe, so
    the two can never disagree about how the remote is reached. `DATABASE()`
    is the schema the connection selected (the trailing database-name argument),
    so no schema name has to be interpolated.
    """
    credentials = read_remote_credentials(context)
    names = ", ".join(
        "'" + table.replace("\\", "\\\\").replace("'", "''") + "'" for table in tables
    )
    statement = (
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({names})"
    )
    argv = _mysql_argv(credentials, remote_tls_options(context), statement)
    result = context.runner.run(ssh_command(context.target, argv))
    if not result.ok:
        stderr = redact(result.stderr, credentials.password).strip()
        raise remote_failure(
            "could not list columns on the remote database to verify sanitize rules "
            f"before the dump: {stderr}",
            stderr,
        )
    # `--batch` separates the two columns with a tab; neither name can hold one.
    return {
        (table, column)
        for table, _, column in (line.partition("\t") for line in result.stdout.splitlines())
    }


def _fake_rules(rules: list[SanitizeRule]) -> list[SanitizeRule]:
    """The active `fake` rules that name a table (a rule without one fails at run time)."""
    return [r for r in rules if r.operation == "fake" and _rule_table(r) is not None]


def _column_problem(
    context: ExecutionContext, rules: list[SanitizeRule], tables: set[str]
) -> str | None:
    """Query the columns of the fake rules' tables, then report the first one missing.

    Runs only for tables that exist: a missing one is either already reported
    (`_first_missing_table_problem`) or tolerated by its rule, and a tolerated
    one has no columns to check. No fake rule left means no round trip at all.
    """
    present = sorted({str(_rule_table(r)) for r in _fake_rules(rules)} & tables)
    if not present:
        return None
    return _first_missing_column_problem(rules, _remote_columns(context, present))


def _first_missing_column_problem(
    rules: list[SanitizeRule], columns: set[tuple[str, str]]
) -> str | None:
    """The message for the first fake column (or key column) the remote lacks, or `None`.

    Column names are compared case-insensitively: MySQL and MariaDB resolve a
    column without regard to case on every platform, so `Email` in the schema
    is the column a rule's backticked `email` updates. Table names stay exact
    (their folding depends on the server's `lower_case_table_names`). A table
    with no pair in `columns` was not queried (missing or tolerated) and is
    skipped.
    """
    present = {(table, column.lower()) for table, column in columns}
    queried = {table for table, _ in columns}
    for rule in _fake_rules(rules):
        table = str(_rule_table(rule))
        if table not in queried:
            continue
        key = str(rule.params.get("key") or "id")
        wanted = [key, *(c.column for c in parse_columns(rule.name, rule.params.get("columns")))]
        for column in wanted:
            if (table, column.lower()) not in present:
                role = "key column" if column == key else "column"
                return (
                    f"sanitize rule {rule.name!r} names {role} {column!r} on table "
                    f"{table!r}, which does not exist on the remote database. Nothing "
                    "was downloaded or imported. Fix the column name in the project's "
                    "rule, or, if the rule comes from a profile, replace it through "
                    "the project's sanitize.rules (mode: replace). Left alone it would "
                    "fail the sanitize step after the import, with production data "
                    "already on this machine."
                )
    return None


def _show_tables_argv(credentials: RemoteCredentials, tls: list[str]) -> list[str]:
    return _mysql_argv(credentials, tls, "SHOW TABLES")


def _mysql_argv(credentials: RemoteCredentials, tls: list[str], statement: str) -> list[str]:
    return [
        "mysql",
        f"--user={credentials.user}",
        f"--password={credentials.password}",
        f"--host={credentials.host}",
        f"--port={credentials.port}",
        *tls,
        "--batch",
        "--skip-column-names",
        "--execute",
        statement,
        credentials.name,
    ]
