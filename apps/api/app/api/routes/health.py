"""Health endpoint.

Design intent: liveness must never depend on a credential. A missing provider key,
an unconfigured Supabase project, or an unreachable data source degrades the
*reported readiness* of that dependency — it does not turn the process unhealthy
and it does not crash startup. Deployment health checks therefore stay meaningful,
and local development needs no secrets.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.dependencies import SettingsDep

router = APIRouter(tags=["health"])

SERVICE_NAME = "enterprise-ai-assistant-api"


class LLMStatus(BaseModel):
    """Readiness of the configured LLM provider (presence, not validity)."""

    provider: str = Field(examples=["openrouter"])
    model: str = Field(examples=["openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"])
    configured: bool = Field(
        description="A credential for this provider is present. Presence only: a key "
        "that exists but is invalid will still fail at call time."
    )


class HealthResponse(BaseModel):
    """Stable liveness payload."""

    status: Literal["ok"] = "ok"
    service: str = SERVICE_NAME
    version: str
    environment: str
    llm: LLMStatus


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 whenever the process is serving requests. Never requires "
    "credentials, a database, or an LLM key.",
)
async def health(settings: SettingsDep) -> HealthResponse:
    """Report liveness plus non-fatal dependency readiness."""
    return HealthResponse(
        version=settings.app_version,
        environment=settings.app_env,
        llm=LLMStatus(
            provider=settings.llm_provider,
            model=settings.llm_model,
            configured=settings.llm_configured,
        ),
    )
