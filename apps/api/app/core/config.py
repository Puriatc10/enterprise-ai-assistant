"""Application configuration.

Single source of truth for every setting. Values come from the environment or a
`.env` file; nothing else in the codebase may hardcode a URL, model id, or
credential.

Design rules encoded here:

* Every field has a default that keeps ``GET /health`` working with an empty
  environment. Missing credentials must degrade readiness, never crash startup.
* Secrets are typed as ``SecretStr`` so they cannot leak through ``repr`` or a
  careless log line.
* Unknown environment keys are ignored on purpose: the environment also holds
  unrelated variables (PATH, HOME, ...).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        # Look for a .env in the API directory or at the repository root.
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- application ------------------------------------------------------
    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    # Comma-separated in the environment, list in code.
    api_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- LLM provider abstraction -----------------------------------------
    # A LiteLLM model identifier: provider prefix + model name.
    # Chosen and verified in Phase 1 (2026-11-14): free, tool-calling, 1M context,
    # 1.1–7.7 s observed latency. Evidence: docs/analysis/PHASE1-LLM.md.
    # Fallbacks if the shared free pool saturates: OpenRouter `inclusionai/
    # ling-3.2-flash:free`, `dots-studio/dots-3-note-preview:free`.
    llm_provider: str = "openrouter"
    llm_model: str = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
    # Free gateway routes are slower than paid endpoints; measure, don't assume.
    llm_timeout_seconds: float = 45.0
    openrouter_api_key: SecretStr | None = None
    # Kept as an alternative provider: the project may later be entitled to it.
    gemini_api_key: SecretStr | None = None
    # Hard ceiling on tool-execution rounds per user turn.
    agent_max_tool_rounds: int = 3

    # -- application storage / auth (Supabase) ----------------------------
    supabase_url: str | None = None
    supabase_anon_key: SecretStr | None = None
    # Bypasses row-level security: backend only, never to browser or model.
    supabase_service_role_key: SecretStr | None = None

    # -- external connectors (read-only) ----------------------------------
    didar_api_base_url: str | None = None
    didar_api_key: SecretStr | None = None
    didar_timeout_seconds: float = 10.0
    connector_postgres_url: SecretStr | None = None

    # -- validation -------------------------------------------------------
    @field_validator("app_env", "log_level", "llm_provider")
    @classmethod
    def _strip_and_lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("agent_max_tool_rounds")
    @classmethod
    def _bounded_rounds(cls, value: int) -> int:
        if not 1 <= value <= 10:
            msg = "agent_max_tool_rounds must be between 1 and 10"
            raise ValueError(msg)
        return value

    @field_validator("llm_timeout_seconds", "didar_timeout_seconds")
    @classmethod
    def _positive_timeout(cls, value: float) -> float:
        if value <= 0:
            msg = "timeouts must be positive"
            raise ValueError(msg)
        return value

    # -- derived values ---------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, parsed from the comma-separated setting."""
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def llm_configured(self) -> bool:
        """Whether the configured provider has a credential to be called with.

        A readiness hint for the health endpoint, not a guarantee: it checks that a
        credential exists, not that the provider will accept it. Mapping provider →
        credential here keeps the "is it usable?" question in one place instead of
        leaking provider names into handlers.
        """
        credential = {
            "openrouter": self.openrouter_api_key,
            "gemini": self.gemini_api_key,
        }.get(self.llm_provider)
        return credential is not None and bool(credential.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so repeated dependency resolution does not re-read the environment.
    Tests that patch the environment call ``get_settings.cache_clear()``.
    """
    return Settings()
