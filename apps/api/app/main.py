"""Application entry point.

An app factory (``create_app``) rather than a module-level singleton only: tests
build an application with explicit settings, while the ASGI entry point
(``app.main:app``) builds it from the environment.

What is intentionally absent at Phase 0: no database connection, no LLM client,
no connector. Startup must succeed with an empty ``.env``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.errors import RequestIDMiddleware, TimingMiddleware, install_error_handlers
from app.api.router import api_router
from app.api.routes.health import SERVICE_NAME
from app.application.tools.executor import ToolExecutor
from app.core.config import Settings, get_settings
from app.core.dependencies import build_token_verifier, build_tool_registry
from app.core.logging import configure_logging
from app.security.rate_limit import TurnRateLimiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook.

    Phase 2 builds long-lived adapters here (LLM client, persistence repository)
    and attaches them to ``app.state`` so nothing is constructed per request.
    """
    settings: Settings = application.state.settings
    # Composition happens at startup, once: the verifier owns a key cache, and the
    # LLM adapter is built lazily on first use (it needs the provider SDK).
    application.state.token_verifier = build_token_verifier(settings)

    # Tools: one registry composed from every enabled domain. Built here so the
    # composition is validated before the first request.
    setup = build_tool_registry(settings)
    application.state.tool_registry = setup.registry
    application.state.tool_executor = ToolExecutor(registry=setup.registry)
    application.state.rate_limiter = TurnRateLimiter(limit=settings.rate_limit_turns_per_hour)

    logger.info(
        "startup service=%s env=%s version=%s llm_provider=%s llm_model=%s "
        "llm_configured=%s supabase_configured=%s auth_enabled=%s "
        "domains=%s tools=%d",
        SERVICE_NAME,
        settings.app_env,
        settings.app_version,
        settings.llm_provider,
        settings.llm_model,
        settings.llm_configured,
        settings.supabase_configured,
        application.state.token_verifier is not None,
        ",".join(setup.domains),
        len(setup.registry),
    )
    yield
    logger.info("shutdown service=%s", SERVICE_NAME)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Args:
        settings: Explicit settings (tests, scripts). Defaults to the environment.
    """
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    application = FastAPI(
        title="Enterprise AI Assistant API",
        summary="Authenticated assistant over multiple read-only organizational data sources.",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = resolved

    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    # Order matters: request id first so error responses can echo it.
    application.add_middleware(RequestIDMiddleware)
    # Added after the id middleware so the access log can read the id it minted.
    application.add_middleware(TimingMiddleware)

    install_error_handlers(application)

    application.include_router(api_router)
    return application


#: ASGI entry point: `uvicorn app.main:app`.
app = create_app()
