"""Repository contract tests — the same expectations against both adapters.

The scoping cases are the reason this file exists. A repository that returns another
caller's row is a data breach with a green test suite, so every adapter is asked the
same questions: can you see someone else's conversation, can you write into it, and
does the error tell the caller which of the two it was?
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.domain.interfaces.repository import ConversationNotFoundError, RepositoryError
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage
from app.infrastructure.persistence.memory import InMemoryConversationRepository
from app.infrastructure.persistence.supabase import SupabaseConversationRepository

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")
ALICE_OTHER_TENANT = AuthContext(subject="alice-id", tenant_id="tenant-b")

CONVERSATION_ROW = {
    "id": "c1",
    "title": "بیمه",
    "created_at": "2026-11-14T10:00:00Z",
    "updated_at": "2026-11-14T10:05:00Z",
}
MESSAGE_ROW = {
    "id": "m1",
    "role": "user",
    "content": "چند بیمه‌نامه دارم؟",
    "tool_call_id": None,
    "created_at": "2026-11-14T10:01:00Z",
}


# ===========================================================================
# In-memory adapter
# ===========================================================================


async def test_memory_create_then_get_roundtrip() -> None:
    repo = InMemoryConversationRepository()

    created = await repo.create(context=ALICE, title="بیمه")
    fetched = await repo.get(context=ALICE, conversation_id=created.id)

    assert fetched.id == created.id
    assert fetched.title == "بیمه"


async def test_memory_hides_another_users_conversation() -> None:
    repo = InMemoryConversationRepository()
    created = await repo.create(context=ALICE)

    with pytest.raises(ConversationNotFoundError):
        await repo.get(context=BOB, conversation_id=created.id)


async def test_memory_does_not_leak_existence_through_error_text() -> None:
    """Unknown id and someone else's id must produce an indistinguishable error."""
    repo = InMemoryConversationRepository()
    created = await repo.create(context=ALICE)

    with pytest.raises(ConversationNotFoundError) as unknown:
        await repo.get(context=ALICE, conversation_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(ConversationNotFoundError) as foreign:
        await repo.get(context=BOB, conversation_id=created.id)

    assert str(unknown.value) == str(foreign.value).replace(created.id, unknown.value.args[0])


async def test_memory_tenant_is_part_of_the_scope() -> None:
    """Same user id, different tenant: still not theirs."""
    repo = InMemoryConversationRepository()
    created = await repo.create(context=ALICE)

    with pytest.raises(ConversationNotFoundError):
        await repo.get(context=ALICE_OTHER_TENANT, conversation_id=created.id)


async def test_memory_listing_is_scoped_and_newest_first() -> None:
    now = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
    ticks = iter([now, now + timedelta(minutes=1), now + timedelta(minutes=2)])
    repo = InMemoryConversationRepository(clock=lambda: next(ticks))

    first = await repo.create(context=ALICE, title="first")
    second = await repo.create(context=ALICE, title="second")
    await repo.create(context=BOB, title="bob's")

    listed = await repo.list_recent(context=ALICE)

    assert [c.id for c in listed] == [second.id, first.id]


async def test_memory_append_and_read_messages_in_order() -> None:
    repo = InMemoryConversationRepository()
    created = await repo.create(context=ALICE)

    await repo.append_message(
        context=ALICE,
        conversation_id=created.id,
        message=ChatMessage(role="user", content="یک"),
    )
    await repo.append_message(
        context=ALICE,
        conversation_id=created.id,
        message=ChatMessage(role="assistant", content="دو"),
    )

    messages = await repo.list_messages(context=ALICE, conversation_id=created.id)

    assert [m.content for m in messages] == ["یک", "دو"]
    assert messages[0].as_chat_message().content == "یک"


async def test_memory_cannot_append_into_another_users_conversation() -> None:
    repo = InMemoryConversationRepository()
    created = await repo.create(context=ALICE)

    with pytest.raises(ConversationNotFoundError):
        await repo.append_message(
            context=BOB,
            conversation_id=created.id,
            message=ChatMessage(role="user", content="injected"),
        )


async def test_memory_appending_bumps_updated_at_like_the_db_trigger() -> None:
    now = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
    ticks = iter([now, now + timedelta(minutes=1), now + timedelta(minutes=5)])
    repo = InMemoryConversationRepository(clock=lambda: next(ticks))
    older = await repo.create(context=ALICE, title="older")
    newer = await repo.create(context=ALICE, title="newer")

    assert [c.id for c in await repo.list_recent(context=ALICE)] == [newer.id, older.id]

    await repo.append_message(
        context=ALICE, conversation_id=older.id, message=ChatMessage(role="user", content="x")
    )

    assert [c.id for c in await repo.list_recent(context=ALICE)] == [older.id, newer.id]


# ===========================================================================
# Supabase adapter (PostgREST over a mock transport — no network)
# ===========================================================================


class _Recorder:
    """Records outgoing requests and replies with a canned response."""

    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status
        self._payload = payload if payload is not None else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._payload)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _repo(recorder: _Recorder, *, token: str = "user-jwt") -> SupabaseConversationRepository:
    return SupabaseConversationRepository(
        base_url="https://project.supabase.co",
        api_key="sb_publishable_test",
        access_token=token,
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
    )


