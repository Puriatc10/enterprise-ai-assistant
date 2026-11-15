"""The tool loop: proposal → execution → grounded answer.

The fake adapter lets each test assert what the model *saw* on the second round,
which is where grounding actually happens: if the tool result is not in the message
list, the answer is decoration.
"""

from __future__ import annotations

import json

from app.application.orchestration.chat import PARTIAL_ANSWER_FALLBACK, ChatService
from app.application.tools.executor import ToolExecutor
from app.application.tools.policies import build_policy_registry
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMResponse
from app.domain.models.tools import ToolCall
from app.infrastructure.connectors.mock.policies import MockPolicyConnector, portfolio_for
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")


def _proposal(name: str, arguments: dict | None = None, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        message=ChatMessage(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments or {}),),
        ),
        model="fake/model",
    )


def _service(
    responses: list, *, max_tool_rounds: int = 3
) -> tuple[ChatService, FakeLLMAdapter, InMemoryConversationRepository]:
    llm = FakeLLMAdapter(responses)
    repository = InMemoryConversationRepository()
    registry = build_policy_registry(MockPolicyConnector())
    service = ChatService(
        repository=repository,
        llm=llm,
        tools=registry,
        executor=ToolExecutor(registry=registry),
        max_tool_rounds=max_tool_rounds,
    )
    return service, llm, repository


async def test_tools_are_offered_on_the_first_round() -> None:
    service, llm, _ = _service([text_response("باشه")])

    await service.send_message(context=ALICE, message="سلام")

    offered = {spec.name for spec in llm.requests[0].tools}
    assert offered == {
        "get_my_policy_count",
        "get_my_policies",
        "get_policy_details",
        "get_policy_status",
    }


async def test_a_tool_call_is_executed_and_its_result_reaches_the_model() -> None:
    service, llm, repository = _service(
        [_proposal("get_my_policy_count"), text_response("شما ۳ بیمه‌نامه دارید.")]
    )

    result = await service.send_message(context=ALICE, message="چند بیمه‌نامه دارم؟")

    assert result.answer == "شما ۳ بیمه‌نامه دارید."
    assert len(llm.requests) == 2

    # The second round must carry the tool result as data, keyed to the call id.
    tool_messages = [m for m in llm.requests[1].messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "c1"
    payload = json.loads(tool_messages[0].content)
    assert payload == {"status": "ok", "data": {"count": 3, "active_count": 2}}

    # And the execution is audited, with a hash rather than the arguments.
    audit = repository.audit_records
    assert len(audit) == 1
    assert audit[0]["tool_name"] == "get_my_policy_count"
    assert audit[0]["status"] == "ok"
    assert len(str(audit[0]["arguments_hash"])) == 64


async def test_a_not_found_tool_result_is_explained_to_the_model() -> None:
    bob_policy = portfolio_for(BOB.subject)[0]["policy_id"]
    service, llm, repository = _service(
        [
            _proposal("get_policy_details", {"policy_id": bob_policy}),
            text_response("چنین بیمه‌نامه‌ای برای شما پیدا نشد."),
        ]
    )

    await service.send_message(context=ALICE, message="جزئیات این بیمه‌نامه؟")

    tool_message = next(m for m in llm.requests[1].messages if m.role == "tool")
    payload = json.loads(tool_message.content)
    assert payload["status"] == "not_found"
    assert "data" not in payload
    assert repository.audit_records[0]["status"] == "not_found"


async def test_the_round_limit_yields_a_stated_partial_answer_not_a_loop() -> None:
    """A model that keeps asking for tools must not cost an unbounded number of calls."""
    service, llm, repository = _service([_proposal("get_my_policy_count")], max_tool_rounds=2)

    result = await service.send_message(context=ALICE, message="چند بیمه‌نامه دارم؟")

    assert result.answer == PARTIAL_ANSWER_FALLBACK
    assert len(llm.requests) == 2
    assert len(repository.audit_records) == 2


async def test_no_tools_are_offered_when_no_registry_is_configured() -> None:
    llm = FakeLLMAdapter([text_response("باشه")])
    service = ChatService(repository=InMemoryConversationRepository(), llm=llm)

    await service.send_message(context=ALICE, message="سلام")

    assert llm.requests[0].tools == ()


async def test_the_turn_reports_which_tools_ran() -> None:
    """The client shows provenance, so the turn must describe it without the data."""
    service, _, _ = _service([_proposal("get_my_policy_count"), text_response("۳ بیمه‌نامه")])

    result = await service.send_message(context=ALICE, message="چند بیمه‌نامه دارم؟")

    assert len(result.tools_used) == 1
    usage = result.tools_used[0]
    assert usage.tool == "get_my_policy_count"
    assert usage.status == "ok"
    assert usage.latency_ms >= 0


async def test_answers_without_tools_report_an_empty_tool_list() -> None:
    service, _, _ = _service([text_response("سلام! چطور کمکت کنم؟")])

    result = await service.send_message(context=ALICE, message="سلام")

    assert result.tools_used == ()
