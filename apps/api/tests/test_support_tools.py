"""Support tools over the real SQLite connector, driven through the real executor.

The connector is exercised unmodified — same class, same file, same read-only URI — so
these tests cover the path production takes rather than a hand-built connector. The
question they answer is the product claim: the model may ask *what* it wants, and the
backend still only shows it the caller's own tickets.

Refusals are tested before successes, because a successful read of someone else's ticket
would mean the design is wrong, not that a feature is missing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.application.tools.executor import ToolExecutor
from app.application.tools.support import build_support_registry
from app.domain.models.auth import AuthContext
from app.domain.models.tools import ToolCall, ToolStatus
from app.infrastructure.connectors.sqlite.support import SqliteSupportConnector

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")

TOOL_NAMES = (
    "support_count_my_tickets",
    "support_get_ticket",
    "support_list_my_tickets",
)

FIXTURE_SQL = """
CREATE TABLE support_tickets (
    customer_ref TEXT NOT NULL,
    ticket_id    TEXT NOT NULL,
    subject      TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL,
    priority     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (customer_ref, ticket_id)
);

INSERT INTO support_tickets VALUES
    ('alice-id', 'TKT-A1', 'خطا در ورود', 'ورود ناموفق است.', 'open', 'high',
     '2026-01-10T09:00:00+03:30', '2026-01-20T09:00:00+03:30'),
    ('alice-id', 'TKT-A2', 'فاکتور رسمی', 'فاکتور لازم دارم.', 'open', 'normal',
     '2026-01-11T09:00:00+03:30', '2026-01-19T09:00:00+03:30'),
    ('alice-id', 'TKT-A3', 'تغییر شماره', 'شماره تغییر کرده است.', 'closed', 'low',
     '2026-01-12T09:00:00+03:30', '2026-01-18T09:00:00+03:30'),
    ('bob-id',   'TKT-B1', 'بازنشانی رمز', 'رمز را فراموش کردم.', 'open', 'high',
     '2026-01-14T09:00:00+03:30', '2026-01-16T09:00:00+03:30');
