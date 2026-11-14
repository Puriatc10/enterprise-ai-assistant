"""Contract tests for the LLM adapter and its error taxonomy.

Everything here runs offline: the provider call is an injected async fake, so the
SDK's optional extra is not required and no test can make a network call. What is
tested is exactly what Phase 1 found to be fragile — parameter mapping, retry
behaviour, and which errors are allowed to escape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.domain.interfaces.llm import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.domain.models.conversation import ChatMessage, LLMRequest
from app.domain.models.tools import ToolCall, ToolSpec
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.llm.litellm_adapter import LiteLLMAdapter, RetryPolicy, classify_error

# ---------------------------------------------------------------------------
# Minimal stand-ins for the provider's response objects. They mimic the shape the
# adapter reads (attributes, not dicts), which is what LiteLLM returns.
# ---------------------------------------------------------------------------


@dataclass
class _Function:
    name: str
    arguments: str | dict[str, Any] | None


@dataclass
class _ToolCall:
    id: str
    function: _Function


@dataclass
class _Message:
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str | None = "stop"


@dataclass
class _Usage:
    prompt_tokens: int | None = 10
    completion_tokens: int | None = 5


@dataclass
class _RawResponse:
    choices: list[_Choice]
    model: str = "test/model"
    usage: _Usage | None = None


def _raw(
    content: str = "ok",
    tool_calls: list[_ToolCall] | None = None,
    usage: _Usage | None = None,
) -> _RawResponse:
    return _RawResponse(
        choices=[_Choice(message=_Message(content=content, tool_calls=tool_calls))],
        usage=_Usage() if usage is None else usage,
    )


class _ProviderError(Exception):
    """Provider exception with a status code, like LiteLLM's own classes."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class _TimeoutError(Exception):
    """Named to mimic a provider timeout exception."""


@dataclass
class _Recorder:
    """Injected provider call: records kwargs, replays a script, records sleeps."""

    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    delays: list[float] = field(default_factory=list)
    sleep_seconds: float = 0.0

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else _raw()
        if isinstance(item, BaseException):
            raise item
        return item

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)


def _adapter(recorder: _Recorder, **kwargs: Any) -> LiteLLMAdapter:
    return LiteLLMAdapter(
        completion=recorder,
        model="openrouter/test/model:free",
        api_key="test-key",
        sleep=recorder.sleep,
        **kwargs,
    )


def _request(**kwargs: Any) -> LLMRequest:
    return LLMRequest(
        messages=kwargs.pop("messages", (ChatMessage(role="user", content="سلام"),)),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Request mapping
# ---------------------------------------------------------------------------


async def test_maps_user_and_system_messages() -> None:
    recorder = _Recorder()
    request = _request(
        messages=(
            ChatMessage(role="system", content="be brief"),
            ChatMessage(role="user", content="چند بیمه‌نامه دارم؟"),
        )
    )

    await _adapter(recorder).complete(request)

    assert recorder.calls[0]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "چند بیمه‌نامه دارم؟"},
    ]


async def test_maps_assistant_tool_calls_and_tool_results() -> None:
    recorder = _Recorder()
    request = _request(
        messages=(
            ChatMessage(role="user", content="چند؟"),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(id="c1", name="get_my_policy_count", arguments={}),),
            ),
            ChatMessage(role="tool", content='{"count": 3}', tool_call_id="c1"),
        )
    )

    await _adapter(recorder).complete(request)

    messages = recorder.calls[0]["messages"]
    assert messages[1]["tool_calls"][0]["id"] == "c1"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "get_my_policy_count"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {}
    assert messages[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"count": 3}'}


