"""Conversation and generation models.

These are application-owned shapes. They are deliberately close to, but not
identical with, any provider's wire format: the LLM adapter translates in both
directions, so nothing above ``infrastructure/llm`` sees a vendor payload.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models.tools import ToolCall, ToolSpec

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """One turn in a conversation, in provider-neutral form."""

    role: Role
    content: str = ""
    # Set when the assistant asks for tools in this turn.
    tool_calls: tuple[ToolCall, ...] = ()
    # Set on a 'tool' message: which call this content answers.
    tool_call_id: str | None = None


class Usage(BaseModel):
    """Token accounting, when the provider reports it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMResponse(BaseModel):
    """A model response, normalized.

    ``tool_calls`` non-empty means the model wants tools executed before it can
    answer; the orchestrator decides whether it may have them.
    """

    message: ChatMessage = Field(description="Assistant message (text and/or tool calls).")
    model: str = Field(description="Model identifier that produced the response.")
    usage: Usage | None = None
    finish_reason: str | None = None


class LLMRequest(BaseModel):
    """Everything the orchestrator sends to the provider port."""

    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    #: ``None`` means "do not send it". Phase 1 found that Gemini 3.x rejects
    #: ``temperature`` outright, so the port cannot promise it is supported; the
    #: adapter maps or drops it per provider.
    temperature: float | None = None
    max_tokens: int | None = None


class LLMStreamChunk(BaseModel):
    """One increment of a streaming completion.

    ``text`` is a delta to append. ``response`` is set exactly once, on the final
    chunk, carrying the same shape a non-streaming call returns — so the tool loop can
    decide what comes next without issuing a second request.
    """

    text: str = ""
    response: LLMResponse | None = None


class StreamEventKind(StrEnum):
    """What the browser is told while a turn is in flight."""

    TOKEN = "token"
    TOOL = "tool"
    DONE = "done"
    ERROR = "error"


class StreamEvent(BaseModel):
    """A server-sent event for a streaming turn.

    Deliberately narrow: text, one tool outcome, the finished answer, or an error. The
    client renders exactly these and invents nothing, which is why ``done.answer`` is
    authoritative even though the client already has the tokens.
    """

    kind: StreamEventKind
    text: str | None = None
    tool: ToolUsage | None = None
    answer: str | None = None
    conversation_id: str | None = None
    #: Set on ``done`` when this turn named the conversation. The client applies it to its
    #: own list instead of refetching — a refetch races the write and can return the old,
    #: untitled row.
    title: str | None = None
    message: str | None = None
    code: str | None = None


class ToolUsage(BaseModel):
    """One executed tool call, as reported back to the client.

    Carries the tool name, the outcome and the duration — never the arguments or the
    result. The UI's job is to show that an answer was grounded in real data, not to
    replay what the user asked about.
    """

    tool: str
    status: str
    latency_ms: int


class Conversation(BaseModel):
    """A stored chat thread.

    Deliberately carries no owner fields: the repository already scoped every read
    and write to the caller, so re-exposing ``user_id`` here would tempt callers to
    compare it themselves — the check that must never be re-implemented ad hoc.
    """

    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class StoredMessage(BaseModel):
    """A persisted turn, with the identity the database assigned it."""

    id: str
    role: Role
    content: str = ""
    tool_call_id: str | None = None
    created_at: datetime

    def as_chat_message(self) -> ChatMessage:
        """Project back into the provider-facing shape, dropping storage fields."""
        return ChatMessage(role=self.role, content=self.content, tool_call_id=self.tool_call_id)
