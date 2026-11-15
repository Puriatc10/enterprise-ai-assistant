"""SQLite connector (read-only, file-backed).

The reason this adapter exists next to the mock is that a real engine proves the
abstraction: real schemas, real query planning, real failure modes — and no credentials,
no network, no service to stand up.

Three controls are enforced in code, not merely documented:

1. **Read-only at the engine.** The database is opened with ``?mode=ro``, so SQLite
   refuses a write at the file level ("attempt to write a readonly database"). If a
   future statement in this module were wrong, it would still be unable to mutate the
   demo data.
2. **Named operations only.** ``list_tickets`` / ``count_tickets`` / ``ticket_by_id``.
   SQL text never travels through ``FetchSpec``; the operation name selects a constant
   statement.
3. **Bounded everywhere.** Every list query carries a ``LIMIT`` fixed at construction,
   and every statement is aborted by a progress handler after ``timeout_ms``.

Identity comes from the verified ``AuthContext.subject`` bound as a query parameter,
never from ``spec.params``. Because the ``WHERE`` clause is scoped by ``customer_ref``,
another customer's ticket id returns exactly what a nonexistent id returns, so the
connector is not an existence oracle.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.domain.interfaces.connector import (
    ConnectorHealth,
    ConnectorResult,
    ConnectorStatus,
    FetchSpec,
)
from app.domain.models.auth import AuthContext
from app.domain.models.support import (
    STATUS_OPEN,
    project_ticket_detail,
    project_tickets,
)

logger = logging.getLogger(__name__)

#: Operations this connector answers. Anything else is refused rather than guessed.
ALLOWED_OPERATIONS = frozenset({"list_tickets", "count_tickets", "ticket_by_id"})

#: Hard ceiling regardless of what the caller asks for.
MAX_ROW_LIMIT = 100

#: How often (in VM instructions) the progress handler checks the deadline. Small
#: enough that a runaway scan is stopped promptly, large enough not to dominate a
#: query that finishes in microseconds.
_PROGRESS_HANDLER_INSTRUCTIONS = 1_000

_TICKET_COLUMNS = "ticket_id, subject, status, priority, created_at, updated_at"

_LIST_SQL = f"""
    SELECT {_TICKET_COLUMNS}
      FROM support_tickets
     WHERE customer_ref = ?
     ORDER BY updated_at DESC, ticket_id
     LIMIT ?
"""

# The status literal is a module constant, not caller input — the only value ever
# interpolated into a statement in this file.
_COUNT_SQL = f"""
    SELECT count(*) AS total,
           sum(CASE WHEN status = '{STATUS_OPEN}' THEN 1 ELSE 0 END) AS open_total
      FROM support_tickets
     WHERE customer_ref = ?
"""

_ONE_SQL = f"""
    SELECT {_TICKET_COLUMNS}, description
      FROM support_tickets
     WHERE customer_ref = ?
       AND ticket_id = ?