"""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "support.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(FIXTURE_SQL)
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def executor(db_path: Path) -> ToolExecutor:
    return ToolExecutor(registry=build_support_registry(SqliteSupportConnector(path=str(db_path))))


async def _call(executor: ToolExecutor, context: AuthContext, name: str, **arguments: object):
    return await executor.execute(
        call=ToolCall(id="c1", name=name, arguments=dict(arguments)), context=context
    )


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_the_registry_offers_exactly_the_three_support_tools(db_path: Path) -> None:
    registry = build_support_registry(SqliteSupportConnector(path=str(db_path)))

    assert registry.names == TOOL_NAMES


def test_every_tool_is_read_only_and_bounded(db_path: Path) -> None:
    registry = build_support_registry(SqliteSupportConnector(path=str(db_path)))

    for name in registry.names:
        tool = registry.get(name)
        assert tool is not None
        assert tool.timeout_seconds == 8.0
        assert tool.result_model is not None, "a result model is what catches a bad payload"
        assert tool.required_scopes == frozenset()
        # The description is how the model routes, so it must say when to use it.
        assert "Use" in tool.description


def test_tool_argument_schemas_forbid_extra_fields(db_path: Path) -> None:
    registry = build_support_registry(SqliteSupportConnector(path=str(db_path)))

    for name in registry.names:
        tool = registry.get(name)
        assert tool is not None
        schema = tool.spec().parameters
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False


def test_no_tool_takes_an_identity_argument(db_path: Path) -> None:
    """The strongest statement of the design: identity is not part of any interface."""
    registry = build_support_registry(SqliteSupportConnector(path=str(db_path)))

    for name in registry.names:
        tool = registry.get(name)
        assert tool is not None
        assert set(tool.spec().parameters.get("properties", {})) <= {"ticket_id"}


def test_composes_with_the_policy_registry(db_path: Path) -> None:
    """Domains merge by name, and a collision would fail at startup."""
    from app.application.tools.policies import build_policy_registry
    from app.infrastructure.connectors.mock.policies import MockPolicyConnector

    merged = build_policy_registry(MockPolicyConnector()).compose(
        build_support_registry(SqliteSupportConnector(path=str(db_path)))
    )

    assert set(TOOL_NAMES) <= set(merged.names)
    assert "get_my_policies" in merged


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


async def test_count_tool_answers_for_the_caller(executor: ToolExecutor) -> None:
    record = await _call(executor, ALICE, "support_count_my_tickets")

    assert record.result.status is ToolStatus.OK
    assert record.result.data == {"count": 3, "open_count": 2}


async def test_count_tool_differs_per_caller(executor: ToolExecutor) -> None:
    record = await _call(executor, BOB, "support_count_my_tickets")

    assert record.result.data == {"count": 1, "open_count": 1}


async def test_list_tool_returns_the_callers_tickets_only(executor: ToolExecutor) -> None:
    record = await _call(executor, ALICE, "support_list_my_tickets")

    assert record.result.status is ToolStatus.OK
    tickets = record.result.data["tickets"]
    assert {ticket["ticket_id"] for ticket in tickets} == {"TKT-A1", "TKT-A2", "TKT-A3"}
    assert all("customer_ref" not in ticket for ticket in tickets)
    assert all(
        set(ticket)
        == {
            "ticket_id",
            "subject",
            "status",
            "priority",
            "created_at",
            "updated_at",
        }
        for ticket in tickets
    )


async def test_get_ticket_returns_the_full_ticket(executor: ToolExecutor) -> None:
    record = await _call(executor, ALICE, "support_get_ticket", ticket_id="TKT-A1")

    assert record.result.status is ToolStatus.OK
    assert record.result.data == {
        "ticket_id": "TKT-A1",
        "subject": "خطا در ورود",
        "status": "open",
        "priority": "high",
        "created_at": "2026-01-10T09:00:00+03:30",
        "updated_at": "2026-01-20T09:00:00+03:30",
        "description": "ورود ناموفق است.",
    }


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_another_users_ticket_is_not_found_through_the_tool(
    executor: ToolExecutor,
) -> None:
    record = await _call(executor, ALICE, "support_get_ticket", ticket_id="TKT-B1")

    assert record.result.status is ToolStatus.NOT_FOUND
    assert record.result.data is None


async def test_an_identity_shaped_argument_is_a_validation_error(
    executor: ToolExecutor,
) -> None:
    """``extra="forbid"`` is what stops the model from naming another customer."""
    record = await _call(executor, ALICE, "support_list_my_tickets", user_id="bob-id")

    assert record.result.status is ToolStatus.INVALID_REQUEST
    assert "user_id" in (record.result.message or "")


async def test_a_customer_ref_argument_is_also_rejected(executor: ToolExecutor) -> None:
    """The column name must be no more usable than a 'user_id' alias."""
    record = await _call(executor, ALICE, "support_get_ticket", customer_ref="bob-id")

    assert record.result.status is ToolStatus.INVALID_REQUEST
    assert "customer_ref" in (record.result.message or "")


async def test_get_ticket_requires_a_ticket_id(executor: ToolExecutor) -> None:
    record = await _call(executor, ALICE, "support_get_ticket")

    assert record.result.status is ToolStatus.INVALID_REQUEST


async def test_a_ticket_id_of_the_wrong_shape_is_refused(executor: ToolExecutor) -> None:
    record = await _call(executor, ALICE, "support_get_ticket", ticket_id="'; DROP TABLE x;--")

    assert record.result.status is ToolStatus.INVALID_REQUEST


async def test_an_unknown_tool_name_is_refused(executor: ToolExecutor) -> None:
    record = await _call(executor, ALICE, "support_run_sql", query="select 1")

    assert record.result.status is ToolStatus.INVALID_REQUEST


async def test_a_missing_database_is_an_upstream_error_not_a_crash(tmp_path: Path) -> None:
    """The assistant must be able to say "support data is unavailable", truthfully."""
    executor = ToolExecutor(
        registry=build_support_registry(
            SqliteSupportConnector(path=str(tmp_path / "absent.sqlite3"))
        )
    )

    record = await _call(executor, ALICE, "support_count_my_tickets")

    assert record.result.status is ToolStatus.UPSTREAM_ERROR
    assert "missing" in (record.result.message or "")
