"""Streaming turns over SSE.

Two things must hold at once: the user sees text before the turn finishes, and the
server persists exactly what it streamed — once. A stream that shows a nice answer and
stores a different one is worse than no streaming at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.dependencies import get_llm_adapter
from app.domain.interfaces.llm import LLMRateLimitError
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMResponse
from app.domain.models.tools import ToolCall
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository
from app.main import create_app
from app.security.auth import require_auth_context

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")


@pytest.fixture
def streaming_client() -> Iterator[tuple[TestClient, FakeLLMAdapter]]:
    application = create_app(Settings(_env_file=None))
    application.state.conversation_repository = InMemoryConversationRepository()
    llm = FakeLLMAdapter([text_response("شما سه بیمه‌نامه دارید.")])

    application.dependency_overrides[require_auth_context] = lambda: ALICE
    application.dependency_overrides[get_llm_adapter] = lambda: llm

    with TestClient(application) as client:
        yield client, llm


def _read_events(client: TestClient, conversation_id: str, content: str) -> list[dict]:
    """Collect the SSE payloads for one turn."""
    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/stream",
        json={"content": content},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payloads = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    return payloads


def test_stream_emits_tokens_then_done_and_persists_once(streaming_client: tuple) -> None:
    client, _ = streaming_client
    conversation_id = client.post("/conversations", json={}).json()["id"]

    events = _read_events(client, conversation_id, "چند بیمه‌نامه دارم؟")

    kinds = [event["kind"] for event in events]
    assert kinds.count("token") >= 2, "text must arrive in deltas"

    # `done` settles the answer; a `title` event may follow it, because a conversation
    # that was just created is named after its answer — never before it.
    done = next(event for event in events if event["kind"] == "done")
    assert kinds.index("done") > 0
    assert all(kind == "title" for kind in kinds[kinds.index("done") + 1 :])

    streamed = "".join(event["text"] for event in events if event["kind"] == "token")
    assert done["answer"] == "شما سه بیمه‌نامه دارید."
    assert streamed == done["answer"]

    # Persisted exactly once, and identical to what was shown.
    history = client.get(f"/conversations/{conversation_id}/messages").json()
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[1]["content"] == done["answer"]


def test_stream_reports_the_tool_it_used(streaming_client: tuple) -> None:
    client, llm = streaming_client
    llm._responses = [  # noqa: SLF001 - test seam
        LLMResponse(
            message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(id="call-1", name="get_my_policy_count", arguments={}),),
            ),
            model="fake/model",
        ),
        text_response("سه بیمه‌نامه دارید."),
    ]
    conversation_id = client.post("/conversations", json={}).json()["id"]

    events = _read_events(client, conversation_id, "چند بیمه‌نامه دارم؟")

    tool_events = [event for event in events if event["kind"] == "tool"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool"]["tool"] == "get_my_policy_count"
    assert tool_events[0]["tool"]["status"] == "ok"
    assert [e["kind"] for e in events if e["kind"] != "title"][-1] == "done"


def test_stream_maps_a_rate_limit_to_an_error_event(streaming_client: tuple) -> None:
    """The status line is already sent, so the failure has to travel as data."""
    client, llm = streaming_client
    llm._responses = [LLMRateLimitError("429")]  # noqa: SLF001 - test seam
    conversation_id = client.post("/conversations", json={}).json()["id"]

    events = _read_events(client, conversation_id, "سلام")

    assert len(events) == 1
    assert events[0]["kind"] == "error"
    assert events[0]["code"] == "llm_rate_limited"
    assert events[0]["message"]


def test_unknown_conversation_is_a_404_before_streaming_starts(streaming_client: tuple) -> None:
    client, _ = streaming_client

    response = client.post(
        "/conversations/00000000-0000-0000-0000-000000000000/messages/stream",
        json={"content": "سلام"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"
