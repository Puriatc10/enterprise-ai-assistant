"""Deterministic ``LLMPort`` for tests, and for running the app without a key.

Two uses, one class:

* **Tests** script exact responses (or exceptions) and inspect what was sent, so
  orchestration can be verified without network, keys, or latency.
* **Local runs** without a credential get a coherent but clearly-labelled answer
  instead of a crash — the app must boot and behave predictably with no secrets
  (`docs/architecture/ARCHITECTURE.md`).

It is deliberately dumb: no heuristics, no pretending to be a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.interfaces.llm import LLMError
from app.domain.models.conversation import ChatMessage, LLMRequest, LLMResponse


class FakeLLMAdapter:
    """Scripted ``LLMPort`` implementation.

    Args:
        responses: Responses (or exceptions) to return in order. The last entry is
            reused once the script is exhausted, which keeps multi-round tests
            (tool loop then final answer) terse.

    Attributes:
        requests: Every ``LLMRequest`` received, in order — the assertion surface
            for "did we send the right thing?" rather than "did the text match?".
    """

    def __init__(
        self,
        responses: Sequence[LLMResponse | LLMError] | None = None,
    ) -> None:
        self._responses: list[LLMResponse | LLMError] = list(responses or [])
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)

        if not self._responses:
            raise LLMError("FakeLLMAdapter has no scripted response.")

        # The last entry is reused, so multi-round tests stay terse.
        next_response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        if isinstance(next_response, LLMError):
            raise next_response
        return next_response


class ScriptedLLM(FakeLLMAdapter):
    """Alias kept for readability where intent matters more than mechanism."""


def text_response(content: str, *, model: str = "fake/scripted") -> LLMResponse:
    """Shorthand for a plain-text assistant response."""
    return LLMResponse(
        message=ChatMessage(role="assistant", content=content),
        model=model,
        finish_reason="stop",
    )
