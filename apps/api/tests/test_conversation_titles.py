"""Conversation titles: the model's suggestion, the fallback, and who may overwrite what.

The title is the only part of the product that is pure presentation, so the rules are
narrow on purpose: suggested once, best effort, never over a user's own rename.
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.orchestration.chat import ChatService, _title_from
from app.application.orchestration.naming import (
    LLMConversationNamer,
    clean_title,
)
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMRequest, LLMResponse
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
QUESTION = "چند بیمه‌نامه فعال دارم؟"


class StubNamer:
    """Returns a fixed suggestion, and remembers what it was asked."""

    def __init__(self, title: str | None) -> None:
        self._title = title
        self.calls: list[str] = []

    async def suggest(self, text: str) -> str | None:
        self.calls.append(text)
        return self._title


class FailingNamer:
    async def suggest(self, text: str) -> str | None:
        raise RuntimeError("provider said no")


def _service(
    *,
    namer: object = None,
    llm: FakeLLMAdapter | None = None,
) -> tuple[ChatService, InMemoryConversationRepository]:
    repository = InMemoryConversationRepository()
    service = ChatService(
        repository=repository,
        llm=llm or FakeLLMAdapter([text_response("شما دو بیمه‌نامه فعال دارید.")]),
        namer=namer,  # type: ignore[arg-type]
    )
    return service, repository


async def _title_of(repository: InMemoryConversationRepository, conversation_id: str) -> str | None:
    conversation = await repository.get(context=ALICE, conversation_id=conversation_id)
    return conversation.title


async def test_the_suggestion_becomes_the_title() -> None:
    namer = StubNamer("بیمه‌نامه‌های فعال من")
    service, repository = _service(namer=namer)

    result = await service.send_message(context=ALICE, message=QUESTION)

    assert await _title_of(repository, result.conversation_id) == "بیمه‌نامه‌های فعال من"
    # Handed back with the answer so the client never has to refetch to see it.
    assert result.title == "بیمه‌نامه‌های فعال من"
    assert namer.calls == [QUESTION]


async def test_without_a_suggestion_the_first_message_names_the_thread() -> None:
    service, repository = _service(namer=StubNamer(None))

    result = await service.send_message(context=ALICE, message=QUESTION)

    assert await _title_of(repository, result.conversation_id) == _title_from(QUESTION)


async def test_a_failing_namer_still_answers_and_still_names_the_thread() -> None:
    service, repository = _service(namer=FailingNamer())

    result = await service.send_message(context=ALICE, message=QUESTION)

    assert result.answer == "شما دو بیمه‌نامه فعال دارید."
    assert await _title_of(repository, result.conversation_id) == _title_from(QUESTION)


async def test_a_user_title_is_never_overwritten_by_a_later_turn() -> None:
    service, repository = _service(namer=StubNamer("پیشنهاد مدل"))
    created = await repository.create(context=ALICE, title="عنوان خودم")

    await service.send_message(context=ALICE, message=QUESTION, conversation_id=created.id)

    assert await _title_of(repository, created.id) == "عنوان خودم"


async def test_naming_runs_once_not_once_per_turn() -> None:
    namer = StubNamer("بیمه‌نامه‌های فعال من")
    service, repository = _service(namer=namer)

    first = await service.send_message(context=ALICE, message=QUESTION)
    await service.send_message(
        context=ALICE, message="و تیکت‌هایم؟", conversation_id=first.conversation_id
    )

    assert len(namer.calls) == 1


async def test_streaming_sends_the_title_as_its_own_event_after_done() -> None:
    """The answer must not wait for the title; the title must still arrive."""
    namer = StubNamer("بیمه‌نامه‌های فعال من")
    service, repository = _service(namer=namer)

    order: list[str] = []
    announced: str | None = None
    conversation_id = ""
    async for event in service.stream_message(context=ALICE, message=QUESTION):
        order.append(event.kind.value)
        if event.kind.value == "done":
            conversation_id = event.conversation_id or ""
        if event.kind.value == "title":
            announced = event.text

    assert conversation_id
    # `done` first, so the answer settles without waiting on a second model call.
    assert order.index("done") < order.index("title")
    assert announced == "بیمه‌نامه‌های فعال من"
    assert await _title_of(repository, conversation_id) == "بیمه‌نامه‌های فعال من"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("بیمه‌نامه‌های فعال من", "بیمه‌نامه‌های فعال من"),
        ("«بیمه‌نامه‌های فعال من»", "بیمه‌نامه‌های فعال من"),
        ("عنوان: بیمه‌نامه‌های فعال من", "بیمه‌نامه‌های فعال من"),
        ("Title: Active policies", "Active policies"),
        ("بیمه‌نامه‌ها.\nتوضیح اضافه", "بیمه‌نامه‌ها"),
        ("   ", None),
        ("", None),
        (None, None),
        ("یک دو سه چهار پنج شش هفت هشت نه", None),
    ],
)
def test_clean_title_accepts_titles_and_rejects_prose(
    raw: str | None, expected: str | None
) -> None:
    assert clean_title(raw) == expected


class HangingLLM:
    """A provider that never answers — the case the timeout exists for."""

    async def complete(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - awaited
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    async def stream(self, request: LLMRequest):  # pragma: no cover - unused
        raise AssertionError("unreachable")
        yield  # noqa: E501


async def test_a_slow_provider_times_out_instead_of_holding_the_turn() -> None:
    namer = LLMConversationNamer(HangingLLM(), timeout_seconds=0.05)  # type: ignore[arg-type]

    assert await namer.suggest("چند بیمه‌نامه دارم؟") is None


async def test_the_namer_sends_a_small_tool_free_request() -> None:
    llm = FakeLLMAdapter(
        [LLMResponse(message=ChatMessage(role="assistant", content="x"), model="fake")]
    )
    namer = LLMConversationNamer(llm)

    await namer.suggest("چند بیمه‌نامه دارم؟")

    request = llm.requests[-1]
    assert request.tools == ()
    # 200, not 32: measured truncation at 32 left the title cut mid-word.
    assert request.max_tokens is not None and request.max_tokens <= 256
    assert len(request.messages) == 2
