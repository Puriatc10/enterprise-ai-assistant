"""Security invariants that Phase 0 can already pin down.

These are cheap tests now and load-bearing later: they assert that authorization
fails *closed* and that the identity model cannot be populated from untrusted
input.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.models.auth import AuthContext
from app.security.auth import require_auth_context


def test_auth_dependency_fails_closed() -> None:
    """No verifier yet => 501, never an implicit anonymous pass-through."""
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(require_auth_context())

    assert excinfo.value.status_code == 501


def test_auth_context_requires_a_subject_and_tenant() -> None:
    with pytest.raises(ValidationError):
        AuthContext(subject="", tenant_id="tenant-1")

    with pytest.raises(ValidationError):
        AuthContext(subject="user-1")  # type: ignore[call-arg]


def test_auth_context_is_immutable() -> None:
    context = AuthContext(subject="user-1", tenant_id="tenant-1", scopes=frozenset({"a"}))

    with pytest.raises(ValidationError):
        context.scopes = frozenset({"a", "admin"})  # type: ignore[misc]


def test_has_scopes_requires_every_scope() -> None:
    context = AuthContext(subject="u", tenant_id="t", scopes=frozenset({"policies:read"}))

    assert context.has_scopes({"policies:read"}) is True
    assert context.has_scopes({"policies:read", "policies:write"}) is False
    assert context.has_scopes(set()) is True
