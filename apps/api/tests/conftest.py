"""Shared test fixtures.

Tests must never depend on the developer's real environment or on network access.
Settings are constructed explicitly with ``_env_file=None`` so a local ``.env``
cannot change what a test asserts.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

#: Obvious dummy value: never a real credential.
FAKE_API_KEY = "test-key-not-a-real-credential"


@pytest.fixture
def bare_settings() -> Settings:
    """Settings with nothing configured — the Phase 0 default state."""
    return Settings(_env_file=None, gemini_api_key=None)


@pytest.fixture
def configured_settings() -> Settings:
    """Settings with a placeholder credential, to exercise readiness reporting."""
    return Settings(_env_file=None, gemini_api_key=FAKE_API_KEY)


@pytest.fixture
def client(bare_settings: Settings) -> Iterator[TestClient]:
    """Client for an app built from unconfigured settings."""
    with TestClient(create_app(bare_settings)) as test_client:
        yield test_client


@pytest.fixture
def configured_client(configured_settings: Settings) -> Iterator[TestClient]:
    """Client for an app whose LLM credential is present (but invalid)."""
    with TestClient(create_app(configured_settings)) as test_client:
        yield test_client
