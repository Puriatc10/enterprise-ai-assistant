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
    """Base class for provider-neutral LLM failures.

    ``retryable`` is part of the contract, not an afterthought: Phase 1 showed the
    SDK happily retrying a hard `403 PERMISSION_DENIED` until the request budget
    ran out. The transport layer decides what is worth retrying, once, here.
    """

    retryable: bool = False


class LLMAuthError(LLMError):
    """Credential missing, invalid, or not entitled to this model.

    Covers `401`/`403` and provider "not available for this project" refusals.
    Never retried: repeating the request cannot fix entitlement.
    """

    retryable = False


class LLMTimeoutError(LLMError):
    """The provider did not answer within the allowed time.

    Retryable — a slow free-tier pool is a common cause, not a permanent state.
    """

    retryable = True


class LLMRateLimitError(LLMError):
    """Quota or rate limit reached (`429`). Retryable with backoff."""

    retryable = True


class LLMUnavailableError(LLMError):
    """Transport failure or provider-side error (`5xx`). Retryable."""

    retryable = True


class LLMInvalidResponseError(LLMError):
    """The provider answered with something we cannot normalize.

    Not retryable on its own: a malformed payload usually repeats. Callers may
    escalate to a fallback model instead.
    """

    retryable = False


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
