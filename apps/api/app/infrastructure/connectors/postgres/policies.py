"""PostgreSQL connector: read-only, named operations, bounded.

Three controls are enforced *in code*, not merely documented:

1. **Named operations only.** ``list_policies`` / ``count_policies`` / ``policy_by_id``.
   There is no ``execute_sql`` and no way to pass SQL text through ``FetchSpec`` — the
   operation name selects a constant statement.
2. **Every query runs in a READ ONLY transaction** with a statement timeout. If the
   role is ever over-privileged by mistake, the transaction refuses the write anyway,
   and a runaway query cannot pin a connection.
3. **A row limit is always present**, server-side, in the SQL itself.

Identity: the caller's verified ``AuthContext.subject`` is bound as ``$1``. It is never
taken from ``spec.params``, so a model cannot ask about another customer. In production
the subject→customer mapping belongs in a mapping table; here the demo seed stores the
subject directly in ``customer_ref``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.interfaces.connector import (
    ConnectorHealth,
    ConnectorResult,
    ConnectorStatus,
    FetchSpec,
)
from app.domain.models.auth import AuthContext
from app.domain.models.policies import project_policies, project_policy

logger = logging.getLogger(__name__)

#: Operations this connector answers.
ALLOWED_OPERATIONS = frozenset({"list_policies", "count_policies", "policy_by_id"})

#: Hard ceiling regardless of what the caller asks for.
MAX_ROW_LIMIT = 100

_COLUMNS = "policy_id, type, status, insurer, start_date, end_date, premium_toman"

_LIST_SQL = f"""
    SELECT {_COLUMNS}
      FROM connector_policies
     WHERE customer_ref = $1
     ORDER BY start_date DESC, policy_id
     LIMIT $2
"""

_COUNT_SQL = """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'فعال') AS active
      FROM connector_policies
     WHERE customer_ref = $1
"""

_ONE_SQL = f"""
    SELECT {_COLUMNS}
      FROM connector_policies
     WHERE customer_ref = $1
       AND policy_id = $2
"""


class PostgresPolicyConnector:
    """Read-only policy data from a PostgreSQL database.

    ``pool_factory`` is injected so unit tests can drive the connector without a
    server; the CI integration job exercises the real ``asyncpg`` pool.
    """

    name = "postgres-policies"

    def __init__(
        self,
        *,
        dsn: str,
        row_limit: int = 50,
        statement_timeout_ms: int = 5_000,
        pool_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        if row_limit <= 0 or row_limit > MAX_ROW_LIMIT:
            msg = f"row_limit must be between 1 and {MAX_ROW_LIMIT}"
            raise ValueError(msg)
        if statement_timeout_ms <= 0:
            msg = "statement_timeout_ms must be positive"
            raise ValueError(msg)

        self._dsn = dsn
        self._row_limit = row_limit
        self._statement_timeout_ms = statement_timeout_ms
        self._pool_factory = pool_factory or self._default_pool_factory
        self._pool: Any | None = None

    async def _default_pool_factory(self) -> Any:
        import asyncpg  # imported lazily: the [postgres] extra is optional

        return await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=5)

    async def _get_pool(self) -> Any:
        if self._pool is None:
            self._pool = await self._pool_factory()
        return self._pool

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def health(self) -> ConnectorHealth:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as connection:
                await connection.fetchval("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - health must answer, not raise
            logger.warning("postgres connector unreachable: %s", type(exc).__name__)
            return ConnectorHealth(reachable=False, detail="database unreachable")
        return ConnectorHealth(reachable=True, detail="read-only connection ok")

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        if spec.operation not in ALLOWED_OPERATIONS:
            logger.warning("postgres connector refused operation=%r", spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE,
                message="Unsupported operation.",
            )

        subject = context.subject
        if not subject:
            # Defensive: an empty subject would match nothing, but a missing identity
            # must never be treated as "show everything".
            return ConnectorResult(
                status=ConnectorStatus.UNAUTHORIZED, message="Missing caller identity."
            )

        try:
            pool = await self._get_pool()
            # READ ONLY + statement timeout, both scoped to this transaction.
            async with (
                pool.acquire() as connection,
                connection.transaction(readonly=True),
            ):
                await connection.execute(
                    f"SET LOCAL statement_timeout = {self._statement_timeout_ms}"
                )
                return await self._run(connection, spec, subject)
        except TimeoutError:
            return ConnectorResult(
                status=ConnectorStatus.TIMEOUT, message="The database did not respond in time."
            )
        except Exception as exc:  # noqa: BLE001 - a connector never crashes the turn
            if type(exc).__name__ == "QueryCanceledError":
                return ConnectorResult(
                    status=ConnectorStatus.TIMEOUT,
                    message="The database did not respond in time.",
                )
            logger.exception("postgres connector failed operation=%s", spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR, message="The database query failed."
            )

    async def _run(self, connection: Any, spec: FetchSpec, subject: str) -> ConnectorResult:
        if spec.operation == "count_policies":
            row = await connection.fetchrow(_COUNT_SQL, subject)
            if row is None:
                return ConnectorResult(status=ConnectorStatus.UPSTREAM_ERROR, message="No result.")
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={"count": int(row["total"]), "active_count": int(row["active"])},
            )

        if spec.operation == "list_policies":
            rows = await connection.fetch(_LIST_SQL, subject, self._row_limit)
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={"policies": project_policies([dict(row) for row in rows])},
            )

        # policy_by_id — the same statement for details and status lookups.
        policy_id = spec.params.get("policy_id")
        if not isinstance(policy_id, str) or not policy_id.strip():
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE, message="policy_id is required."
            )

        row = await connection.fetchrow(_ONE_SQL, subject, policy_id.strip())
        if row is None:
            # Scoped by customer_ref, so another customer's id looks exactly like a
            # nonexistent one — which is the point: no existence oracle.
            return ConnectorResult(
                status=ConnectorStatus.NOT_FOUND, message="No such policy for this user."
            )

        return ConnectorResult(status=ConnectorStatus.OK, data=project_policy(dict(row)))
