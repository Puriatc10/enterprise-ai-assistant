"""The CRM tools offered to the model: deals, pipeline and the caller's account.

Three tools, all read-only and self-scoped. Their shape is the security design:

* **No arguments name anything.** There is no account id, no deal id, no user id and no
  tenant: a CRM question is always about *the caller*, so the argument models are empty
  and ``extra="forbid"``, which turns ``crm_list_my_deals(user_id="someone")`` into a
  validation error instead of a silently ignored field.
* **Identity is not an argument at all.** The executor passes ``AuthContext`` to the
  handler; the connector binds ``context.subject`` as ``$1``. There is no code path where
  model output could name a different customer.
* **Results are projected** to the public CRM fields (no internal ids, no ``auth_subject``).

Scope note: ``required_scopes`` stays empty for the same reason as the policy tools —
every authenticated user may read *their own* CRM data, and ownership is enforced by the
data source. Scopes become meaningful for a role that reads other people's data (an account
manager looking up a customer), which needs an actual role model rather than an invented one.
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


# -- argument model --------------------------------------------------------
class CrmNoArguments(BaseModel):
    """A CRM tool that takes nothing.

    ``extra="forbid"`` is the security property: attempting to pass an identity-shaped
    field fails validation rather than being quietly dropped.
    """

    model_config = ConfigDict(extra="forbid")


# -- result models ---------------------------------------------------------
class DealSummary(BaseModel):
    deal_id: str
    title: str
    stage: str
    amount_toman: int
    currency: str
    # Optional because ``expected_close`` is free text in the source and a row may legitimately
    # lack it; the projection omits a missing field rather than filling it with null.
    expected_close: str | None = None
    updated_at: str | None = None


class DealListResult(BaseModel):
    deals: list[DealSummary]


class PipelineSummary(BaseModel):
    open_deals: int
    total_amount_toman: int = Field(description="Sum of open deal amounts, in toman.")
    currency: str


class AccountSummary(BaseModel):
    name: str
    industry: str
    owner_name: str
    created_at: str | None = None


def _unwrap(result: ConnectorResult) -> Any:
    """Turn a connector result into tool data, or refuse with the right status.

    The same mapping as the policy tools, kept local so two domains do not import each
    other's modules.
    """
    if result.ok:
        return result.data
    raise ToolRefusal(
        _CONNECTOR_STATUS_TO_TOOL_STATUS.get(result.status, ToolStatus.UPSTREAM_ERROR),
        result.message or "The data source could not answer.",
    )


def build_crm_registry(connector: DataConnectorPort) -> ToolRegistry:
    """Register the CRM tools against a connector adapter."""

    async def list_my_deals(context: AuthContext, _: CrmNoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="list_deals"), context))

    async def summarize_my_pipeline(context: AuthContext, _: CrmNoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="summarize_pipeline"), context))

    async def get_account(context: AuthContext, _: CrmNoArguments) -> Any:
        return _unwrap(await connector.fetch(FetchSpec(operation="account_by_subject"), context))

    return ToolRegistry(
        [
            ToolDefinition(
                name="crm_list_my_deals",
                description=(
                    "List the deals (sales opportunities) belonging to the authenticated "
                    "user's company account: title, stage, amount in toman, currency and "
                    "expected close date. Use when they ask what deals they have or what is "
                    "in their pipeline. Takes no arguments."
                ),
                arguments_model=CrmNoArguments,
                handler=list_my_deals,
                result_model=DealListResult,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="crm_summarize_my_pipeline",
                description=(
                    "Summarize the authenticated user's open pipeline: how many deals are "
                    "still open, their total value in toman and the currency. Deals that are "
                    "already won or lost are excluded. Use for 'how much is in my pipeline'. "
                    "Takes no arguments."
                ),
                arguments_model=CrmNoArguments,
                handler=summarize_my_pipeline,
                result_model=PipelineSummary,
                timeout_seconds=8.0,
            ),
            ToolDefinition(
                name="crm_get_account",
                description=(
                    "Return the authenticated user's own company account: name, industry, "
                    "account owner and the date the account was created. Use when they ask "
                    "which company or account they are on. Takes no arguments."
                ),
                arguments_model=CrmNoArguments,
                handler=get_account,
                result_model=AccountSummary,
                timeout_seconds=8.0,
            ),
        ]
    )
