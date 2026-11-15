"""Composition root: the one place that decides which concrete adapter is live.

Phase 0 wired settings only. Phase 2 adds the LLM adapter; Phase 3 adds the tool
registry; Phase 4 adds connectors. Keeping this file as the single wiring point is
what allows routes, services and orchestration to depend on protocols instead of
implementations.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, Request, status

from app.application.tools.crm import build_crm_registry
from app.application.tools.executor import ToolExecutor
from app.application.tools.overview import build_overview_registry
from app.application.tools.policies import build_policy_registry
from app.application.tools.registry import ToolRegistry
from app.application.tools.support import build_support_registry
from app.core.config import Settings, get_settings
from app.domain.interfaces.connector import DataConnectorPort
from app.domain.interfaces.llm import LLMPort
from app.domain.interfaces.repository import ConversationRepository
from app.domain.models.auth import AuthContext
from app.domain.models.sources import DomainDescriptor
from app.infrastructure.connectors.mock.policies import MockPolicyConnector
from app.infrastructure.connectors.postgres.crm import CrmConnector
from app.infrastructure.connectors.postgres.policies import PostgresPolicyConnector
from app.infrastructure.connectors.sqlite.support import SqliteSupportConnector
from app.infrastructure.llm.litellm_adapter import LiteLLMAdapter
from app.infrastructure.persistence.supabase import SupabaseConversationRepository
from app.security.auth import require_auth_context
from app.security.rate_limit import TurnRateLimiter
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


def get_conversation_repository(request: Request) -> ConversationRepository:
    """Build a repository scoped to the caller of this request.

    The caller's token is forwarded to PostgREST, so the database applies its own
    row-level policies alongside the backend's scoping. That is why this is built per
    request rather than cached on ``app.state``: the credential is per caller.

    ``app.state.conversation_repository`` is an explicit override seam for tests and
    for a local run with no database configured.
    """
    override = getattr(request.app.state, "conversation_repository", None)
    if override is not None:
        return override

    settings = provide_settings(request)
    access_token = getattr(request.state, "access_token", None)
    if not settings.supabase_configured or not settings.supabase_url or not access_token:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Persistence is not configured (Supabase URL/key missing).",
        )

    anon_key = settings.supabase_anon_key
    assert anon_key is not None  # guaranteed by supabase_configured  # noqa: S101
    return SupabaseConversationRepository(
        base_url=settings.supabase_url,
        api_key=anon_key.get_secret_value(),
        access_token=access_token,
    )


#: Annotated dependency type for handlers: ``repo: ConversationRepositoryDep``.
ConversationRepositoryDep = Annotated[ConversationRepository, Depends(get_conversation_repository)]

#: Annotated dependency type for handlers: ``context: AuthContextDep``.
AuthContextDep = Annotated[AuthContext, Depends(require_auth_context)]


def build_policy_connector(settings: Settings) -> DataConnectorPort:
    """Choose the adapter that answers policy questions.

    One place decides which source is live, so the tool layer and the orchestrator stay
    unaware of it. ``postgres`` without a DSN is a configuration error, not something to
    paper over with a silent fallback to synthetic data — falling back would answer
    confidently from the wrong source.
    """
    if settings.policies_source == "postgres":
        dsn = settings.postgres_dsn
        if dsn is None:
            msg = "POLICIES_SOURCE=postgres requires CONNECTOR_POSTGRES_URL"
            raise RuntimeError(msg)
        return PostgresPolicyConnector(
            dsn=dsn,
            row_limit=settings.postgres_row_limit,
            statement_timeout_ms=settings.postgres_statement_timeout_ms,
        )
    return MockPolicyConnector()


@dataclass(frozen=True, slots=True)
class ToolSetup:
    """A composed registry plus the domains that fed it."""

    registry: ToolRegistry
    domains: tuple[str, ...]


def build_tool_registry(settings: Settings) -> ToolSetup:
    """Compose every enabled domain into one registry.

    This is the only place that decides which sources are live, so the orchestrator never
    learns a domain exists. A domain that is configured but unreachable still composes:
    its tools answer with a typed error instead of vanishing, which is far easier to
    diagnose than a silently shorter tool list.
    """
    domain_registries: list[ToolRegistry] = []
    descriptors: list[DomainDescriptor] = []

    def add(descriptor_id: str, label: str, description: str, registry: ToolRegistry) -> None:
        domain_registries.append(registry)
        descriptors.append(
            DomainDescriptor(
                id=descriptor_id,
                label=label,
                description=description,
                tools=registry.names,
            )
        )

    add(
        "insurance",
        "بیمه",
        "بیمه‌نامه‌های سازمانی کاربر",
        build_policy_registry(build_policy_connector(settings)),
    )

    if settings.support_source == "sqlite":
        add(
            "support",
            "پشتیبانی",
            "تیکت‌های پشتیبانی ثبت‌شده برای کاربر",
            build_support_registry(
                SqliteSupportConnector(
                    path=settings.support_sqlite_path,
                    row_limit=settings.postgres_row_limit,
                )
            ),
        )

    if settings.crm_source == "postgres":
        dsn = settings.postgres_dsn
        if dsn is None:
            msg = "CRM_SOURCE=postgres requires CONNECTOR_POSTGRES_URL"
            raise RuntimeError(msg)
        add(
            "crm",
            "CRM",
            "حساب مشتری و معاملات فروش",
            build_crm_registry(
                CrmConnector(
                    dsn=dsn,
                    row_limit=settings.postgres_row_limit,
                    statement_timeout_ms=settings.postgres_statement_timeout_ms,
                )
            ),
        )

    # The capability tool goes in last: it describes what the composed registry holds, so
    # it must not be able to describe itself as missing.
    composed = domain_registries[0].compose(*domain_registries[1:])
    registry = composed.compose(build_overview_registry(descriptors))
    return ToolSetup(registry=registry, domains=tuple(item.id for item in descriptors))


def get_tool_registry(request: Request) -> ToolRegistry:
    """The registered tools.

    Built once per process (startup sets it on ``app.state``); the fallback keeps
    scripts and tests working when they construct an app without the lifespan.
    """
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        registry = build_tool_registry(get_settings()).registry
        request.app.state.tool_registry = registry
    return registry


def get_tool_executor(request: Request) -> ToolExecutor:
    """The executor, bound to whichever registry is live."""
    executor = getattr(request.app.state, "tool_executor", None)
    if executor is None:
        executor = ToolExecutor(registry=get_tool_registry(request))
        request.app.state.tool_executor = executor
    return executor


#: Annotated dependency types for handlers.
ToolRegistryDep = Annotated[ToolRegistry, Depends(get_tool_registry)]
ToolExecutorDep = Annotated[ToolExecutor, Depends(get_tool_executor)]


def get_rate_limiter(request: Request) -> TurnRateLimiter:
    """The process-wide limiter.

    Built at startup so every request shares one window; the fallback keeps scripts and
    tests working when they construct an app without the lifespan.
    """
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = TurnRateLimiter(limit=get_settings().rate_limit_turns_per_hour)
        request.app.state.rate_limiter = limiter
    return limiter


RateLimiterDep = Annotated[TurnRateLimiter, Depends(get_rate_limiter)]
