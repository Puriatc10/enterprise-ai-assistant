"""HTTP contract for the chat endpoints.

Dependencies are overridden rather than mocked at the HTTP layer: the real routing,
validation, middleware and error handlers all run, so what is tested is the behaviour
a client would meet. Only the two edges — identity and the provider — are replaced.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.dependencies import get_llm_adapter
from app.domain.interfaces.llm import LLMAuthError, LLMRateLimitError
from app.domain.models.auth import AuthContext
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository
from app.main import create_app
from app.security.auth import require_auth_context

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")


@pytest.fixture
def app_client() -> Iterator[tuple[TestClient, dict[str, AuthContext], FakeLLMAdapter]]:
    """A client whose identity is switchable and whose model is scripted."""
    application = create_app(Settings(_env_file=None))
    application.state.conversation_repository = InMemoryConversationRepository()
    actor: dict[str, AuthContext] = {"current": ALICE}
    llm = FakeLLMAdapter([text_response("شما ۳ بیمه‌نامه دارید.")])

    application.dependency_overrides[require_auth_context] = lambda: actor["current"]
    application.dependency_overrides[get_llm_adapter] = lambda: llm

    with TestClient(application) as client:
        yield client, actor, llm


def test_protected_routes_fail_closed_when_auth_is_not_configured() -> None:
    application = create_app(Settings(_env_file=None))

    with TestClient(application) as client:
        response = client.post("/conversations", json={})

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "http_501"


def test_every_response_carries_a_request_id(app_client: tuple) -> None:
    client, _, _ = app_client

    generated = client.get("/health")
    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert generated.headers["x-request-id"]
    assert echoed.headers["x-request-id"] == "abc-123"


def test_create_conversation_returns_it(app_client: tuple) -> None:
    client, _, _ = app_client

    response = client.post("/conversations", json={"title": "بیمه‌نامه‌ها"})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "بیمه‌نامه‌ها"
    assert body["id"]


def test_sending_a_message_returns_the_answer_and_persists_history(app_client: tuple) -> None:
    client, _, _ = app_client
    conversation_id = client.post("/conversations", json={}).json()["id"]

    answer = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "چند بیمه‌نامه دارم؟"},
    )
    history = client.get(f"/conversations/{conversation_id}/messages")

    assert answer.status_code == 200
    assert answer.json()["answer"] == "شما ۳ بیمه‌نامه دارید."
    assert answer.json()["conversation_id"] == conversation_id
    assert [m["role"] for m in history.json()] == ["user", "assistant"]


def test_unknown_conversation_is_a_404_envelope(app_client: tuple) -> None:
    client, _, _ = app_client

    response = client.post(
        "/conversations/00000000-0000-0000-0000-000000000000/messages",
        json={"content": "سلام"},
    )

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "conversation_not_found"
    assert body["request_id"]


def test_conversations_are_scoped_to_the_caller(app_client: tuple) -> None:
    client, actor, _ = app_client
    client.post("/conversations", json={"title": "مال آلیس"})

    actor["current"] = BOB
    response = client.get("/conversations")

    assert response.status_code == 200
    assert response.json() == []


def test_empty_message_is_a_validation_error(app_client: tuple) -> None:
    client, _, _ = app_client
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.post(f"/conversations/{conversation_id}/messages", json={"content": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_a_provider_credential_problem_is_503_not_500(app_client: tuple) -> None:
    """Our misconfiguration must not look like the caller's mistake."""
    client, _, llm = app_client
    llm._responses = [LLMAuthError("no entitlement")]  # noqa: SLF001 - test seam
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.post(f"/conversations/{conversation_id}/messages", json={"content": "سلام"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


def test_a_rate_limited_provider_is_429(app_client: tuple) -> None:
    client, _, llm = app_client
    llm._responses = [LLMRateLimitError("429")]  # noqa: SLF001 - test seam
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.post(f"/conversations/{conversation_id}/messages", json={"content": "سلام"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "llm_rate_limited"
