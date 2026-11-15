"""The primary journey, end to end over HTTP.

Everything real except the two edges: identity (a switchable principal) and the model
(a scripted adapter). Routing, validation, middleware, the tool registry, the executor,
the orchestrator, persistence and the error envelope all run for real — which is the
point, because those are where the product's claims live.

This is the Phase 6 exit criterion expressed as a test: log in, ask, get a grounded
answer, reload the conversation. The security negatives are here too, because "the user
can see their answer" and "only that user can" are the same acceptance criterion.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.dependencies import get_llm_adapter
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMResponse
from app.domain.models.tools import ToolCall
from app.infrastructure.connectors.mock.policies import portfolio_for
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository
from app.main import create_app
from app.security.auth import require_auth_context

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")

QUESTION = "چند بیمه‌نامه دارم؟"


def _propose(name: str, arguments: dict | None = None) -> LLMResponse:
    return LLMResponse(
        message=ChatMessage(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id="call-1", name=name, arguments=arguments or {}),),
        ),
        model="fake/model",
    )


@pytest.fixture
def journey() -> Iterator[tuple[TestClient, dict[str, AuthContext], FakeLLMAdapter]]:
    application = create_app(Settings(_env_file=None))
    application.state.conversation_repository = InMemoryConversationRepository()

    actor: dict[str, AuthContext] = {"current": ALICE}
    llm = FakeLLMAdapter([text_response("شما سه بیمه‌نامه دارید.")])

    application.dependency_overrides[require_auth_context] = lambda: actor["current"]
    application.dependency_overrides[get_llm_adapter] = lambda: llm

    with TestClient(application) as client:
        yield client, actor, llm


def test_login_to_grounded_answer_to_reload(journey: tuple) -> None:
    client, _, llm = journey
    llm._responses = [  # noqa: SLF001 - test seam
        _propose("get_my_policy_count"),
        text_response("شما سه بیمه‌نامه دارید: دو فعال و یکی منقضی."),
    ]

    created = client.post("/conversations", json={})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    answer = client.post(
        f"/conversations/{conversation_id}/messages", json={"content": QUESTION}
    )
    assert answer.status_code == 200
    body = answer.json()

    # The answer exists and describes itself as grounded.
    assert body["answer"].startswith("شما سه بیمه‌نامه")
    assert [tool["tool"] for tool in body["tools_used"]] == ["get_my_policy_count"]
    assert body["tools_used"][0]["status"] == "ok"
    assert body["tools_used"][0]["latency_ms"] >= 0

    # Reload: the user's question and the answer are both there, and no tool row leaked
    # into the transcript (tool turns are audit data, not conversation).
    history = client.get(f"/conversations/{conversation_id}/messages").json()
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[0]["content"] == QUESTION
    assert history[1]["content"] == body["answer"]

    # The sidebar sees it, with a title derived from the first message.
    listed = client.get("/conversations").json()
    assert [item["id"] for item in listed] == [conversation_id]
    assert listed[0]["title"] == QUESTION


def test_the_turn_reports_a_failed_tool_without_failing_the_answer(journey: tuple) -> None:
    client, _, llm = journey
    foreign_id = portfolio_for(BOB.subject)[0]["policy_id"]
    llm._responses = [  # noqa: SLF001 - test seam
        _propose("get_policy_details", {"policy_id": foreign_id}),
        text_response("چنین بیمه‌نامه‌ای برای شما پیدا نشد."),
    ]
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "جزئیات بیمه‌نامه؟"}
    )

    assert response.status_code == 200
    assert response.json()["tools_used"][0]["status"] == "not_found"
    assert response.json()["answer"] == "چنین بیمه‌نامه‌ای برای شما پیدا نشد."


def test_the_model_cannot_name_another_user(journey: tuple) -> None:
    """The identity field is a validation error, so nothing is read for Bob."""
    client, _, llm = journey
    llm._responses = [  # noqa: SLF001 - test seam
        _propose("get_my_policy_count", {"user_id": BOB.subject}),
        text_response("اطلاعات کافی ندارم."),
    ]
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages", json={"content": QUESTION}
    )

    assert response.status_code == 200
    assert response.json()["tools_used"][0]["status"] == "invalid_request"


def test_another_user_cannot_read_the_conversation(journey: tuple) -> None:
    client, actor, _ = journey
    conversation_id = client.post("/conversations", json={"title": "مال آلیس"}).json()["id"]

    actor["current"] = BOB
    read = client.get(f"/conversations/{conversation_id}/messages")
    write = client.post(
        f"/conversations/{conversation_id}/messages", json={"content": QUESTION}
    )

    # Identical response for "not yours" and "does not exist": no existence oracle.
    assert read.status_code == 404
    assert write.status_code == 404
    assert read.json()["error"]["code"] == "conversation_not_found"


def test_a_persian_question_longer_than_the_limit_is_refused(journey: tuple) -> None:
    client, _, _ = journey
    conversation_id = client.post("/conversations", json={}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "ب" * 4001}
    )

    assert response.status_code == 422
