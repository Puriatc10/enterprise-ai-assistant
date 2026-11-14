"""Real authentication: bearer token → verified ``AuthContext``.

Phase 0 shipped a dependency that failed closed with `501`. That is replaced here,
but the fail-closed behaviour is preserved for the case that still matters: a
deployment with no Supabase configuration must refuse protected routes rather than
invent an identity.

The rule this file enforces (docs/architecture/SECURITY.md §1): identity comes from
a verified token and from nowhere else. No handler, service, tool or connector may
fall back to a client-supplied user or tenant id.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.domain.models.auth import AuthContext
from app.security.tokens import AuthError, SupabaseTokenVerifier

#: Header carrying the Supabase access token.
AUTHORIZATION_HEADER = "Authorization"

_BEARER = "bearer"


def _unauthorized(reason: str, detail: str) -> HTTPException:
    # `WWW-Authenticate` is required by RFC 6750 for a 401, and the reason code is
    # safe to expose: it names a category, never a token or a claim value.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Authentication failed: {detail}",
        headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description="{reason}"'},
    )


async def require_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency: return the verified identity for this request.

    Raises:
        HTTPException: 501 when authentication is not configured (fail closed),
            401 when the credential is missing or untrustworthy.
    """
    verifier: SupabaseTokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Authentication is not configured (SUPABASE_URL/SUPABASE_ANON_KEY missing). "
                "Protected routes fail closed rather than serving anonymous requests."
            ),
        )

    scheme, _, token = (request.headers.get(AUTHORIZATION_HEADER) or "").partition(" ")
    if scheme.lower() != _BEARER or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed: missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return await verifier.verify(token.strip())
    except AuthError as exc:
        raise _unauthorized(exc.reason, str(exc)) from exc
