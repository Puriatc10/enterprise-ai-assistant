"""Orchestration behaviour, verified without a provider or a database.

The fake adapter records every request, so these tests assert *what was asked* — which
history, which system prompt — rather than matching answer strings.
"""

from __future__ import annotations

import pytest

from app.application.orchestration.chat import (
    SYSTEM_PROMPT,
    ChatService,
    EmptyAnswerError,
    _title_from,
)
from app.domain.interfaces.llm import LLMAuthError
from app.domain.interfaces.repository import ConversationNotFoundError
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")


def _service(llm: FakeLLMAdapter) -> tuple[ChatService, InMemoryConversationRepository]:
    repository = InMemoryConversationRepository()
    return ChatService(repository=repository, llm=llm), repository


async def test_first_turn_creates_a_conversation_and_persists_both_messages() -> None:
    service, repository = _service(FakeLLMAdapter([text_response("پاسخ من")]))

    result = await service.send_message(context=ALICE, message="چند بیمه‌نامه دارم؟")

    assert result.answer == "پاسخ من"
    stored = await repository.list_messages(context=ALICE, conversation_id=result.conversation_id)
    assert [(m.role, m.content) for m in stored] == [
        ("user", "چند بیمه‌نامه دارم؟"),
        ("assistant", "پاسخ من"),
    ]


async def test_the_system_prompt_and_history_are_sent_to_the_model() -> None:
    llm = FakeLLMAdapter([text_response("اول"), text_response("دوم")])
    service, _ = _service(llm)

    first = await service.send_message(context=ALICE, message="سؤال یک")
    await service.send_message(
        context=ALICE, message="سؤال دو", conversation_id=first.conversation_id
    )

    messages = llm.requests[1].messages
    assert messages[0].role == "system"
    assert messages[0].content == SYSTEM_PROMPT
    assert [(m.role, m.content) for m in messages[1:]] == [
        ("user", "سؤال یک"),
        ("assistant", "اول"),
        ("user", "سؤال دو"),
    ]


async def test_persian_only_rule_is_in_the_system_prompt() -> None:
    """Phase 1 measured foreign-script leakage; the prompt must pin the language."""
    assert "فارسی" in SYSTEM_PROMPT
    assert "غیرفارسی" in SYSTEM_PROMPT


async def test_the_title_comes_from_the_first_message() -> None:
    service, repository = _service(FakeLLMAdapter([text_response("باشه")]))

    result = await service.send_message(context=ALICE, message="وضعیت بیمه‌نامه من چیست؟")

    conversation = await repository.get(context=ALICE, conversation_id=result.conversation_id)
    assert conversation.title == "وضعیت بیمه‌نامه من چیست؟"


async def test_another_users_conversation_is_not_found() -> None:
    service, _ = _service(FakeLLMAdapter([text_response("باشه")]))
    created = await service.send_message(context=ALICE, message="سلام")

    with pytest.raises(ConversationNotFoundError):
        await service.send_message(
            context=BOB, message="سلام", conversation_id=created.conversation_id
        )


async def test_a_blank_message_is_rejected_before_anything_is_stored() -> None:
    service, repository = _service(FakeLLMAdapter([text_response("باشه")]))

    with pytest.raises(ValueError):
        await service.send_message(context=ALICE, message="   ")

    assert await repository.list_recent(context=ALICE) == []


async def test_an_empty_answer_is_not_persisted() -> None:
    """A blank assistant turn would replay as a broken conversation forever."""
    service, repository = _service(FakeLLMAdapter([text_response("   ")]))

    with pytest.raises(EmptyAnswerError):
        await service.send_message(context=ALICE, message="سلام")

    conversations = await repository.list_recent(context=ALICE)
    stored = await repository.list_messages(context=ALICE, conversation_id=conversations[0].id)
    assert [m.role for m in stored] == ["user"], "only the user turn survives"


async def test_the_user_turn_survives_a_provider_failure() -> None:
    """Persistence order is deliberate: what was asked is recorded first."""
    service, repository = _service(FakeLLMAdapter([LLMAuthError("no entitlement")]))

    with pytest.raises(LLMAuthError):
        await service.send_message(context=ALICE, message="سؤال من")

    conversations = await repository.list_recent(context=ALICE)
    stored = await repository.list_messages(context=ALICE, conversation_id=conversations[0].id)
    assert [(m.role, m.content) for m in stored] == [("user", "سؤال من")]
    assert isinstance(stored[0].as_chat_message(), ChatMessage)


def test_long_titles_are_truncated_on_a_word_boundary() -> None:
    title = _title_from("یک متن بسیار طولانی دربارهٔ بیمه‌نامه‌های من که باید کوتاه شود", limit=20)

    assert len(title) <= 20
    assert not title.endswith(" ")
