"""Chat endpoints: create a conversation, send a turn, read history.

Every route depends on ``AuthContextDep`` — there is no anonymous path — and the
repository is built from that caller's token, so the database applies the same scope.

Routers translate HTTP ↔ application and nothing else: no scope checks, no SQL, no
prompt logic. When a dependency refuses (501/401) the request never reaches here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from app.application.orchestration.chat import ChatService, EmptyAnswerError
from app.application.orchestration.naming import LLMConversationNamer
from app.core.dependencies import (
    AuthContextDep,
    ConversationRepositoryDep,
    LLMPortDep,
    RateLimiterDep,
    ToolExecutorDep,
    ToolRegistryDep,
)
from app.domain.interfaces.llm import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import StreamEvent, StreamEventKind, Usage
from app.security.rate_limit import RateLimitExceeded, TurnRateLimiter

logger = logging.getLogger(__name__)


def _enforce_rate_limit(limiter: TurnRateLimiter, context: AuthContext) -> None:
    """Spend one turn from the caller's window, or refuse with the wait.

    Called before any model work: a limited caller must not cost a provider call.
    """
    decision = limiter.check(context.subject)
    if not decision.allowed:
        raise RateLimitExceeded(decision.retry_after_seconds)


router = APIRouter(prefix="/conversations", tags=["chat"])

MAX_MESSAGE_LENGTH = 4000


class CreateConversationRequest(BaseModel):
    title: str | None = Field(
        default=None, max_length=200, description="Optional; defaults to the first message."
    )


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class RenameConversationRequest(BaseModel):
    # Stripped and non-empty by the model, not by the handler: a blank title is the
    # caller's mistake and deserves a 422, not a stored empty string.
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ToolUsageResponse(BaseModel):
    tool: str
    status: str
    latency_ms: int


class SendMessageResponse(BaseModel):
    conversation_id: str
    answer: str
    model: str
    usage: Usage | None = None
    #: Present so the UI can show that the answer was grounded, and in what. Deliberately
    #: excludes arguments and results: the client needs provenance, not a copy of the data.
    tools_used: list[ToolUsageResponse] = Field(default_factory=list)
    #: Present when this turn named the conversation, so a client can update its list
    #: without a second request. ``None`` means "nothing changed", not "no title".
    conversation_title: str | None = None


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=201,
    summary="Start a conversation",
)
async def create_conversation(
    payload: CreateConversationRequest,
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
) -> ConversationResponse:
    conversation = await repository.create(context=context, title=payload.title)
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("", response_model=list[ConversationResponse], summary="List my conversations")
async def list_conversations(
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[
        datetime | None,
        Query(description="Keyset cursor: only conversations updated before this instant."),
    ] = None,
) -> list[ConversationResponse]:
    conversations = await repository.list_recent(context=context, limit=limit, before=before)
    return [
        ConversationResponse(
            id=item.id,
            title=item.title,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in conversations
    ]


@router.patch(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Rename a conversation",
)
async def rename_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
) -> ConversationResponse:
    conversation = await repository.rename(
        context=context, conversation_id=conversation_id, title=payload.title
    )
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete(
    "/{conversation_id}",
    status_code=204,
    response_class=Response,
    summary="Delete a conversation and its messages",
)
async def delete_conversation(
    conversation_id: str,
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
) -> Response:
    await repository.delete(context=context, conversation_id=conversation_id)
    return Response(status_code=204)


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageResponse],
    summary="Read a conversation's history",
)
async def list_messages(
    conversation_id: str,
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MessageResponse]:
    messages = await repository.list_messages(
        context=context, conversation_id=conversation_id, limit=limit
    )
    return [
        MessageResponse(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )
        for message in messages
    ]


@router.post(
    "/{conversation_id}/messages",
    response_model=SendMessageResponse,
    summary="Send a message and get the assistant's answer",
)
async def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
    llm: LLMPortDep,
    tools: ToolRegistryDep,
    executor: ToolExecutorDep,
    limiter: RateLimiterDep,
) -> SendMessageResponse:
    _enforce_rate_limit(limiter, context)
    service = ChatService(
        repository=repository,
        llm=llm,
        tools=tools,
        executor=executor,
        namer=LLMConversationNamer(llm),
    )
    result = await service.send_message(
        context=context,
        message=payload.content,
        conversation_id=conversation_id,
    )
    return SendMessageResponse(
        conversation_id=result.conversation_id,
        answer=result.answer,
        model=result.model,
        usage=result.usage,
        tools_used=[
            ToolUsageResponse(tool=item.tool, status=item.status, latency_ms=item.latency_ms)
            for item in result.tools_used
        ],
        conversation_title=result.title,
    )


# -- streaming -------------------------------------------------------------
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Render and most reverse proxies buffer responses by default, which would deliver
    # the whole answer at once and defeat the point.
    "X-Accel-Buffering": "no",
}


def _sse(event: StreamEvent) -> str:
    """One server-sent event: a named type plus a JSON payload."""
    return f"event: {event.kind.value}\ndata: {event.model_dump_json(exclude_none=True)}\n\n"


def _error_event(exc: Exception) -> StreamEvent:
    """Map a failure onto the same codes the HTTP handlers use.

    Once the first byte of a stream is sent the status line is fixed, so failures have
    to travel as data. Same taxonomy as `api/errors.py`, so a client has one set of
    codes to translate either way.
    """
    if isinstance(exc, LLMRateLimitError):
        return StreamEvent(
            kind=StreamEventKind.ERROR,
            code="llm_rate_limited",
            message="مدل در حال حاضر پاسخ‌گو نیست؛ چند لحظه بعد دوباره تلاش کنید.",
        )
    if isinstance(exc, LLMTimeoutError):
        return StreamEvent(
            kind=StreamEventKind.ERROR,
            code="llm_timeout",
            message="پاسخ مدل در زمان مجاز نرسید؛ دوباره تلاش کنید.",
        )
    if isinstance(exc, LLMAuthError):
        return StreamEvent(
            kind=StreamEventKind.ERROR,
            code="llm_not_configured",
            message="سرویس مدل پیکربندی نشده است.",
        )
    if isinstance(exc, EmptyAnswerError):
        return StreamEvent(
            kind=StreamEventKind.ERROR,
            code="empty_answer",
            message="پاسخی از مدل دریافت نشد.",
        )
    if isinstance(exc, LLMError):
        return StreamEvent(
            kind=StreamEventKind.ERROR,
            code="llm_error",
            message="خطا در ارتباط با مدل.",
        )
    return StreamEvent(
        kind=StreamEventKind.ERROR,
        code="internal_error",
        message="خطای غیرمنتظره رخ داد.",
    )


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Send a message and stream the answer",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_message(
    conversation_id: str,
    payload: SendMessageRequest,
    context: AuthContextDep,
    repository: ConversationRepositoryDep,
    llm: LLMPortDep,
    tools: ToolRegistryDep,
    executor: ToolExecutorDep,
    limiter: RateLimiterDep,
) -> StreamingResponse:
    _enforce_rate_limit(limiter, context)

    # Resolve before streaming: an unknown or foreign conversation must be a 404 status,
    # not an error event inside a 200 response.
    await repository.get(context=context, conversation_id=conversation_id)

    service = ChatService(
        repository=repository,
        llm=llm,
        tools=tools,
        executor=executor,
        namer=LLMConversationNamer(llm),
    )

    async def events() -> AsyncIterator[str]:
        try:
            async for event in service.stream_message(
                context=context,
                message=payload.content,
                conversation_id=conversation_id,
            ):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001 - the status line is already sent
            logger.warning(
                "stream failed conversation=%s error=%s", conversation_id, type(exc).__name__
            )
            yield _sse(_error_event(exc))

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)
