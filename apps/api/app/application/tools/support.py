"""The support-ticket tools offered to the model.

Three tools, all read-only and user-scoped. Their shape is the security design:

* no argument names a customer, a tenant, a query or a database path;
* identity is not an argument at all — the executor passes ``AuthContext`` to the
  handler, so the model cannot ask about someone else's tickets;
* every result is projected to the ticket fields a customer may see (no
  ``customer_ref``, no internal columns).

``required_scopes`` is empty for the same reason as the policy tools: every
authenticated user may read *their own* tickets, and ownership is enforced by the
connector. A support agent reading a customer's tickets is a different tool with an
actual role behind it, which this phase does not have.

The descriptions are the routing mechanism. The model picks a tool by reading them, so
each one states *when* to use it, not only what it returns.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.application.tools.executor import ToolRefusal
from app.application.tools.registry import ToolDefinition, ToolRegistry
from app.domain.interfaces.connector import (
    ConnectorResult,
    ConnectorStatus,
    DataConnectorPort,
    FetchSpec,
)
from app.domain.models.auth import AuthContext
from app.domain.models.tools import ToolStatus

_CONNECTOR_STATUS_TO_TOOL_STATUS: dict[ConnectorStatus, ToolStatus] = {
    ConnectorStatus.NOT_FOUND: ToolStatus.NOT_FOUND,
    ConnectorStatus.UNAUTHORIZED: ToolStatus.FORBIDDEN,
    ConnectorStatus.TIMEOUT: ToolStatus.TIMEOUT,
    ConnectorStatus.INVALID_RESPONSE: ToolStatus.INVALID_RESPONSE,
    ConnectorStatus.UPSTREAM_ERROR: ToolStatus.UPSTREAM_ERROR,
}


# -- argument models -------------------------------------------------------
class NoArguments(BaseModel):
    """A tool that takes nothing.

    ``extra="forbid"`` is the point: attempting
    ``support_list_my_tickets(customer_ref="bob-id")`` fails validation instead of being
    quietly ignored. Declared here rather than imported from the policy domain so this
    module stays usable on its own; the semantics are identical on purpose.
    """

    model_config = ConfigDict(extra="forbid")


class SupportTicketIdArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(
        min_length=4,
        max_length=64,
        pattern=r"^[A-Za-z0-9-]+$",
        description="Ticket identifier, e.g. TKT-1042.",
    )


# -- result models ---------------------------------------------------------
class SupportTicketCountResult(BaseModel):
    count: int
    open_count: int


class SupportTicketSummary(BaseModel):
    """One row of a ticket list. ``subject`` is the ticket's title, not the caller."""

    ticket_id: str
    subject: str
    status: str
    priority: str
    created_at: str
    updated_at: str


class SupportTicketListResult(BaseModel):
    tickets: list[SupportTicketSummary]


class SupportTicketDetail(SupportTicketSummary):
    description: str


def _unwrap(result: ConnectorResult) -> Any:
    """Turn a connector result into tool data, or refuse with the right status."""
    if result.ok:
        return result.data
    raise ToolRefusal(
        _CONNECTOR_STATUS_TO_TOOL_STATUS.get(result.status, ToolStatus.UPSTREAM_ERROR),
        result.message or "The data source could not answer.",
    )


def build_support_registry(connector: DataConnectorPort) -> ToolRegistry:
    """Register the support tools against a connector adapter.

    The connector is injected, never constructed here: the tool layer must not know
    whether the tickets come from SQLite, a helpdesk API or the mock, and tests thread a
    temporary database through the same path production uses.
    """

    async def count_tickets(context: AuthContext, _: NoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="count_tickets"), context))

    async def list_tickets(context: AuthContext, _: NoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="list_tickets"), context))

    async def ticket_details(context: AuthContext, arguments: SupportTicketIdArguments) -> Any:
        return _unwrap(
            await connector.fetch(
                FetchSpec(operation="ticket_by_id", params={"ticket_id": arguments.ticket_id}),
                context,
            )
        )

    return ToolRegistry(
        [
            ToolDefinition(
                name="support_count_my_tickets",
                description=(
                    "Count the support tickets belonging to the authenticated user, "
                    "split into all tickets and those still open. Use for any question "
                    "about how many tickets they have open, or how many they have "
                    "raised in total. Takes no arguments."
                ),
                arguments_model=NoArguments,
                handler=count_tickets,
                result_model=SupportTicketCountResult,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="support_list_my_tickets",
                description=(
                    "List the authenticated user's support tickets with subject, "
                    "status, priority and when each was created or last updated. Use "
                    "when they ask what tickets they have, which one is still open, or "
                    "what is waiting on support. Takes no arguments; it never returns "
                    "another user's tickets."
                ),
                arguments_model=NoArguments,
                handler=list_tickets,
                result_model=SupportTicketListResult,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="support_get_ticket",
                description=(
                    "Return one of the authenticated user's own support tickets in "
                    "full, including the description they wrote, by ticket id. Use when "
                    "they name or reference a specific ticket, or ask what a ticket "
                    "says. The id must be one they already saw in a list."
                ),
                arguments_model=SupportTicketIdArguments,
                handler=ticket_details,
                result_model=SupportTicketDetail,
                timeout_seconds=8.0,
            ),
        ]
    )
