"""Health endpoint contract.

The single most important property under test: the API boots and reports healthy
**without any credential**, so local development and CI need no secrets.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import FAKE_API_KEY


def test_health_returns_ok_without_any_credentials(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "enterprise-ai-assistant-api"
    assert body["environment"] == "development"
    assert body["version"]


def test_health_reports_llm_as_unconfigured_without_a_key(client: TestClient) -> None:
    llm = client.get("/health").json()["llm"]

    assert llm["provider"] == "openrouter"
    assert llm["model"].startswith("openrouter/")
    assert llm["configured"] is False


def test_health_reports_llm_as_configured_when_a_key_is_present(
    configured_client: TestClient,
) -> None:
    assert configured_client.get("/health").json()["llm"]["configured"] is True


def test_health_never_echoes_the_credential(configured_client: TestClient) -> None:
    assert FAKE_API_KEY not in configured_client.get("/health").text


def test_openapi_schema_documents_the_health_route(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]


def test_cors_allows_the_configured_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_does_not_allow_an_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "https://not-our-frontend.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers
