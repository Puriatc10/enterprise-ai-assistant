"""Tool-calling models: what the model may ask for, and what it gets back.

The distinction that matters here: a ``ToolCall`` is a *proposal* from an
untrusted source. It carries no identity, no credentials and no permission — the
executor supplies those from the verified ``AuthContext``. A ``ToolSpec`` is a
*declaration* by the application, including the scopes it needs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """A tool the application offers to the model."""

    name: str = Field(description="Stable tool name, e.g. 'get_my_policy_count'.")
    description: str = Field(description="When to use it, in the model's terms.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the arguments (derived from a Pydantic model).",
    )
    required_scopes: frozenset[str] = Field(
        default=frozenset(),
        description="Scopes the caller must hold before this tool may run.",
    )
    timeout_seconds: float = Field(default=10.0, gt=0)


class ToolCall(BaseModel):
    """A tool invocation proposed by the model. Untrusted until validated."""

    id: str = Field(description="Provider-supplied call id, echoed back with the result.")
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolStatus(StrEnum):
    """Outcome taxonomy. Errors are facts the model may explain, not exceptions."""

    OK = "ok"
    INVALID_REQUEST = "invalid_request"  # schema/name/identity violation
    FORBIDDEN = "forbidden"  # authenticated but not authorized
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"


class ToolResult(BaseModel):
    """What the executor returns for one call. Never raw provider output."""

    call_id: str
    tool: str
    status: ToolStatus
    data: Any | None = Field(default=None, description="Validated payload when status is ok.")
    message: str | None = Field(
        default=None,
        description="Sanitized, human-readable note. No stack traces, no internals.",
    )

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.OK
