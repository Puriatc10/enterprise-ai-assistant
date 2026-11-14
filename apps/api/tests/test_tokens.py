"""Token-verification contract, with real signatures and no network.

Each test signs a genuine ES256 token with a locally generated P-256 key and
serves a matching JWKS from an injected fetcher — the same path production takes,
minus HTTP. That is the only way to prove signature checking works: a mocked
decoder proves nothing about the crypto.

The negative cases are the point. A verifier that accepts a token it should reject
is worse than no verifier, because it makes the system *look* authenticated.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.security.tokens import AuthError, JwksCache, SupabaseTokenVerifier, TokenVerifierConfig

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"
KID = "test-kid-1"


def _b64_uint(value: int, length: int = 32) -> str:
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(private_key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, Any]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
        "x": _b64_uint(numbers.x),
        "y": _b64_uint(numbers.y),
    }


PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "app_metadata": {"tenant_id": "tenant-9", "roles": ["member"]},
    }
    claims.update(overrides)
    return claims


def _token(
    claims: dict[str, Any] | None = None,
    *,
    key: ec.EllipticCurvePrivateKey = PRIVATE_KEY,
    kid: str = KID,
    algorithm: str = "ES256",
    **overrides: Any,
) -> str:
    payload = _claims(**overrides) if claims is None else claims
    signing_key: Any = key if algorithm == "ES256" else "x" * 32
    return jwt.encode(payload, signing_key, algorithm=algorithm, headers={"kid": kid})


class _Jwks:
    """Mutable JWKS document, so key rotation can be simulated."""

    def __init__(self, keys: list[dict[str, Any]]) -> None:
        self.document: dict[str, Any] = {"keys": keys}
        self.fetches = 0

    async def __call__(self) -> dict[str, Any]:
        self.fetches += 1
        return self.document


def _verifier(jwks: _Jwks) -> SupabaseTokenVerifier:
    return SupabaseTokenVerifier(
        jwks=JwksCache(jwks),
        config=TokenVerifierConfig(issuer=ISSUER, audience=AUDIENCE),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_valid_token_yields_auth_context_from_claims() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    context = await verifier.verify(_token())

    assert context.subject == "user-1"
    assert context.tenant_id == "tenant-9"
    assert context.roles == ("member",)
    assert context.has_scopes(set()) is True


async def test_tenant_falls_back_to_subject_without_a_claim() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    context = await verifier.verify(_token(app_metadata={}))

    assert context.tenant_id == "user-1"


async def test_scopes_are_collected_from_claim_and_metadata() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))
    token = _token(scope="policies:read chat:write", app_metadata={"scopes": ["policies:write"]})

    context = await verifier.verify(token)

    assert context.scopes == frozenset({"policies:read", "chat:write", "policies:write"})
    assert context.has_scopes({"chat:write"}) is True
    assert context.has_scopes({"admin"}) is False


# ---------------------------------------------------------------------------
# Negative cases — every one of these is a bypass if it passes
# ---------------------------------------------------------------------------


async def test_expired_token_is_rejected() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))
    now = int(time.time())
    expired_claims = {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": now - 120}

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token(expired_claims))

    assert excinfo.value.reason == "expired_token"


async def test_wrong_audience_is_rejected() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token(aud="anon"))

    assert excinfo.value.reason == "invalid_audience"


async def test_wrong_issuer_is_rejected() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token(iss="https://attacker.example/auth/v1"))

    assert excinfo.value.reason == "invalid_issuer"


async def test_signature_from_another_key_is_rejected() -> None:
    """The kid matches, the signature does not: crypto, not lookup, must catch it."""
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token(key=OTHER_KEY))

    assert excinfo.value.reason == "invalid_token"


async def test_unknown_kid_is_rejected_after_a_refresh() -> None:
    jwks = _Jwks([_jwk(PRIVATE_KEY, KID)])
    verifier = _verifier(jwks)

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token(kid="different-kid"))

    assert excinfo.value.reason == "unknown_kid"
    assert jwks.fetches == 1


async def test_algorithm_outside_the_allowlist_is_rejected_before_decoding() -> None:
    """`alg` comes from the token, so it must never choose how it is verified."""
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token(algorithm="HS256"))

    assert excinfo.value.reason == "unsupported_alg"


async def test_token_without_subject_is_rejected() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))
    now = int(time.time())

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token({"iss": ISSUER, "aud": AUDIENCE, "exp": now + 300}))

    assert excinfo.value.reason in {"missing_claim", "missing_subject"}


async def test_empty_and_malformed_tokens_are_rejected() -> None:
    verifier = _verifier(_Jwks([_jwk(PRIVATE_KEY, KID)]))

    with pytest.raises(AuthError) as empty:
        await verifier.verify("   ")
    assert empty.value.reason == "missing_token"

    with pytest.raises(AuthError) as malformed:
        await verifier.verify("not-a-jwt")
    assert malformed.value.reason == "malformed_token"


async def test_jwks_without_usable_keys_is_rejected() -> None:
    verifier = _verifier(_Jwks([]))

    with pytest.raises(AuthError) as excinfo:
        await verifier.verify(_token())

    assert excinfo.value.reason == "jwks_empty"


# ---------------------------------------------------------------------------
# Key caching and rotation
# ---------------------------------------------------------------------------


async def test_jwks_is_fetched_once_across_calls() -> None:
    jwks = _Jwks([_jwk(PRIVATE_KEY, KID)])
    verifier = _verifier(jwks)

    await verifier.verify(_token())
    await verifier.verify(_token())

    assert jwks.fetches == 1, "a hot path must not fetch the JWKS per request"


async def test_rotated_key_triggers_a_refresh_without_restart() -> None:
    jwks = _Jwks([_jwk(PRIVATE_KEY, "old-kid")])
    verifier = _verifier(jwks)
    await verifier.verify(_token(kid="old-kid"))

    jwks.document = {"keys": [_jwk(PRIVATE_KEY, "new-kid")]}
    context = await verifier.verify(_token(kid="new-kid"))

    assert context.subject == "user-1"
    assert jwks.fetches == 2
