"""Phase 4 exit criterion: two sources answer through one orchestrator, unchanged.

The claim being tested is not "the Postgres connector works" (that is
``test_postgres_connector.py`` and the CI integration job). It is that swapping the
adapter changes *only* the data — the registry, the executor and ``ChatService`` are the
same objects, and the number the model receives comes from whichever source is live.
"""

from __future__ import annotations

import json

import pytest

from app.application.orchestration.chat import ChatService
from app.application.tools.executor import ToolExecutor
from app.application.tools.policies import build_policy_registry
from app.core.config import Settings
from app.core.dependencies import build_policy_connector
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMResponse
from app.domain.models.tools import ToolCall
from app.infrastructure.connectors.mock.policies import MockPolicyConnector
from app.infrastructure.connectors.postgres.policies import PostgresPolicyConnector
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class _Connection:
    """Minimal asyncpg-shaped connection: enough for a count query."""

    def __init__(self, count: int, active: int) -> None:
        self._count = (count, active)

    def transaction(self, *, readonly: bool = False) -> _Transaction:
        return _Transaction()

    async def execute(self, sql: str, *args: object) -> str:
        return "SET"

    async def fetchrow(self, sql: str, *args: object) -> dict:
        return {"total": self._count[0], "active": self._count[1]}


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def acquire(self) -> _Pool:
        return self

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


def _proposal() -> LLMResponse:
    return LLMResponse(
        message=ChatMessage(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id="c1", name="get_my_policy_count", arguments={}),),
        ),
        model="fake/model",
    )


def _postgres_connector(count: int, active: int) -> PostgresPolicyConnector:
    pool = _Pool(_Connection(count, active))

    async def factory() -> _Pool:
        return pool

    return PostgresPolicyConnector(dsn="postgresql://readonly@example/db", pool_factory=factory)


async def _ask(connector) -> dict:
    """Run one turn and return the payload the model was given for its tool call."""
    llm = FakeLLMAdapter([_proposal(), text_response("پاسخ")])
    registry = build_policy_registry(connector)
    service = ChatService(
        repository=InMemoryConversationRepository(),
        llm=llm,
        tools=registry,
        executor=ToolExecutor(registry=registry),
    )

    await service.send_message(context=ALICE, message="چند بیمه‌نامه دارم؟")

    tool_message = next(message for message in llm.requests[1].messages if message.role == "tool")
    return json.loads(tool_message.content)


@pytest.mark.parametrize(
    ("connector", "expected"),
    [
        (MockPolicyConnector(), {"count": 3, "active_count": 2}),
        (_postgres_connector(7, 5), {"count": 7, "active_count": 5}),
        (_postgres_connector(0, 0), {"count": 0, "active_count": 0}),
    ],
    ids=["mock", "postgres-with-data", "postgres-empty"],
)
async def test_the_same_orchestrator_answers_from_either_source(connector, expected: dict) -> None:
    payload = await _ask(connector)

    assert payload["status"] == "ok"
    assert payload["data"] == expected


def test_the_factory_selects_the_connector_from_settings() -> None:
    assert isinstance(build_policy_connector(Settings(policies_source="mock")), MockPolicyConnector)

    postgres = build_policy_connector(
        Settings(
            policies_source="postgres",
            connector_postgres_url="postgresql://readonly@example/db",
        )
    )
    assert isinstance(postgres, PostgresPolicyConnector)


def test_postgres_source_without_a_dsn_fails_loudly() -> None:
    """Silently falling back to synthetic data would answer from the wrong source."""
    with pytest.raises(RuntimeError, match="CONNECTOR_POSTGRES_URL"):
        build_policy_connector(Settings(policies_source="postgres"))
