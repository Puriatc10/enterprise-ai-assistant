"""CRM connector: the caller's accounts and deals, read-only, over PostgreSQL.

RLS does not apply to this connection, the WHERE clause is the control.

Every statement joins ``crm.deals`` to ``crm.accounts`` and filters on
``a.auth_subject = $1``; the account lookup filters on ``a.auth_subject = $1`` alone. The
subject comes from the verified ``AuthContext`` and is bound as a parameter, never
interpolated. There is deliberately no operation that takes an account id, a deal id or a
subject as a parameter, so "another customer's data" is not *denied* — it does not exist: a
list comes back empty and an account comes back ``NOT_FOUND``, which is not an existence
oracle.

Three named operations, nothing else:

* ``list_deals`` — the caller's deals, newest activity first, at most ``row_limit`` of them.
* ``summarize_pipeline`` — count and total of the caller's *open* deals. Aggregates over
  the same scoped join, so the total cannot include another account's deals.
* ``account_by_subject`` — the caller's own account.

The transaction, statement timeout, row limit, identity binding and error mapping come from
``base.PostgresReadOnlyConnector``; this module adds only CRM statements and projection.
"""

from __future__ import annotations

from typing import Any

from app.domain.interfaces.connector import ConnectorResult, ConnectorStatus, FetchSpec
from app.domain.models.crm import (
    CLOSED_STAGES,
    DEFAULT_CURRENCY,
    project_account,
    project_deals,
)
from app.infrastructure.connectors.postgres.base import PostgresReadOnlyConnector

#: Operations this connector answers.
ALLOWED_OPERATIONS = frozenset({"list_deals", "summarize_pipeline", "account_by_subject"})

#: Built from the domain's closed-stage list so a change there reaches the SQL by
#: construction. These are module constants, never caller input.
_CLOSED_STAGE_SQL = ", ".join(f"'{stage}'" for stage in CLOSED_STAGES)

_DEAL_COLUMNS = """
           d.id::text AS deal_id,
           d.title,
           d.stage,
           d.amount_toman,
           d.currency,
           d.expected_close,
           d.updated_at"""

_LIST_DEALS_SQL = f"""
    SELECT {_DEAL_COLUMNS}
      FROM crm.deals d
      JOIN crm.accounts a ON a.id = d.account_id
     WHERE a.auth_subject = $1
     ORDER BY d.updated_at DESC, d.id
     LIMIT $2
"""

_SUMMARIZE_SQL = f"""
    SELECT count(*) AS open_deals,
           coalesce(sum(d.amount_toman), 0) AS total_amount_toman,
           coalesce(min(d.currency), '{DEFAULT_CURRENCY}') AS currency
      FROM crm.deals d
      JOIN crm.accounts a ON a.id = d.account_id
     WHERE a.auth_subject = $1
       AND d.stage NOT IN ({_CLOSED_STAGE_SQL})
"""

# ``auth_subject`` is UNIQUE, so this returns at most one row; LIMIT 1 keeps that true even
# if the constraint is ever relaxed, and keeps the statement bounded like every other one.
_ACCOUNT_SQL = """
    SELECT a.name,
           a.industry,
           a.owner_name,
           a.created_at
      FROM crm.accounts a
     WHERE a.auth_subject = $1
     LIMIT 1
"""


class CrmConnector(PostgresReadOnlyConnector):
    """Read-only CRM data (accounts, deals, pipeline) for one verified subject."""

    name = "postgres-crm"
    allowed_operations = ALLOWED_OPERATIONS

    async def _run(self, connection: Any, spec: FetchSpec, subject: str) -> ConnectorResult:
        if spec.operation == "list_deals":
            rows = await connection.fetch(_LIST_DEALS_SQL, subject, self.row_limit)
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={"deals": project_deals([dict(row) for row in rows])},
            )

        if spec.operation == "summarize_pipeline":
            row = await connection.fetchrow(_SUMMARIZE_SQL, subject)
            if row is None:
                # An aggregate without GROUP BY always returns a row; a missing one is a
                # driver/statement problem, not "no data".
                return ConnectorResult(status=ConnectorStatus.UPSTREAM_ERROR, message="No result.")
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={
                    "open_deals": int(row["open_deals"] or 0),
                    "total_amount_toman": int(row["total_amount_toman"] or 0),
                    "currency": str(row["currency"] or DEFAULT_CURRENCY),
                },
            )

        # account_by_subject — the only remaining operation in the allowlist.
        row = await connection.fetchrow(_ACCOUNT_SQL, subject)
        if row is None:
            # Scoped by auth_subject, so an account that belongs to someone else looks
            # exactly like one that does not exist. No existence oracle.
            return ConnectorResult(
                status=ConnectorStatus.NOT_FOUND, message="No account for this user."
            )
        return ConnectorResult(status=ConnectorStatus.OK, data=project_account(dict(row)))
