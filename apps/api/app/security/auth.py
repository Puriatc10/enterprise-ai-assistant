"""Authentication enforcement point.

Phase 0 deliberately contains **no** token verifier. Rather than shipping a
placeholder that lets requests through, the dependency fails closed: any route
that depends on it returns ``501`` until Phase 2 wires real Supabase JWT
verification (signature against JWKS, ``iss``/``aud``, ``exp``/``nbf``, then
``AuthContext`` derived from the claims).

Why this file exists before the implementation: authorization must not be
something a later feature adds. Every future route is written against
``require_auth_context``; the contract is "you get a verified identity or the
request stops here".
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.domain.models.auth import AuthContext

#: Header carrying the Supabase access token.
AUTHORIZATION_HEADER = "Authorization"


async def require_auth_context(request: Request | None = None) -> AuthContext:
    """FastAPI dependency: return the verified identity for this request.

    Phase 0 behaviour: always raises ``501 Not Implemented``.

    Phase 2 behaviour: reads the bearer token, verifies it against Supabase's
    JWKS, and returns ``AuthContext`` built only from verified claims. A model,
    request body, or header other than the bearer token may never supply
    ``subject`` or ``tenant_id``.

    Raises:
        HTTPException: 401 when no usable credential is presented, 403 when the
            token is valid but lacks the required scope, 501 until implemented.
    """
    # The request is accepted now so the signature stays stable in Phase 2.
    del request
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Authentication is not implemented yet (Phase 2). This endpoint fails closed "
            "rather than serving unauthenticated requests."
        ),
    )
