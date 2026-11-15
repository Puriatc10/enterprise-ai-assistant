"""Two domains, two source technologies, one orchestrator.

The exit criterion for Phase 7 is not "a second connector exists" — it is that a question
needing data from two independent sources is answered in one turn, with both tool calls
visible, and that adding the domain required no change to `ChatService`, the registry or
the executor.

The support source here is a real SQLite file built from the committed seed SQL; the
insurance source is the mock connector. Nothing about the orchestration knows the
difference.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.application.orchestration.chat import ChatService
from app.application.tools.executor import ToolExecutor
from app.core.config import Settings
from app.core.dependencies import build_tool_registry
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMResponse
from app.domain.models.tools import ToolCall
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
SEED_SQL = Path(__file__).resolve().parents[1] / "data" / "support_demo.sql"


@pytest.fixture
def support_db(tmp_path: Path) -> Iterator[Path]:
    """A real SQLite database built from the committed seed SQL."""
    path = tmp_path / "support.sqlite3"
    script = SEED_SQL.read_text(encoding="utf-8")
    with sqlite3.connect(path) as connection:
        connection.executescript(script)
        connection.commit()
    yield path


def _settings(db_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        policies_source="mock",
        support_source="sqlite",
        support_sqlite_path=str(db_path),
        crm_source="disabled",
    )


def test_the_registry_composes_three_sources_into_one_allowlist(support_db: Path) -> None:
    setup = build_tool_registry(_settings(support_db))

    assert setup.domains == ("insurance", "support")
    names = set(setup.registry.names)
    assert {
        "get_my_policy_count",
        "get_my_policies",
        "get_policy_details",
        "get_policy_status",
        "support_count_my_tickets",
        "support_list_my_tickets",
        "support_get_ticket",
        "list_my_data_sources",
    } <= names


async def test_the_capability_tool_reflects_the_composed_domains(support_db: Path) -> None:
    setup = build_tool_registry(_settings(support_db))

    record = await ToolExecutor(registry=setup.registry).execute(
        call=ToolCall(id="c1", name="list_my_data_sources", arguments={}),
        context=ALICE,
    )

    assert record.result.status.value == "ok"
    assert record.result.data is not None
    sources = record.result.data["sources"]
    assert [source["id"] for source in sources] == ["insurance", "support"]
    support = next(source for source in sources if source["id"] == "support")
    assert "support_count_my_tickets" in support["tools"]


async def test_one_question_across_two_sources_is_answered_in_one_turn(
    support_db: Path,
) -> None:
    """The claim Phase 7 exists to prove."""
    setup = build_tool_registry(_settings(support_db))

    proposed = LLMResponse(
        message=ChatMessage(
            role="assistant",
            content="",
            tool_calls=(
                ToolCall(id="call-a", name="get_my_policy_count", arguments={}),
                ToolCall(id="call-b", name="support_count_my_tickets", arguments={}),
            ),
        ),
        model="fake/model",
    )
    llm = FakeLLMAdapter([proposed, text_response("سه بیمه‌نامه و دو تیکت باز دارید.")])
    repository = InMemoryConversationRepository()
    service = ChatService(
        repository=repository,
        llm=llm,
        tools=setup.registry,
        executor=ToolExecutor(registry=setup.registry),
    )

    result = await service.send_message(
        context=ALICE, message="چند بیمه‌نامه دارم و چند تیکت باز دارم؟"
    )

    assert result.answer == "سه بیمه‌نامه و دو تیکت باز دارید."
    assert [(item.tool, item.status) for item in result.tools_used] == [
        ("get_my_policy_count", "ok"),
        ("support_count_my_tickets", "ok"),
    ]

    # Both tool results reached the model on the second round, as data.
    tool_messages = [message for message in llm.requests[1].messages if message.role == "tool"]
    payloads = [json.loads(message.content) for message in tool_messages]
    assert payloads[0]["data"] == {"count": 3, "active_count": 2}
    assert payloads[1]["data"] == {"count": 4, "open_count": 2}

    # And the transcript stays a human conversation: two rows, no tool turns.
    assert len(repository.audit_records) == 2


async def test_a_disabled_domain_is_absent_rather_than_broken(support_db: Path) -> None:
    settings = Settings(
        _env_file=None,
        policies_source="mock",
        support_source="disabled",
        crm_source="disabled",
    )

    setup = build_tool_registry(settings)

    assert setup.domains == ("insurance",)
    assert "support_count_my_tickets" not in setup.registry.names
    assert "list_my_data_sources" in setup.registry.names
