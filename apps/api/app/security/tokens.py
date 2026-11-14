"""Supabase access-token verification.

This is the trust boundary: the only place a caller's identity is established, and
the only place that may construct an ``AuthContext``. Everything upstream —
routers, orchestration, tool execution, connectors — consumes that object and never
looks at the request for identity.

Design decisions, each with a reason:

* **Asymmetric only.** Tokens are verified against the project's JWKS
  (ES256/RS256). This project therefore stores no shared JWT secret: a leaked
  public-key cache is harmless, a leaked signing secret is not.
* **`alg` is checked against an allowlist before decoding.** Accepting the token's
  own `alg` header is the classic JWT bypass (`alg: none`, or HS256 signed with the
  public key).
* **Keys are cached, and refreshed only when an unknown `kid` appears** — rotating
  keys must not require a restart, and a hot path must not fetch on every request.
* **Claims that are required are required.** `exp`, `sub`, `iss`, `aud` are not
  optional; a token missing any of them is not a token we can trust for scoping.
* **The fetcher is injected**, so tests sign real tokens with a locally generated
  key pair and never touch the network.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import jwt

from app.domain.models.auth import AuthContext

logger = logging.getLogger(__name__)

JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class AuthError(Exception):
    """A credential that cannot be trusted. Maps to HTTP 401.

    ``reason`` is a short machine-readable code safe to log and to return: it never
    contains the token, and never a stack trace.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class JwksCache:
    """Cached, lazily-refreshed JSON Web Key Set.

    Args:
        fetch_jwks: Coroutine returning the raw JWKS document.
        ttl_seconds: How long a fetched set is considered fresh.
        clock: Injectable time source (tests drive rotation deterministically).
    """

    def __init__(
        self,
        fetch_jwks: JwksFetcher,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch_jwks = fetch_jwks
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self.fetches = 0

    async def _refresh(self) -> None:
        document = await self._fetch_jwks()
        keys: dict[str, jwt.PyJWK] = {}
        for raw in document.get("keys", []):
            kid = raw.get("kid")
            if not kid or raw.get("use") not in (None, "sig"):
                continue
            try:
                keys[str(kid)] = jwt.PyJWK(raw)
            except jwt.PyJWTError:  # pragma: no cover - defensive against odd JWKS
                logger.warning("ignoring unusable JWKS entry kid=%s", kid)
        if not keys:
            raise AuthError("jwks_empty", "The identity provider published no usable keys.")

        self._keys = keys
        self._fetched_at = self._clock()
        self.fetches += 1

    async def get(self, kid: str) -> jwt.PyJWK:
        """Return the key for ``kid``, refreshing once if needed."""
        fetched_at = self._fetched_at
        is_stale = fetched_at is None or (self._clock() - fetched_at) > self._ttl_seconds
        if is_stale or kid not in self._keys:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("unknown_kid", "Token was signed with an unknown key.")
        return key


@dataclass(frozen=True, slots=True)
class TokenVerifierConfig:
    """What a valid token must say, per project."""

    issuer: str
    audience: str
    #: Allowlist. Never trust the token's own `alg` header.
    algorithms: tuple[str, ...] = ("ES256", "RS256")
    leeway_seconds: int = 30


class SupabaseTokenVerifier:
    """Verifies Supabase access tokens and derives ``AuthContext``."""

    def __init__(
        self,
        jwks: JwksCache,
        config: TokenVerifierConfig,
        *,
        decode: Callable[..., dict[str, Any]] = jwt.decode,
    ) -> None:
        self._jwks = jwks
        self._config = config
        self._decode_fn = decode

    async def verify(self, token: str) -> AuthContext:
        """Return the verified identity, or raise ``AuthError``.

        Raises:
            AuthError: malformed, unsigned-by-us, expired, wrong audience/issuer, or
                missing a claim required for scoping.
        """
        if not token or not token.strip():
            raise AuthError("missing_token", "No bearer token was supplied.")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthError("malformed_token", "Token is not a valid JWT.") from exc

        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in self._config.algorithms:
            raise AuthError("unsupported_alg", "Token uses a signing algorithm we do not accept.")

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError("missing_kid", "Token header is missing a key id.")

        key = await self._jwks.get(kid)

        try:
            claims = self._decode_fn(
                token,
                key=key.key,
                algorithms=[algorithm],
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=self._config.leeway_seconds,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("expired_token", "Token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("invalid_audience", "Token was issued for another audience.") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError("invalid_issuer", "Token was issued by another provider.") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise AuthError("missing_claim", "Token is missing a required claim.") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token", "Token signature or claims did not verify.") from exc

        return self._to_context(claims)

    def _to_context(self, claims: dict[str, Any]) -> AuthContext:
        """Derive identity from verified claims only — never from request input."""
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthError("missing_subject", "Token has no usable subject.")

        metadata = claims.get("app_metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        # Tenant: an explicit claim wins; otherwise a user is their own tenant,
        # which keeps per-user isolation correct even before tenants are modelled.
        tenant = metadata.get("tenant_id") or claims.get("tenant_id") or subject

        roles: list[str] = []
        for candidate in (metadata.get("roles"), claims.get("role")):
            if isinstance(candidate, str):
                roles.append(candidate)
            elif isinstance(candidate, (list, tuple)):
                roles.extend(str(item) for item in candidate)

        scopes: set[str] = set()
        for candidate in (claims.get("scope"), metadata.get("scopes")):
            if isinstance(candidate, str):
                scopes.update(candidate.split())
            elif isinstance(candidate, (list, tuple)):
                scopes.update(str(item) for item in candidate)

        return AuthContext(
            subject=subject,
            tenant_id=str(tenant),
            roles=tuple(dict.fromkeys(roles)),
            scopes=frozenset(scopes),
        )