async def test_supabase_create_sends_owner_from_context_and_returns_the_row() -> None:
    recorder = _Recorder(status=201, payload=[CONVERSATION_ROW])

    conversation = await _repo(recorder).create(context=ALICE, title="بیمه")

    request = recorder.last
    assert request.method == "POST"
    assert str(request.url) == "https://project.supabase.co/rest/v1/conversations"
    body = httpx.Response(200, content=request.content).json()
    assert body == {"user_id": "alice-id", "tenant_id": "tenant-a", "title": "بیمه"}
    assert request.headers["prefer"] == "return=representation"
    assert conversation.id == "c1"
    assert conversation.updated_at.tzinfo is not None


async def test_supabase_forwards_the_callers_token_not_the_api_key() -> None:
    """RLS evaluates the bearer token; the publishable key is only the `apikey`."""
    recorder = _Recorder(payload=[CONVERSATION_ROW])

    await _repo(recorder, token="alice-jwt").get(context=ALICE, conversation_id="c1")

    request = recorder.last
    assert request.headers["authorization"] == "Bearer alice-jwt"
    assert request.headers["apikey"] == "sb_publishable_test"


async def test_supabase_get_treats_an_empty_result_as_not_found() -> None:
    recorder = _Recorder(payload=[])

    with pytest.raises(ConversationNotFoundError):
        await _repo(recorder).get(context=ALICE, conversation_id="c1")

    params = recorder.last.url.params
    assert params["id"] == "eq.c1"
    assert params["limit"] == "1"


async def test_supabase_403_from_rls_is_reported_as_not_found() -> None:
    """A permission refusal must not confirm that the row exists."""
    recorder = _Recorder(status=403, payload={"message": "permission denied"})

    with pytest.raises(ConversationNotFoundError):
        await _repo(recorder).get(context=BOB, conversation_id="c1")


async def test_supabase_server_error_is_a_repository_error() -> None:
    recorder = _Recorder(status=503, payload={"message": "unavailable"})

    with pytest.raises(RepositoryError) as excinfo:
        await _repo(recorder).list_recent(context=ALICE)

    assert not isinstance(excinfo.value, ConversationNotFoundError)


async def test_supabase_list_recent_asks_for_newest_first() -> None:
    recorder = _Recorder(payload=[CONVERSATION_ROW])

    conversations = await _repo(recorder).list_recent(context=ALICE, limit=5)

    params = recorder.last.url.params
    assert params["order"] == "updated_at.desc"
    assert params["limit"] == "5"
    assert [c.id for c in conversations] == ["c1"]


async def test_supabase_append_message_stamps_owner_and_returns_row() -> None:
    recorder = _Recorder(status=201, payload=[MESSAGE_ROW])

    stored = await _repo(recorder).append_message(
        context=ALICE,
        conversation_id="c1",
        message=ChatMessage(role="user", content="چند بیمه‌نامه دارم؟"),
    )

    request = recorder.last
    assert str(request.url).endswith("/rest/v1/messages")
    body = httpx.Response(200, content=request.content).json()
    assert body["conversation_id"] == "c1"
    assert body["user_id"] == "alice-id"
    assert body["tenant_id"] == "tenant-a"
    assert body["role"] == "user"
    assert body["tool_call_id"] is None
    assert stored.id == "m1"


async def test_supabase_list_messages_is_chronological() -> None:
    recorder = _Recorder(payload=[MESSAGE_ROW])

    messages = await _repo(recorder).list_messages(context=ALICE, conversation_id="c1")

    params = recorder.last.url.params
    assert params["conversation_id"] == "eq.c1"
    assert params["order"] == "created_at.asc"
    assert [m.id for m in messages] == ["m1"]
