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
    # Audience Supabase puts in access tokens; "authenticated" is its default.
    supabase_jwt_audience: str = "authenticated"
    # Deliberately absent: supabase_service_role_key. The backend queries PostgREST
    # with the *caller's* token so row-level security enforces scope. Holding a
    # key that bypasses RLS would disable the backstop this design advertises
    # (docs/architecture/SECURITY.md §2).

    # -- external connectors (read-only) ----------------------------------
    didar_api_base_url: str | None = None
    didar_api_key: SecretStr | None = None
    didar_timeout_seconds: float = 10.0

    # Which adapter answers policy questions. "mock" needs nothing and is the
    # documented demonstration path (ADR-0002); "postgres" reads a real database
    # with read-only credentials (ADR-0005).
    policies_source: str = "mock"
    connector_postgres_url: SecretStr | None = None
    postgres_row_limit: int = 50
    postgres_statement_timeout_ms: int = 5_000

    # -- support tickets (SQLite, read-only file) — ADR-0008 -----------------
    # "sqlite" reads a seeded file; "mock" generates in-memory; "disabled" omits the
    # domain entirely. The file is created by scripts/seed_support_sqlite.py, never by
    # the running application (no runtime writes).
    support_source: str = "sqlite"
    support_sqlite_path: str = "data/demo_support.sqlite3"

    # -- CRM (customers, deals) on a read-only PostgreSQL connection --------
    # Off by default: it needs a reachable database, a `crm` schema and a SELECT-only
    # role, so turning it on is a deliberate environment decision.
    crm_source: str = "disabled"

    # -- validation -------------------------------------------------------
    @field_validator("app_env", "log_level", "llm_provider", "policies_source")
    @classmethod
    def _strip_and_lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("support_source", "crm_source")
    @classmethod
    def _strip_source(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("support_source")
    @classmethod
    def _known_support_source(cls, value: str) -> str:
        allowed = {"sqlite", "mock", "disabled"}
        if value not in allowed:
            msg = f"support_source must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return value

    @field_validator("crm_source")
    @classmethod
    def _known_crm_source(cls, value: str) -> str:
        allowed = {"postgres", "disabled"}
        if value not in allowed:
            msg = f"crm_source must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return value

    @field_validator("policies_source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        allowed = {"mock", "postgres"}
        if value not in allowed:
            msg = f"policies_source must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return value

    @field_validator("postgres_row_limit")
    @classmethod
    def _bounded_row_limit(cls, value: int) -> int:
        # The connector refuses anything above 100 as well; this catches a bad .env
        # at boot rather than at the first query.
        if not 1 <= value <= 100:
            msg = "postgres_row_limit must be between 1 and 100"
            raise ValueError(msg)
        return value

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

    @field_validator("supabase_url", "didar_api_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str | None) -> str | None:
        """Normalize base URLs: a trailing slash produces `//auth/v1/...`."""
        return value.rstrip("/") if isinstance(value, str) else value

    # -- derived values ---------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, parsed from the comma-separated setting."""
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def llm_api_key(self) -> str | None:
        """The credential string for the configured provider, if any.

        Adapters receive this explicitly rather than relying on the SDK finding it
        in ``os.environ``: settings are read from ``.env`` by Pydantic and never
        injected into the process environment, so an SDK that only looks at
        environment variables would silently see no key.
        """
        credential = {
            "openrouter": self.openrouter_api_key,
            "gemini": self.gemini_api_key,
        }.get(self.llm_provider)
        return credential.get_secret_value() if credential is not None else None

    @property
    def llm_configured(self) -> bool:
        """Whether the configured provider has a credential to be called with.

        A readiness hint for the health endpoint, not a guarantee: it checks that a
        credential exists, not that the provider will accept it. Mapping provider →
        credential here keeps the "is it usable?" question in one place instead of
        leaking provider names into handlers.
        """
        key = self.llm_api_key
        return key is not None and bool(key.strip())

    # -- Supabase derived endpoints ---------------------------------------
    @property
    def postgres_dsn(self) -> str | None:
        """Connection string for the read-only policy connector, if configured."""
        if self.connector_postgres_url is None:
            return None
        dsn = self.connector_postgres_url.get_secret_value().strip()
        return dsn or None

    @property
    def supabase_configured(self) -> bool:
        """Whether storage/auth can be reached at all (URL + public key present)."""
        return bool(self.supabase_url) and self.supabase_anon_key is not None

    @property
    def supabase_jwks_url(self) -> str | None:
        """Public keys for verifying access tokens.

        Asymmetric signing (ES256/RS256) is the current Supabase default, which is
        why no shared JWT secret is stored anywhere in this project.
        """
        if not self.supabase_url:
            return None
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def supabase_issuer(self) -> str | None:
        """Expected `iss` claim for this project's tokens."""
        if not self.supabase_url:
            return None
        return f"{self.supabase_url}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so repeated dependency resolution does not re-read the environment.
    Tests that patch the environment call ``get_settings.cache_clear()``.
    """
    return Settings()
