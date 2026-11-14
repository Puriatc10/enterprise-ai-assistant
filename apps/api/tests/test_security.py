"""Security invariants that Phase 0 can already pin down.

These are cheap tests now and load-bearing later: they assert that authorization
fails *closed* and that the identity model cannot be populated from untrusted
input.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.domain.models.auth import AuthContext
from app.security.auth import require_auth_context


def _request(app: FastAPI, headers: dict[str, str] | None = None) -> Request:
    """Build a Request with a real ``app`` in scope, so ``app.state`` works."""
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": raw_headers, "app": app}
    )


def test_auth_dependency_fails_closed_without_configuration() -> None:
    """No verifier configured => 501, never an implicit anonymous pass-through."""
    app = FastAPI()  # no token_verifier on app.state

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(require_auth_context(_request(app)))

    assert excinfo.value.status_code == 501


def test_missing_bearer_token_is_a_401() -> None:
    app = FastAPI()
    app.state.token_verifier = object()  # configured, so the 501 branch is skipped

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(require_auth_context(_request(app, {"Authorization": "Basic abc"})))

    assert excinfo.value.status_code == 401
    assert "www-authenticate" in {key.lower() for key in (excinfo.value.headers or {})}


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
