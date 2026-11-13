"""Conversation and generation models.

These are application-owned shapes. They are deliberately close to, but not
identical with, any provider's wire format: the LLM adapter translates in both
directions, so nothing above ``infrastructure/llm`` sees a vendor payload.
"""

from __future__ import annotations

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
    temperature: float = 0.0
    max_tokens: int | None = None
