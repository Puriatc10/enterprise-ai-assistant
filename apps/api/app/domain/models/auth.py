"""Authentication context — the identity every layer must trust.

This model is produced *only* by verifying a token (``app.security``). Anything a
client or a model sends is untrusted input and must never populate it.

Frozen on purpose: a downstream component cannot widen its own scope by mutating
the context it was handed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AuthContext(BaseModel):
    """Verified identity and authorization scope for one request."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1, description="Verified user id (JWT 'sub').")
    tenant_id: str = Field(min_length=1, description="Verified tenant/organization id.")
    roles: tuple[str, ...] = Field(default=(), description="Roles from the verified token.")
    scopes: frozenset[str] = Field(
        default=frozenset(),
        description="Permission strings from the verified token, e.g. 'policies:read'.",
    )

    def has_scopes(self, required: frozenset[str] | set[str] | tuple[str, ...]) -> bool:
        """True when this context holds every required scope."""
        return set(required) <= self.scopes
