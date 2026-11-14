"""Chat endpoints: create a conversation, send a turn, read history.

Every route depends on ``AuthContextDep`` — there is no anonymous path — and the
repository is built from that caller's token, so the database applies the same scope.

Routers translate HTTP ↔ application and nothing else: no scope checks, no SQL, no
prompt logic. When a dependency refuses (501/401) the request never reaches here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.application.orchestration.chat import ChatService
from app.core.dependencies import (
    AuthContextDep,
    ConversationRepositoryDep,
    LLMPortDep,
    ToolExecutorDep,
    ToolRegistryDep,
)
from app.domain.models.conversation import Usage

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


class SendMessageResponse(BaseModel):
    conversation_id: str
    answer: str
    model: str
    usage: Usage | None = None


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
) -> list[ConversationResponse]:
    conversations = await repository.list_recent(context=context, limit=limit)
    return [
        ConversationResponse(
            id=item.id,
            title=item.title,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in conversations
    ]


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
) -> SendMessageResponse:
    service = ChatService(repository=repository, llm=llm, tools=tools, executor=executor)
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
    )