async def test_maps_tool_specs_and_requests_automatic_tool_choice() -> None:
    recorder = _Recorder()
    spec = ToolSpec(
        name="get_my_policy_count",
        description="Count the caller's policies.",
        parameters={"type": "object", "properties": {}},
    )

    await _adapter(recorder).complete(_request(tools=(spec,)))

    call = recorder.calls[0]
    assert call["tools"][0] == {
        "type": "function",
        "function": {
            "name": "get_my_policy_count",
            "description": "Count the caller's policies.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    assert call["tool_choice"] == "auto"


async def test_tools_are_omitted_when_none_are_offered() -> None:
    recorder = _Recorder()

    await _adapter(recorder).complete(_request())

    assert "tools" not in recorder.calls[0]
    assert "tool_choice" not in recorder.calls[0]


async def test_sampling_parameters_are_omitted_unless_requested() -> None:
    """Gemini 3.x rejects `temperature`; the adapter must not send it unasked."""
    recorder = _Recorder()

    await _adapter(recorder).complete(_request())
    await _adapter(recorder).complete(_request(temperature=0.2, max_tokens=64))

    assert "temperature" not in recorder.calls[0]
    assert "max_tokens" not in recorder.calls[0]
    assert recorder.calls[1]["temperature"] == 0.2
    assert recorder.calls[1]["max_tokens"] == 64


async def test_passes_credential_explicitly_and_disables_sdk_retries() -> None:
    recorder = _Recorder()

    await _adapter(recorder).complete(_request())

    assert recorder.calls[0]["api_key"] == "test-key"
    # The SDK's own retry loop is what turned a 403 into a multi-minute hang.
    assert recorder.calls[0]["num_retries"] == 0
    assert recorder.calls[0]["timeout"] == 45.0


# ---------------------------------------------------------------------------
# Response mapping
# ---------------------------------------------------------------------------


async def test_normalizes_text_response_with_usage() -> None:
    recorder = _Recorder(script=[_raw("سلام!", None)])

    result = await _adapter(recorder).complete(_request())

    assert result.message.content == "سلام!"
    assert result.message.tool_calls == ()
    assert result.model == "test/model"
    assert result.usage is not None
    assert result.usage.prompt_tokens == 10
    assert result.finish_reason == "stop"


async def test_normalizes_tool_call_with_json_arguments() -> None:
    raw = _raw(
        content=None,
        tool_calls=[
            _ToolCall(id="c9", function=_Function("get_policy_status", '{"policy_id": "PL-1"}'))
        ],
    )
    recorder = _Recorder(script=[raw])

    result = await _adapter(recorder).complete(_request())

    assert result.message.tool_calls == (
        ToolCall(id="c9", name="get_policy_status", arguments={"policy_id": "PL-1"}),
    )


async def test_unparsable_tool_arguments_are_rejected_not_defaulted() -> None:
    """Silently becoming `{}` would hide the defect until a validation error later."""
    raw = _raw(
        tool_calls=[_ToolCall(id="c1", function=_Function("get_policy_status", "{not json"))]
    )
    recorder = _Recorder(script=[raw])

    with pytest.raises(LLMInvalidResponseError):
        await _adapter(recorder).complete(_request())

    assert len(recorder.calls) == 1, "a malformed payload must not be retried"


async def test_tool_call_without_a_name_is_rejected() -> None:
    raw = _raw(tool_calls=[_ToolCall(id="c1", function=_Function("", "{}"))])
    recorder = _Recorder(script=[raw])

    with pytest.raises(LLMInvalidResponseError):
        await _adapter(recorder).complete(_request())


async def test_response_without_choices_is_rejected() -> None:
    recorder = _Recorder(script=[_RawResponse(choices=[])])

    with pytest.raises(LLMInvalidResponseError):
        await _adapter(recorder).complete(_request())


# ---------------------------------------------------------------------------
# Retry policy and error taxonomy
# ---------------------------------------------------------------------------


async def test_auth_failure_is_never_retried() -> None:
    recorder = _Recorder(script=[_ProviderError("no free tier for this project", 403)])

    with pytest.raises(LLMAuthError):
        await _adapter(recorder).complete(_request())

    assert len(recorder.calls) == 1
    assert recorder.delays == []


async def test_rate_limit_is_retried_with_backoff_then_raised() -> None:
    recorder = _Recorder(script=[_ProviderError("429 slow down", 429) for _ in range(3)])

    with pytest.raises(LLMRateLimitError):
        await _adapter(
            recorder, retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.5)
        ).complete(_request())

    assert len(recorder.calls) == 3
    assert recorder.delays == [0.5, 1.0]


async def test_transient_failure_then_success_returns_the_answer() -> None:
    recorder = _Recorder(script=[_ProviderError("bad gateway", 502), _raw("بله")])

    result = await _adapter(recorder).complete(_request())

    assert result.message.content == "بله"
    assert len(recorder.calls) == 2


async def test_server_error_is_classified_as_unavailable() -> None:
    recorder = _Recorder(script=[_ProviderError("boom", 500)])

    with pytest.raises(LLMUnavailableError):
        await _adapter(recorder, retry=RetryPolicy(max_attempts=1)).complete(_request())


async def test_timeout_is_retryable() -> None:
    recorder = _Recorder(script=[_TimeoutError("Request timed out."), _raw("ok")])

    result = await _adapter(recorder).complete(_request())

    assert result.message.content == "ok"


async def test_client_error_is_not_retried() -> None:
    recorder = _Recorder(script=[_ProviderError("bad request", 400)])

    with pytest.raises(LLMError) as excinfo:
        await _adapter(recorder).complete(_request())

    assert not isinstance(excinfo.value, LLMRateLimitError)
    assert len(recorder.calls) == 1


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_ProviderError("x", 401), LLMAuthError),
        (_ProviderError("x", 403), LLMAuthError),
        (_ProviderError("x", 429), LLMRateLimitError),
        (_ProviderError("x", 503), LLMUnavailableError),
        (_TimeoutError("timed out"), LLMTimeoutError),
        (ConnectionError("connection refused"), LLMUnavailableError),
        (ValueError("something odd"), LLMError),
    ],
)
def test_error_classification_table(exc: BaseException, expected: type[LLMError]) -> None:
    assert isinstance(classify_error(exc), expected)


def test_error_messages_are_single_line_and_bounded() -> None:
    error = classify_error(_ProviderError("line one\nline two\n" + "x" * 500, 500))

    assert "\n" not in str(error)
    assert len(str(error)) <= 200


# ---------------------------------------------------------------------------
# The fake adapter's own contract
# ---------------------------------------------------------------------------


async def test_fake_adapter_replays_script_and_records_requests() -> None:
    fake = FakeLLMAdapter([text_response("first"), text_response("last")])
    request = _request()

    first = await fake.complete(request)
    second = await fake.complete(request)
    third = await fake.complete(request)

    assert [first.message.content, second.message.content, third.message.content] == [
        "first",
        "last",
        "last",
    ]
    assert fake.requests == [request, request, request]


async def test_fake_adapter_raises_scripted_errors() -> None:
    fake = FakeLLMAdapter([LLMAuthError("no credential")])

    with pytest.raises(LLMAuthError):
        await fake.complete(_request())
