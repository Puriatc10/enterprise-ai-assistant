"""The data-connector port.

Each organizational system (Didar24, a PostgreSQL database, a third-party REST
API, the synthetic mock) is an adapter behind this protocol. The orchestrator
never sees a vendor payload: adapters normalize into ``ConnectorResult``.

Security contract every adapter must honour (docs/architecture/SECURITY.md §4):

* credentials are read-only and least-privilege;
* operations are an allowlist, never a caller-supplied query or URL;
* the scope actually queried is re-derived from ``AuthContext``, so a caller
  cannot reach another user's or tenant's data;
* every call is bounded by a timeout and a row/response limit;
* failures map onto ``ConnectorStatus``; raw upstream errors do not propagate.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.domain.models.auth import AuthContext


class ConnectorStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"


class FetchSpec(BaseModel):
    """A named, allowlisted operation. Never raw SQL and never a free-form URL."""

    operation: str = Field(description="Allowlisted operation name, e.g. 'policy_count'.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Operation parameters. Adapters validate these against their own schema.",
    )


class ConnectorResult(BaseModel):
    """Normalized connector output, in application terms."""

    status: ConnectorStatus
    data: Any | None = None
    message: str | None = Field(
        default=None, description="Sanitized note; never a raw upstream error body."
    )

    @property
    def ok(self) -> bool:
        return self.status is ConnectorStatus.OK


class ConnectorHealth(BaseModel):
    """Whether a connector is usable right now (used by readiness checks)."""

    reachable: bool
    detail: str | None = None


@runtime_checkable
class DataConnectorPort(Protocol):
    """Read-only access to one data source, scoped to the caller."""

    name: str

    async def health(self) -> ConnectorHealth:
        """Cheap reachability/credential check. Must not leak secrets."""
        ...

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        """Execute one allowlisted read, restricted to ``context``'s scope.

        Implementations must never accept a subject or tenant id from ``spec``;
        it comes from ``context`` alone.

        Raises:
            Nothing. Failures are returned as a ``ConnectorResult`` with a
            non-``ok`` status so the orchestrator can explain them truthfully.
        """
        ...
