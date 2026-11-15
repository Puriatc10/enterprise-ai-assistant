"""The capability tool: what this assistant can actually reach.

Written as a tool rather than a system-prompt sentence because a prompt claim is not
verifiable, drifts as domains are enabled or disabled, and cannot be checked by a test.
This answers from the same descriptors that built the registry, so it cannot describe a
domain that is not there.

Scope note: every authenticated user sees the same catalogue today. When a role can read
other customers' data, this tool will filter by scope and become the honest answer to
"what may *you* query".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.application.tools.registry import ToolDefinition, ToolRegistry
from app.domain.models.auth import AuthContext
from app.domain.models.sources import DomainDescriptor


class NoArguments(BaseModel):
    """Takes nothing: identity alone decides what is listed."""

    model_config = ConfigDict(extra="forbid")


class SourceSummary(BaseModel):
    id: str
    label: str
    description: str
    tools: list[str]


class SourceListResult(BaseModel):
    sources: list[SourceSummary]


def build_overview_registry(descriptors: Sequence[DomainDescriptor]) -> ToolRegistry:
    """Register ``list_my_data_sources`` over the enabled domains."""

    async def list_sources(_: AuthContext, __: NoArguments) -> dict[str, Any]:
        return {
            "sources": [
                {
                    "id": descriptor.id,
                    "label": descriptor.label,
                    "description": descriptor.description,
                    "tools": list(descriptor.tools),
                }
                for descriptor in descriptors
            ]
        }

    return ToolRegistry(
        [
            ToolDefinition(
                name="list_my_data_sources",
                description=(
                    "List the data domains this assistant can query for the signed-in "
                    "user, with the tools available in each. Use when asked what you can "
                    "do, which data you can see, or whether a topic is covered at all."
                ),
                arguments_model=NoArguments,
                handler=list_sources,
                result_model=SourceListResult,
                # Cheap and local: no timeout pressure, but still bounded like the rest.
                timeout_seconds=2.0,
            )
        ]
    )
