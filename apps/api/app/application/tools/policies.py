"""The policy tools offered to the model.

Four tools, all read-only and user-scoped. Their shape is the security design:

* no arguments name a user, a tenant, a query or a URL;
* identity is not an argument at all — the executor passes ``AuthContext`` to the
  handler, so the model cannot ask about someone else;
* every result is projected to fields a customer may see (no national id, no address).

Scope note: ``required_scopes`` is empty because every authenticated user may read
*their own* policies, and ownership is enforced by the data source. The registry and
executor fully support scopes; they become meaningful when a role reads other people's
data (an agent looking up a customer), which is a later phase with an actual role
model rather than an invented one.
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

    ``extra="forbid"`` is the point: attempting ``get_my_policy_count(user_id=...)``
    fails validation instead of being quietly ignored.
    """

    model_config = ConfigDict(extra="forbid")


class PolicyIdArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(
        min_length=4,
        max_length=64,
        pattern=r"^[A-Za-z0-9-]+$",
        description="Policy identifier, e.g. POL-1A2B3C-1.",
    )


# -- result models ---------------------------------------------------------
class PolicyCountResult(BaseModel):
    count: int
    active_count: int


class PolicyStatusResult(BaseModel):
    policy_id: str
    status: str


class PolicySummary(BaseModel):
    policy_id: str
    type: str
    status: str
    insurer: str
    start_date: str
    end_date: str
    premium_toman: int


class PolicyListResult(BaseModel):
    policies: list[PolicySummary]


def _unwrap(result: ConnectorResult) -> Any:
    """Turn a connector result into tool data, or refuse with the right status."""
    if result.ok:
        return result.data
    raise ToolRefusal(
        _CONNECTOR_STATUS_TO_TOOL_STATUS.get(result.status, ToolStatus.UPSTREAM_ERROR),
        result.message or "The data source could not answer.",
    )


def build_policy_registry(connector: DataConnectorPort) -> ToolRegistry:
    """Register the policy tools against a connector adapter."""

    async def count_policies(context: AuthContext, _: NoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="count_policies"), context))

    async def list_policies(context: AuthContext, _: NoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="list_policies"), context))

    async def policy_details(context: AuthContext, arguments: PolicyIdArguments) -> Any:
        return _unwrap(
            await connector.fetch(
                FetchSpec(operation="policy_by_id", params={"policy_id": arguments.policy_id}),
                context,
            )
        )

    async def policy_status(context: AuthContext, arguments: PolicyIdArguments) -> dict[str, Any]:
        policy = _unwrap(
            await connector.fetch(
                FetchSpec(operation="policy_by_id", params={"policy_id": arguments.policy_id}),
                context,
            )
        )
        status = policy.get("status") if isinstance(policy, dict) else None
        if not isinstance(status, str):
            raise ToolRefusal(ToolStatus.INVALID_RESPONSE, "The data source omitted a status.")
        return {"policy_id": arguments.policy_id, "status": status}

    return ToolRegistry(
        [
            ToolDefinition(
                name="get_my_policy_count",
                description=(
                    "Count the insurance policies belonging to the authenticated user. "
                    "Use for any question about how many policies they have, including "
                    "how many are active. Takes no arguments."
                ),
                arguments_model=NoArguments,
                handler=count_policies,
                result_model=PolicyCountResult,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="get_my_policies",
                description=(
                    "List the authenticated user's policies with type, status, insurer and "
                    "date range. Use when they ask what policies they have."
                ),
                arguments_model=NoArguments,
                handler=list_policies,
                result_model=PolicyListResult,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="get_policy_details",
                description=(
                    "Return full details of one of the user's own policies, by policy id. "
                    "Use when they name a specific policy."
                ),
                arguments_model=PolicyIdArguments,
                handler=policy_details,
                result_model=PolicySummary,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="get_policy_status",
                description=(
                    "Return just the status (active/expired) of one of the user's own "
                    "policies, by policy id."
                ),
                arguments_model=PolicyIdArguments,
                handler=policy_status,
                result_model=PolicyStatusResult,
                timeout_seconds=8.0,
            ),
        ]
    )
