"""Composition root: the one place that decides which concrete adapter is live.

Phase 0 wired settings only. Phase 2 adds the LLM adapter; Phase 3 adds the tool
registry; Phase 4 adds connectors. Keeping this file as the single wiring point is
what allows routes, services and orchestration to depend on protocols instead of
implementations.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.domain.interfaces.llm import LLMPort
from app.infrastructure.llm.litellm_adapter import LiteLLMAdapter
from app.security.tokens import JwksCache, SupabaseTokenVerifier, TokenVerifierConfig

logger = logging.getLogger(__name__)


def provide_settings(request: Request) -> Settings:
    """FastAPI dependency returning the settings for *this* application instance.

    The application's own settings win over the module-level cache: an app built
    with explicit settings (``create_app(settings)`` in tests or scripts) must
    behave the way it was told to, not the way the process environment happens to
    look. Falls back to the environment-derived settings when no instance value
    is attached.
    """
    instance_settings = getattr(request.app.state, "settings", None)
    if isinstance(instance_settings, Settings):
        return instance_settings
    return get_settings()


#: Annotated dependency type for handlers: ``settings: SettingsDep``.
SettingsDep = Annotated[Settings, Depends(provide_settings)]


def build_llm_adapter(settings: Settings) -> LLMPort:
    """Compose the live LLM adapter.

    The provider SDK is imported **here**, not in the adapter module: that is what
    lets `litellm` stay an optional install while the adapter, the port, the tests
    and CI never need it. If the extra is missing, say so in terms of the fix.
    """
    try:
        from litellm import acompletion  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "The configured LLM provider needs the optional dependency: run `uv sync --extra llm`."
        ) from exc

    return LiteLLMAdapter(
        completion=acompletion,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def get_llm_adapter(request: Request) -> LLMPort:
    """Return the process-wide LLM adapter, building it on first use.

    Cached on ``app.state`` so a client and its connection pool are not rebuilt per
    request. Tests override this dependency rather than patching the SDK.
    """
    adapter = getattr(request.app.state, "llm_adapter", None)
    if adapter is None:
        adapter = build_llm_adapter(provide_settings(request))
        request.app.state.llm_adapter = adapter
    return adapter


#: Annotated dependency type for handlers: ``llm: LLMPortDep``.
LLMPortDep = Annotated[LLMPort, Depends(get_llm_adapter)]


def _jwks_fetcher(url: str) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Build a fetcher for the project's JWKS document.

    A short-lived client per refresh is deliberate: refreshes are rare (cached for
    five minutes and only on rotation), and this avoids holding a connection pool
    open for a call that may never happen.
    """

    async def fetch() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("JWKS endpoint did not return a JSON object.")
        return payload

    return fetch


def build_token_verifier(settings: Settings) -> SupabaseTokenVerifier | None:
    """Compose the token verifier, or ``None`` when Supabase is not configured.

    Returning ``None`` (rather than a permissive stub) is what preserves
    fail-closed behaviour: protected routes answer `501` instead of guessing an
    identity.
    """
    jwks_url = settings.supabase_jwks_url
    issuer = settings.supabase_issuer
    if not jwks_url or not issuer or not settings.supabase_configured:
        logger.info("supabase auth not configured; protected routes will fail closed")
        return None

    return SupabaseTokenVerifier(
        jwks=JwksCache(_jwks_fetcher(jwks_url)),
        config=TokenVerifierConfig(
            issuer=issuer,
            audience=settings.supabase_jwt_audience,
        ),
    )
