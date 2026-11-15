"""PostgreSQL connector: read-only, named operations, bounded.

Four controls are enforced *in code*, not merely documented:

1. **Named operations only.** ``list_policies`` / ``count_policies`` / ``policy_by_id``.
   There is no ``execute_sql`` and no way to pass SQL text through ``FetchSpec`` — the
   operation name selects a constant statement.
2. **Every query runs in a READ ONLY transaction** with a statement timeout. If the
   role is ever over-privileged by mistake, the transaction refuses the write anyway,
   and a runaway query cannot pin a connection.
3. **A row limit is always present**, server-side, in the SQL itself.
4. **Identity is bound from the context.** The caller's verified ``AuthContext.subject``
   is ``$1``; it is never taken from ``spec.params``, so a model cannot ask about another
   customer. In production the subject→customer mapping belongs in a mapping table; here
   the demo seed stores the subject directly in ``customer_ref``.

Controls 1-4 live in ``base.PostgresReadOnlyConnector`` and are shared with the CRM
connector. This module contributes only the policy statements and the projection, so the
controls have one implementation rather than one per domain.
"""

from __future__ import annotations

from typing import Any

from app.domain.interfaces.connector import ConnectorResult, ConnectorStatus, FetchSpec
from app.domain.models.policies import project_policies, project_policy
from app.infrastructure.connectors.postgres.base import PostgresReadOnlyConnector

#: Operations this connector answers.
ALLOWED_OPERATIONS = frozenset({"list_policies", "count_policies", "policy_by_id"})

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


class PostgresPolicyConnector(PostgresReadOnlyConnector):
    """Read-only policy data from a PostgreSQL database.

    ``pool_factory`` is injected so unit tests can drive the connector without a
    server; the CI integration job exercises the real ``asyncpg`` pool.
    """

    name = "postgres-policies"
    allowed_operations = ALLOWED_OPERATIONS

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
            rows = await connection.fetch(_LIST_SQL, subject, self.row_limit)
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={"policies": project_policies([dict(row) for row in rows])},
            )

        # policy_by_id \u2014 the same statement for details and status lookups.
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
