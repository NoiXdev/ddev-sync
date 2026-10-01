"""Check that a sync can succeed before starting to move gigabytes.

One ssh round trip answers both questions that matter - can we connect, and is
the deploy path there - because two round trips through a jump host is two
chances for the user to wait.

When the sanitize step is also selected, a second probe lists the remote
database's tables and checks every non-tolerated `requires_table` the
profile's sanitize rules declare, before the dump even starts: a missing
table costs nothing here (nothing has moved yet), but found only after the
import it means a sanitize rule silently never ran and the preceding steps
in the run already left production data behind.
"""

import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ddev_sync.operations.base import ExecutionContext, OperationError
from ddev_sync.operations.database import RemoteCredentials, read_remote_credentials
from ddev_sync.operations.files import validate_path_pairs
from ddev_sync.operations.sanitize import SanitizeRule, parse_rules
from ddev_sync.transport.ssh import ssh_command

MIN_FREE_BYTES = 2 * 1024**3

_OK = "ddev-sync-ok"
_NO_PATH = "ddev-sync-no-path"


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
    """

    ssh_ok: bool
    remote_path_ok: bool
    free_bytes: int
    sanitize_tables_ok: bool | None = None
    sanitize_tables_detail: str = ""


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
        report, stderr = self._probe(context)
        if not report.ssh_ok:
            raise OperationError(self._ssh_help(context, stderr))
        if not report.remote_path_ok:
            raise OperationError(
                f"the deploy path {context.target.path!r} does not exist on "
                f"{context.target.host}; check the target's 'path' setting"
            )
        if report.free_bytes < MIN_FREE_BYTES:
            raise OperationError(
                f"only {report.free_bytes // 1024**2} MB free on the project volume; "
                f"a sync wants at least {MIN_FREE_BYTES // 1024**3} GB"
            )
        if bool(params.get("sanitize_selected", False)):
            self._probe_sanitize_tables(context)

    def check(self, context: ExecutionContext) -> PreflightReport:
        """Run the probes and report, without raising on a bad result.

        Includes the sanitize-table probe (`_check_sanitize_tables`), the
        same check `execute()` runs when sanitize is selected - `doctor`'s
        whole job is "tell me whether a sync will work", so it must be able
        to report the one failure mode that otherwise only surfaces by
        actually running a sync. `execute()`'s version raises because it
        runs mid-sync and must stop everything after it; this one must not,
        because `check()` itself is documented never to raise.
        """
        report, _stderr = self._probe(context)
        sanitize_ok, sanitize_detail = self._check_sanitize_tables(context, report)
        return PreflightReport(
            ssh_ok=report.ssh_ok,
            remote_path_ok=report.remote_path_ok,
            free_bytes=report.free_bytes,
            sanitize_tables_ok=sanitize_ok,
            sanitize_tables_detail=sanitize_detail,
        )

    def _probe(self, context: ExecutionContext) -> tuple[PreflightReport, str]:
        """Run the ssh probe once and return the report plus any stderr.

        The stderr is only meaningful when the probe failed to connect at
        all, but it is returned alongside the report - rather than stashed on
        `self` - so this object stays stateless: nothing here depends on
        `check` having been called first.
        """
        quoted_path = shlex.quote(context.target.path)
        probe = f"if [ -d {quoted_path} ]; then echo {_OK}; else echo {_NO_PATH}; fi"
        result = context.runner.run(ssh_command(context.target, ["sh", "-c", probe]))
        if not result.ok:
            return PreflightReport(False, False, self._free_bytes(context)), result.stderr
        output = result.stdout.strip()
        if output not in {_OK, _NO_PATH}:
            raise OperationError(
                f"unexpected output from the remote probe on {context.target.host}: {output}"
            )
        return PreflightReport(True, output == _OK, self._free_bytes(context)), ""

    @staticmethod
    def _ssh_help(context: ExecutionContext, stderr: str) -> str:
        return (
            f"cannot reach {context.target.host} over ssh. "
            "Run 'ddev auth ssh' and try again, or check the target's host, "
            f"user and port.\n{stderr.strip()}"
        )

    @staticmethod
    def _free_bytes(context: ExecutionContext) -> int:
        return shutil.disk_usage(context.project.root).free

    @staticmethod
    def _probe_sanitize_tables(context: ExecutionContext) -> None:
        """Abort before the dump if a required sanitize table is missing.

        `parse_rules` is the same parser `SanitizeOperation` itself uses, so
        a `requires_table` containing a placeholder (e.g.
        `${WP_TABLE_PREFIX}users`) arrives here already resolved to the real
        name - checked against the real name, not the literal placeholder
        text. The actual table comparison lives in
        `_first_missing_table_problem`, shared with `check()`'s non-raising
        form (`_check_sanitize_tables`) below, so the two can never quietly
        disagree about what counts as missing.
        """
        required = _required_tables(parse_rules(context))
        if not required:
            return
        tables = _remote_tables(context)
        problem = _first_missing_table_problem(context, required, tables)
        if problem is not None:
            raise OperationError(problem)

    @staticmethod
    def _check_sanitize_tables(
        context: ExecutionContext, report: PreflightReport
    ) -> tuple[bool | None, str]:
        """The non-raising form of `_probe_sanitize_tables`, for `doctor`.

        Returns `(None, "")` when there is nothing meaningful to report:
        either connectivity already failed - `ssh_ok`/`remote_path_ok` above
        already cover that, and there is no remote database left to query -
        or no sanitize rule requires a table at all. Otherwise runs the same
        probe `execute()` runs, catching the one `OperationError` that
        `_remote_tables` can raise (the table *listing* itself failing, e.g.
        bad credentials) instead of letting it propagate, because `check()`
        is documented to never raise.
        """
        if not (report.ssh_ok and report.remote_path_ok):
            return None, ""
        required = _required_tables(parse_rules(context))
        if not required:
            return None, ""
        try:
            tables = _remote_tables(context)
        except OperationError as error:
            return False, str(error)
        problem = _first_missing_table_problem(context, required, tables)
        if problem is not None:
            return False, problem
        plural = "table" if len(required) == 1 else "tables"
        return True, f"{len(required)} required {plural} present"


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
        return (
            f"sanitize rule {rule_name!r} requires table {table!r}, which does "
            f"not exist on {context.target.host}'s database "
            f"({len(tables)} table(s) found). Nothing was downloaded or "
            "imported. Either this project's table is named differently - "
            "override it in .ddev/ddev-sync.yaml - or, if the table is "
            "genuinely optional for this project, add "
            "'tolerate_missing_table: true' to the rule."
        )
    return None


def _remote_tables(context: ExecutionContext) -> set[str]:
    credentials = read_remote_credentials(context)
    result = context.runner.run(ssh_command(context.target, _show_tables_argv(credentials)))
    if not result.ok:
        raise OperationError(
            "could not list tables on the remote database to verify sanitize rules "
            f"before the dump: {_redact(result.stderr, credentials.password).strip()}"
        )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _show_tables_argv(credentials: RemoteCredentials) -> list[str]:
    return [
        "mysql",
        f"--user={credentials.user}",
        f"--password={credentials.password}",
        f"--host={credentials.host}",
        f"--port={credentials.port}",
        "--batch",
        "--skip-column-names",
        "--execute",
        "SHOW TABLES",
        credentials.name,
    ]


def _redact(text: str, secret: str) -> str:
    """Remove `secret` from `text`; a no-op for an empty secret (see the
    identical guard and reasoning in operations/database.py's `_scrub`).
    """
    return text.replace(secret, "<REDACTED>") if secret else text
