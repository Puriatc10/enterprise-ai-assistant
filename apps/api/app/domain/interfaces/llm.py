"""The LLM port.

Everything above ``infrastructure/llm`` talks to this protocol and nothing else.
No ``litellm`` import, no provider request/response type, and no provider-specific
error may appear in a signature here — that is what makes the provider
replaceable by configuration.

Phase 0 declares the contract; Phase 2 implements it (LiteLLM adapter plus a
fake adapter for tests).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models.conversation import LLMRequest, LLMResponse


class LLMError(Exception):
    """Base class for provider-neutral LLM failures."""


class LLMTimeoutError(LLMError):
    """The provider did not answer within the allowed time."""


class LLMRateLimitError(LLMError):
    """The provider rejected the call for quota/rate reasons."""


class LLMInvalidResponseError(LLMError):
    """The provider answered with something we cannot normalize."""


@runtime_checkable
class LLMPort(Protocol):
    """Provider-neutral completion with optional tool declarations."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion.

        Implementations must:
        * apply a timeout (never hang the request),
        * translate the provider's tool-call representation into ``ToolCall``,
        * translate provider errors into ``LLMError`` subclasses,
        * never log prompts or responses at info level.

        Raises:
            LLMTimeoutError: the provider exceeded the timeout.
            LLMRateLimitError: quota or rate limit reached.
            LLMInvalidResponseError: the response could not be normalized.
        """
        ...
