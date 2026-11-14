"""Configuration contract: safe defaults, typed parsing, bounded values."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_are_safe_and_credential_free() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.gemini_api_key is None
    assert settings.supabase_url is None
    assert settings.supabase_configured is False
    assert settings.llm_configured is False


def test_cors_origins_are_parsed_from_a_comma_separated_value() -> None:
    settings = Settings(
        _env_file=None,
        api_cors_origins="http://localhost:5173, https://app.example.com ,",
    )

    assert settings.cors_origins == ["http://localhost:5173", "https://app.example.com"]


def test_values_are_normalized() -> None:
    settings = Settings(_env_file=None, app_env="  PRODUCTION ", llm_provider="Gemini")

    assert settings.app_env == "production"
    assert settings.llm_provider == "gemini"
    assert settings.is_production is True


def test_secrets_are_not_printable() -> None:
    settings = Settings(_env_file=None, openrouter_api_key="super-secret-value")

    assert "super-secret-value" not in repr(settings)
    assert settings.llm_configured is True


def test_readiness_checks_the_credential_of_the_configured_provider() -> None:
    """A key for a *different* provider must not report the active one as ready."""
    settings = Settings(_env_file=None, gemini_api_key="some-gemini-key", llm_provider="openrouter")

    assert settings.llm_configured is False


def test_readiness_is_false_for_an_unknown_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="some-future-provider",
        openrouter_api_key="a-key",
        gemini_api_key="another-key",
    )

    assert settings.llm_configured is False


@pytest.mark.parametrize("rounds", [0, -1, 11])
def test_tool_round_ceiling_is_bounded(rounds: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_max_tool_rounds=rounds)


@pytest.mark.parametrize("timeout", [0, -5.0])
def test_timeouts_must_be_positive(timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=timeout)