"""

_HEALTH_SQL = "SELECT count(*) AS total FROM support_tickets"


class _DatabaseMissingError(Exception):
    """The configured file is not there. Distinct because it is an operator error
    (a bad path or an unseeded data directory), not a query that went wrong."""


def connect_read_only(path: str, *, timeout_ms: int = 4_000) -> sqlite3.Connection:
    """Open ``path`` read-only, with a wall-clock bound on any statement.

    This is the only place the adapter opens the database, so the read-only guarantee
    has exactly one implementation to audit. ``mode=ro`` makes writes impossible at the
    engine; the timeout below makes a slow statement survivable.

    Why a progress handler rather than ``asyncio.wait_for``: a thread inside
    ``sqlite3`` cannot be cancelled. Wrapping the call in a timeout would return to the
    caller while the statement kept scanning, so the bound has to live inside SQLite —
    the handler returning non-zero aborts the running statement with
    ``OperationalError("interrupted")``.
    """
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")

    # A URI path needs forward slashes and the file_uri flag; the query string is
    # appended after the path, so a path containing a literal '?' is out of scope.
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=timeout_ms / 1_000)
    connection.row_factory = sqlite3.Row

    deadline = time.monotonic() + timeout_ms / 1_000
    connection.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0,
        _PROGRESS_HANDLER_INSTRUCTIONS,
    )
    return connection


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """``sqlite3.Row`` is not a ``Mapping``: ``dict(row)`` fails and ``in`` tests values,
    not keys, so the keys are paired with the row explicitly before projecting."""
    return dict(zip(row.keys(), row, strict=True))


class SqliteSupportConnector:
    """Read-only support tickets from a local SQLite file.

    ``path`` is resolved once at construction; every call opens its own short-lived
    read-only connection. Connections are cheap in SQLite and a per-call handle keeps
    the adapter safe to call from several ``asyncio`` threads at once.
    """

    name: str = "sqlite-support"

    def __init__(self, *, path: str, row_limit: int = 50, timeout_ms: int = 4_000) -> None:
        if row_limit <= 0 or row_limit > MAX_ROW_LIMIT:
            msg = f"row_limit must be between 1 and {MAX_ROW_LIMIT}"
            raise ValueError(msg)
        if timeout_ms <= 0:
            msg = "timeout_ms must be positive"
            raise ValueError(msg)

        self._path = Path(path)
        self._row_limit = row_limit
        self._timeout_ms = timeout_ms

    async def health(self) -> ConnectorHealth:
        """Report whether the file is present and readable. Never raises, never leaks
        the path — a readiness endpoint should not describe the filesystem."""
        try:
            total = await asyncio.to_thread(self._probe)
        except _DatabaseMissingError:
            logger.warning("sqlite support database file is missing")
            return ConnectorHealth(reachable=False, detail="support database file is missing")
        except (sqlite3.Error, OSError) as exc:
            logger.warning("sqlite support database unavailable: %s", type(exc).__name__)
            return ConnectorHealth(reachable=False, detail="support database unavailable")
        return ConnectorHealth(reachable=True, detail=f"{total} ticket(s) seeded")

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        if spec.operation not in ALLOWED_OPERATIONS:
            logger.warning("sqlite connector refused operation=%r", spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE,
                message="Unsupported operation.",
            )

        # ``AuthContext`` forbids an empty subject, so this stubs the shape only as a
        # second line of defence: an anonymous read must never mean "show everything".
        subject = getattr(context, "subject", "") or ""
        if not subject.strip():
            return ConnectorResult(
                status=ConnectorStatus.UNAUTHORIZED, message="Missing caller identity."
            )

        ticket_id: str | None = None
        if spec.operation == "ticket_by_id":
            candidate = spec.params.get("ticket_id")
            if not isinstance(candidate, str) or not candidate.strip():
                return ConnectorResult(
                    status=ConnectorStatus.INVALID_RESPONSE, message="ticket_id is required."
                )
            ticket_id = candidate.strip()

        try:
            # Blocking work goes to a thread: a table scan must not stall the event loop.
            return await asyncio.to_thread(self._read, spec.operation, subject, ticket_id)
        except _DatabaseMissingError:
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR,
                message="The support database file is missing.",
            )
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                return ConnectorResult(
                    status=ConnectorStatus.TIMEOUT,
                    message="The support database did not answer in time.",
                )
            logger.warning("sqlite support query failed: %s", type(exc).__name__)
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR,
                message="The support database query failed.",
            )
        except (sqlite3.Error, OSError) as exc:
            logger.warning("sqlite support read failed: %s", type(exc).__name__)
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR,
                message="The support database query failed.",
            )

    # -- internals ---------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        if not self._path.is_file():
            raise _DatabaseMissingError
        return connect_read_only(str(self._path), timeout_ms=self._timeout_ms)

    def _probe(self) -> int:
        connection = self._open()
        try:
            row = connection.execute(_HEALTH_SQL).fetchone()
        finally:
            connection.close()
        return int(row["total"]) if row is not None else 0

    def _read(self, operation: str, subject: str, ticket_id: str | None) -> ConnectorResult:
        """Run one allowlisted statement on a fresh connection. Blocking by design."""
        connection = self._open()
        try:
            if operation == "count_tickets":
                row = connection.execute(_COUNT_SQL, (subject,)).fetchone()
                if row is None:  # pragma: no cover - count(*) always returns a row
                    return ConnectorResult(
                        status=ConnectorStatus.UPSTREAM_ERROR, message="No result."
                    )
                return ConnectorResult(
                    status=ConnectorStatus.OK,
                    data={
                        "count": int(row["total"]),
                        # sum() over zero rows is NULL, so coalesce before int().
                        "open_count": int(row["open_total"] or 0),
                    },
                )

            if operation == "list_tickets":
                rows = connection.execute(_LIST_SQL, (subject, self._row_limit)).fetchall()
                return ConnectorResult(
                    status=ConnectorStatus.OK,
                    data={"tickets": project_tickets(_row_to_dict(row) for row in rows)},
                )

            if ticket_id is None:
                return ConnectorResult(
                    status=ConnectorStatus.INVALID_RESPONSE, message="ticket_id is required."
                )

            row = connection.execute(_ONE_SQL, (subject, ticket_id)).fetchone()
            if row is None:
                return ConnectorResult(
                    status=ConnectorStatus.NOT_FOUND, message="No such ticket for this user."
                )
            return ConnectorResult(
                status=ConnectorStatus.OK, data=project_ticket_detail(_row_to_dict(row))
            )
        finally:
            connection.close()
