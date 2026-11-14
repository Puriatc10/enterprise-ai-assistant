"""LiteLLM implementation of ``LLMPort``.

Four rules earned in Phase 1 (`docs/analysis/PHASE1-LLM.md` §6) are encoded here,
not just documented:

1. **Sampling parameters are optional.** Gemini 3.x rejects ``temperature`` before
   the request is even sent, so it is included only when the caller asked for a
   specific value.
2. **The adapter owns retries.** The SDK's default retry loop turned a hard ``403``
   into a seven-minute hang, so SDK retries stay off and only *retryable* errors
   are retried here, with capped exponential backoff.
3. **One error taxonomy.** Provider exceptions become ``LLMError`` subclasses, so
   orchestration can explain a failure without leaking provider JSON upward.
4. **No provider types escape, and no import at module scope.** ``litellm`` is
   imported only by the composition root, and the completion callable is injected
   — so tests and CI run without the optional extra installed, and no test needs
   network.

The adapter never logs prompts or responses: tool results may contain customer
data, and logs are not the place for it (`docs/architecture/SECURITY.md` §9).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.domain.interfaces.llm import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.domain.models.conversation import ChatMessage, LLMRequest, LLMResponse, Usage
from app.domain.models.tools import ToolCall, ToolSpec

logger = logging.getLogger(__name__)

#: Signature of the injected provider call: ``litellm.acompletion`` in production,
#: an async fake in tests.
CompletionFn = Callable[..., Awaitable[Any]]

_SANITIZED_ERROR_LIMIT = 200


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times, and how patiently, a retryable failure is retried."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff for the given (1-based) attempt number."""
        return min(self.base_delay_seconds * 2 ** (attempt - 1), self.max_delay_seconds)


class LiteLLMAdapter:
    """Provider-neutral completion over LiteLLM.

    Args:
        completion: Async provider callable (injected; see module docstring).
        model: LiteLLM model id, e.g. ``openrouter/vendor/model:free``.
        api_key: Credential for the provider. Passed explicitly because settings
            come from ``.env`` and never reach ``os.environ``.
        timeout_seconds: Per-attempt timeout.
        retry: Retry policy. Retries are the adapter's job, never the SDK's.
        sleep: Injectable sleep, so tests do not actually wait.
    """

    def __init__(
        self,
        *,
        completion: CompletionFn,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 45.0,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._completion = completion
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    # -- port -------------------------------------------------------------
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion, retrying only what is worth retrying."""
        payload = self._build_payload(request)

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                raw = await self._completion(**payload)
                return self._from_raw(raw)
            except Exception as exc:
                error = classify_error(exc)
                if not error.retryable or attempt == self._retry.max_attempts:
                    raise error from exc

                delay = self._retry.delay_for(attempt)
                logger.warning(
                    "llm attempt %d/%d failed (%s); retrying in %.2fs",
                    attempt,
                    self._retry.max_attempts,
                    error.__class__.__name__,
                    delay,
                )
                await self._sleep(delay)

        # Unreachable: the loop either returns or raises.
        raise LLMError("LLM call did not complete")

    # -- request mapping --------------------------------------------------
    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._to_message(message) for message in request.messages],
            "timeout": self._timeout_seconds,
            # Belt and braces: even if the installed LiteLLM defaults change, this
            # adapter never lets the SDK run its own retry loop.
            "num_retries": 0,
        }

        if self._api_key:
            payload["api_key"] = self._api_key

        tools = [self._to_tool(spec) for spec in request.tools]
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Omitted unless explicitly requested — see rule 1.
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        return payload

    @staticmethod
    def _to_message(message: ChatMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id or "",
                "content": message.content,
            }

        if message.role == "assistant" and message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ],
            }

        return {"role": message.role, "content": message.content}

    @staticmethod
    def _to_tool(spec: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters or {"type": "object", "properties": {}},
            },
        }

    # -- response mapping -------------------------------------------------
    def _from_raw(self, raw: Any) -> LLMResponse:
        try:
            choice = raw.choices[0]
            message = choice.message
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError("Provider response contained no usable choice.") from exc

        raw_calls = getattr(message, "tool_calls", None) or []
        tool_calls = tuple(self._to_tool_call(call) for call in raw_calls)

        return LLMResponse(
            message=ChatMessage(
                role="assistant",
                content=getattr(message, "content", None) or "",
                tool_calls=tool_calls,
            ),
            model=getattr(raw, "model", None) or self._model,
            usage=self._to_usage(getattr(raw, "usage", None)),
            finish_reason=getattr(choice, "finish_reason", None),
        )

    @staticmethod
    def _to_usage(raw: Any) -> Usage | None:
        if raw is None:
            return None
        prompt = getattr(raw, "prompt_tokens", None)
        completion = getattr(raw, "completion_tokens", None)
        if prompt is None and completion is None:
            return None
        return Usage(prompt_tokens=prompt, completion_tokens=completion)

    @staticmethod
    def _to_tool_call(raw: Any) -> ToolCall:
        """Normalize a provider tool call, refusing to invent missing pieces."""

        def pick(source: Any, key: str) -> Any:
            if isinstance(source, dict):
                return source.get(key)
            return getattr(source, key, None)

        call_id = pick(raw, "id")
        function = pick(raw, "function")
        name = pick(function, "name") if function is not None else None
        raw_arguments = pick(function, "arguments") if function is not None else None

        if not call_id or not name:
            raise LLMInvalidResponseError("Tool call arrived without an id or name.")

        return ToolCall(
            id=str(call_id),
            name=str(name),
            arguments=_parse_arguments(raw_arguments),
        )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Parse tool-call arguments without fabricating a default.

    An unparsable argument payload is a real defect: turning it into ``{}`` would
    make a tool with required fields fail later with a confusing validation error
    instead of here, where the cause is obvious.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError("Tool call arguments were not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise LLMInvalidResponseError("Tool call arguments were not a JSON object.")
        return parsed
    raise LLMInvalidResponseError("Tool call arguments had an unsupported type.")


def _sanitize(text: str) -> str:
    """Collapse a provider error into one short, log-safe line."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _SANITIZED_ERROR_LIMIT:
        collapsed = collapsed[: _SANITIZED_ERROR_LIMIT - 1] + "…"
    return collapsed


def classify_error(exc: BaseException) -> LLMError:
    """Map any provider exception onto the port's error taxonomy.

    Status codes are read defensively (``status_code``/``code``) and the class name
    is used as a fallback, so this works for LiteLLM's exception classes without
    importing them — which is what keeps the module importable without the
    optional extra installed.
    """
    if isinstance(exc, LLMError):
        return exc

    detail = _sanitize(str(exc))
    name = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None

    if "timeout" in name or "timed out" in detail.lower():
        return LLMTimeoutError(detail or "Provider timed out.")

    if status is not None:
        if status == 429:
            return LLMRateLimitError(detail)
        if status in (401, 403):
            return LLMAuthError(detail)
        if status >= 500:
            return LLMUnavailableError(detail)
        if 400 <= status < 500:
            # Malformed request or model-level refusal: repeating it cannot help.
            return LLMError(detail)

    if "connection" in name or "connect" in name:
        return LLMUnavailableError(detail)

    return LLMError(detail or type(exc).__name__)
