"""CRM connector and tools, driven by a fake pool.

The point of these tests is the *controls*, not the SQL text: that every call opens a READ
ONLY transaction with a statement timeout, that identity comes from the context rather than
the operation parameters, that a list is bounded server-side, that the pipeline total ignores
closed deals and stays an integer, and that a database failure becomes a typed connector
status instead of an exception.

The second half covers the tool layer, because that is where the model touches this domain:
no tool takes an argument, an identity-shaped argument is a validation error, and a
connector refusal reaches the model as the right ``ToolStatus``.

The live-server proof is appended to ``tests/integration/test_postgres_live.py`` and runs in
CI against a real PostgreSQL service container.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.application.tools.crm import build_crm_registry
from app.application.tools.executor import ToolExecutor
from app.domain.interfaces.connector import (
    ConnectorHealth,
    ConnectorResult,
    ConnectorStatus,
    DataConnectorPort,
    FetchSpec,
)
from app.domain.models.auth import AuthContext
from app.domain.models.crm import CLOSED_STAGES, PUBLIC_ACCOUNT_FIELDS, PUBLIC_DEAL_FIELDS
from app.domain.models.tools import ToolCall, ToolStatus
from app.infrastructure.connectors.postgres.base import PostgresReadOnlyConnector
from app.infrastructure.connectors.postgres.crm import CrmConnector

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOT = AuthContext(subject="bot-id", tenant_id="tenant-a")

UPDATED_AT = datetime.fromisoformat("2026-01-12T10:15:00+03:30")
CREATED_AT = datetime.fromisoformat("2025-11-02T09:00:00+03:30")

#: A row as the database returns it: public columns plus the internals a query selects.
DEAL_ROW = {
    "id": "aaaaaaaa-0000-0000-0000-000000000001",
    "account_id": "bbbbbbbb-0000-0000-0000-000000000001",
    "auth_subject": "alice-id",
    "deal_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "title": "تمدید قرارداد پشتیبانی سالانه",
    "stage": "proposal",
    "amount_toman": 780_000_000,
    "currency": "IRT",
    "expected_close": "1404/12/15",
    "updated_at": UPDATED_AT,
    "internal_note": "a column that must never reach the model",
}

ACCOUNT_ROW = {
    "id": "bbbbbbbb-0000-0000-0000-000000000001",
    "auth_subject": "alice-id",
    "name": "شرکت داده‌پردازی آریا",
    "industry": "فناوری اطلاعات",
    "owner_name": "سارا محمدی",
    "created_at": CREATED_AT,
}

#: ``sum(bigint)`` comes back as ``Decimal`` — the connector must make it an int.
PIPELINE_ROW = {
    "open_deals": 3,
    "total_amount_toman": Decimal("2350000000"),
    "currency": "IRT",
}


class FakeTransaction:
    def __init__(self, log: list[tuple], readonly: bool) -> None:
        self._log = log
        self._readonly = readonly

    async def __aenter__(self) -> None:
        self._log.append(("begin", self._readonly))

    async def __aexit__(self, *_: object) -> None:
        self._log.append(("end", self._readonly))


class QueryCanceledError(Exception):
    """Same class name asyncpg raises when a statement exceeds statement_timeout."""


class FakeConnection:
    def __init__(
        self,
        *,
        deals: list[dict] | None = None,
        pipeline: dict | None = None,
        account: dict | None = None,
        fail: Exception | None = None,
    ) -> None:
        self._deals = deals or []
        self._pipeline = pipeline
        self._account = account
        self._fail = fail
        self._log: list[tuple] = []

    def attach(self, log: list[tuple]) -> FakeConnection:
        """Write into the caller's log, so the test can assert on what ran."""
        self._log = log
        return self

    def transaction(self, *, readonly: bool = False) -> FakeTransaction:
        self._log.append(("transaction", readonly))
        return FakeTransaction(self._log, readonly)

    async def execute(self, sql: str, *args: object) -> str:
        self._log.append(("execute", sql, args))
        return "SET"

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self._log.append(("fetch", sql, args))
        if self._fail:
            raise self._fail
        return self._deals

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        self._log.append(("fetchrow", sql, args))
        if self._fail:
            raise self._fail
        # The pipeline aggregate reads crm.deals; the account lookup reads only crm.accounts.
        return self._pipeline if "crm.deals" in sql else self._account

    async def fetchval(self, sql: str) -> int:
        self._log.append(("fetchval", sql))
        return 1


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.closed = False

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def _connector(connection: FakeConnection, **kwargs: Any) -> tuple[CrmConnector, list[tuple]]:
    log: list[tuple] = []
    connection.attach(log)
    pool = FakePool(connection)

    async def factory() -> FakePool:
        return pool

    connector = CrmConnector(dsn="postgresql://readonly@example/db", pool_factory=factory, **kwargs)
    return connector, log


def _fetches(log: list[tuple]) -> list[tuple]:
    return [entry for entry in log if entry[0] == "fetch"]


# -- connector contract ----------------------------------------------------
def test_the_connector_is_a_read_only_postgres_connector() -> None:
    connector, _ = _connector(FakeConnection())

    assert isinstance(connector, PostgresReadOnlyConnector)
    # The orchestrator only ever sees this port; a missing method would be a runtime surprise.
    assert isinstance(connector, DataConnectorPort)
    assert connector.name == "postgres-crm"
    assert connector.allowed_operations == {
        "list_deals",
        "summarize_pipeline",
        "account_by_subject",
    }


async def test_every_query_runs_read_only_with_a_statement_timeout() -> None:
    connector, log = _connector(FakeConnection(deals=[DEAL_ROW]), statement_timeout_ms=3_000)

    await connector.fetch(FetchSpec(operation="list_deals"), ALICE)

    assert ("transaction", True) in log, "queries must run in a READ ONLY transaction"
    timeout_calls = [
        entry for entry in log if entry[0] == "execute" and "statement_timeout" in entry[1]
    ]
    assert timeout_calls, "a statement timeout must be set inside the transaction"
    assert "3000" in timeout_calls[0][1]


async def test_the_row_limit_is_validated_and_bound_server_side() -> None:
    connector, log = _connector(FakeConnection(deals=[DEAL_ROW]), row_limit=7)

    await connector.fetch(FetchSpec(operation="list_deals"), ALICE)

    fetch = _fetches(log)[0]
    assert "LIMIT $2" in fetch[1]
    assert fetch[2] == ("alice-id", 7)


async def test_identity_is_the_first_parameter_and_never_a_parameter_of_the_spec() -> None:
    """A caller-supplied subject must be ignored: ``$1`` is the context's subject."""
    connector, log = _connector(FakeConnection(deals=[DEAL_ROW]))

    await connector.fetch(
        FetchSpec(operation="list_deals", params={"auth_subject": "bob-id", "subject": "bob-id"}),
        ALICE,
    )

    fetch = _fetches(log)[0]
    assert fetch[2][0] == "alice-id"
    assert "bob-id" not in fetch[2]
    assert "bob-id" not in fetch[1]


async def test_another_subjects_deals_are_indistinguishable_from_no_deals() -> None:
    """The database returns nothing for this subject; the connector reports an empty list."""
    connector, _ = _connector(FakeConnection(deals=[]))

    result = await connector.fetch(FetchSpec(operation="list_deals"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert result.data == {"deals": []}


async def test_deals_are_projected_to_public_fields_only() -> None:
    connector, _ = _connector(FakeConnection(deals=[DEAL_ROW]))

    result = await connector.fetch(FetchSpec(operation="list_deals"), ALICE)

    deal = result.data["deals"][0]
    assert set(deal) == set(PUBLIC_DEAL_FIELDS)
    assert "id" not in deal
    assert "account_id" not in deal
    assert "auth_subject" not in deal
    assert "internal_note" not in deal
    assert deal["amount_toman"] == 780_000_000
    assert deal["updated_at"] == UPDATED_AT.isoformat(), "timestamps leave as ISO strings"


async def test_the_pipeline_total_ignores_closed_deals_and_stays_an_integer() -> None:
    connector, log = _connector(FakeConnection(pipeline=PIPELINE_ROW))

    result = await connector.fetch(FetchSpec(operation="summarize_pipeline"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert result.data == {
        "open_deals": 3,
        "total_amount_toman": 2_350_000_000,
        "currency": "IRT",
    }
    assert isinstance(result.data["total_amount_toman"], int)

    fetchrow = [entry for entry in log if entry[0] == "fetchrow"][0]
    assert fetchrow[2] == ("alice-id",), "the aggregate is scoped to the caller too"
    for closed in CLOSED_STAGES:
        assert f"'{closed}'" in fetchrow[1], f"{closed} deals must be excluded from the total"
    assert "NOT IN" in fetchrow[1]


async def test_an_empty_pipeline_reports_zero_and_the_default_currency() -> None:
    """No open deals: ``sum`` is NULL, which must read as 0 toman, not as a missing answer."""
    pipeline = {"open_deals": 0, "total_amount_toman": None, "currency": None}
    connector, _ = _connector(FakeConnection(pipeline=pipeline))

    result = await connector.fetch(FetchSpec(operation="summarize_pipeline"), ALICE)

    assert result.data == {"open_deals": 0, "total_amount_toman": 0, "currency": "IRT"}


async def test_an_account_belonging_to_someone_else_is_not_found() -> None:
    """No existence oracle: scoped by auth_subject, so it looks like an account that is absent."""
    connector, log = _connector(FakeConnection(account=None))

    result = await connector.fetch(FetchSpec(operation="account_by_subject"), ALICE)

    assert result.status is ConnectorStatus.NOT_FOUND
    fetchrow = [entry for entry in log if entry[0] == "fetchrow"][0]
    assert fetchrow[2] == ("alice-id",)


async def test_the_account_is_projected_to_public_fields_only() -> None:
    connector, _ = _connector(FakeConnection(account=ACCOUNT_ROW))

    result = await connector.fetch(FetchSpec(operation="account_by_subject"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert set(result.data) == set(PUBLIC_ACCOUNT_FIELDS)
    assert result.data["created_at"] == CREATED_AT.isoformat()


async def test_unknown_operation_is_refused_before_touching_the_pool() -> None:
    connector, log = _connector(FakeConnection())

    result = await connector.fetch(FetchSpec(operation="execute_sql"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    assert log == []


async def test_a_query_string_in_the_spec_is_not_an_operation() -> None:
    """The allowlist is exact: SQL text is not an operation name."""
    connector, log = _connector(FakeConnection())

    result = await connector.fetch(
        FetchSpec(operation="select * from crm.accounts", params={"sql": "select 1"}),
        ALICE,
    )

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    assert log == []


async def test_missing_identity_is_refused() -> None:
    """Defensive: a context with no subject must never mean "show everything".

    ``AuthContext`` itself forbids an empty subject, so this stubs the shape to exercise the
    connector's own guard — the second line of defence.
    """
    connector, log = _connector(FakeConnection())

    result = await connector.fetch(
        FetchSpec(operation="list_deals"),
        SimpleNamespace(subject="", tenant_id="tenant-a"),  # type: ignore[arg-type]
    )

    assert result.status is ConnectorStatus.UNAUTHORIZED
    assert log == []


async def test_statement_timeout_is_reported_as_a_timeout() -> None:
    connector, _ = _connector(FakeConnection(fail=QueryCanceledError("canceled")))

    result = await connector.fetch(FetchSpec(operation="list_deals"), ALICE)

    assert result.status is ConnectorStatus.TIMEOUT


async def test_an_unexpected_database_error_becomes_an_upstream_error() -> None:
    failure = RuntimeError("relation crm.deals does not exist")
    connector, _ = _connector(FakeConnection(fail=failure))

    result = await connector.fetch(FetchSpec(operation="summarize_pipeline"), ALICE)

    assert result.status is ConnectorStatus.UPSTREAM_ERROR
    assert "relation" not in (result.message or "")
    assert "crm.deals" not in (result.message or "")


@pytest.mark.parametrize("row_limit", [0, -1, 101])
def test_row_limit_is_validated_at_construction(row_limit: int) -> None:
    with pytest.raises(ValueError, match="row_limit"):
        CrmConnector(dsn="postgresql://x", row_limit=row_limit)


@pytest.mark.parametrize("timeout_ms", [0, -5])
def test_statement_timeout_is_validated_at_construction(timeout_ms: int) -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        CrmConnector(dsn="postgresql://x", statement_timeout_ms=timeout_ms)


# -- the tool layer --------------------------------------------------------
class StubConnector:
    """A ``DataConnectorPort`` that answers from a dict, recording what it was asked."""

    name = "stub-crm"

    def __init__(self, results: dict[str, ConnectorResult] | None = None) -> None:
        self._results = results or {}
        self.calls: list[tuple[str, str]] = []

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(reachable=True)

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        self.calls.append((spec.operation, context.subject))
        return self._results.get(
            spec.operation,
            ConnectorResult(status=ConnectorStatus.NOT_FOUND, message="No account for this user."),
        )


def test_the_three_tools_are_registered_by_their_exact_names() -> None:
    registry = build_crm_registry(StubConnector())

    assert registry.names == ("crm_get_account", "crm_list_my_deals", "crm_summarize_my_pipeline")
    # No arguments anywhere: the model cannot name a user, an account or a deal.
    for spec in registry.specs_for(ALICE):
        assert spec.parameters.get("properties") == {}
        assert spec.parameters.get("additionalProperties") is False


async def test_a_tool_call_reads_the_callers_own_deals() -> None:
    connector = StubConnector(
        {
            "list_deals": ConnectorResult(
                status=ConnectorStatus.OK,
                data={
                    "deals": [
                        {
                            "deal_id": "d-1",
                            "title": "تمدید قرارداد پشتیبانی سالانه",
                            "stage": "proposal",
                            "amount_toman": 780_000_000,
                            "currency": "IRT",
                            "expected_close": "1404/12/15",
                            "updated_at": UPDATED_AT.isoformat(),
                        }
                    ]
                },
            )
        }
    )
    executor = ToolExecutor(registry=build_crm_registry(connector))

    record = await executor.execute(
        call=ToolCall(id="c1", name="crm_list_my_deals", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.OK
    assert record.result.data["deals"][0]["deal_id"] == "d-1"
    assert connector.calls == [("list_deals", "alice-id")]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("crm_list_my_deals", {"user_id": "bob-id"}),
        ("crm_summarize_my_pipeline", {"subject": "bob-id"}),
        ("crm_get_account", {"account_id": "bbbbbbbb-0000-0000-0000-000000000001"}),
    ],
)
async def test_an_identity_shaped_argument_is_a_validation_error(
    tool: str, arguments: dict[str, str]
) -> None:
    connector = StubConnector()
    executor = ToolExecutor(registry=build_crm_registry(connector))

    record = await executor.execute(
        call=ToolCall(id="c1", name=tool, arguments=arguments), context=ALICE
    )

    assert record.result.status is ToolStatus.INVALID_REQUEST
    assert connector.calls == [], "nothing may reach the data source"


async def test_a_missing_account_reaches_the_model_as_not_found() -> None:
    connector = StubConnector()  # answers NOT_FOUND for every operation
    executor = ToolExecutor(registry=build_crm_registry(connector))

    record = await executor.execute(
        call=ToolCall(id="c1", name="crm_get_account", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.NOT_FOUND
    assert record.result.message


async def test_the_pipeline_tool_returns_the_connectors_numbers() -> None:
    connector = StubConnector(
        {
            "summarize_pipeline": ConnectorResult(
                status=ConnectorStatus.OK,
                data={"open_deals": 3, "total_amount_toman": 2_350_000_000, "currency": "IRT"},
            )
        }
    )
    executor = ToolExecutor(registry=build_crm_registry(connector))

    record = await executor.execute(
        call=ToolCall(id="c1", name="crm_summarize_my_pipeline", arguments={}),
        context=BOT,
    )

    assert record.result.status is ToolStatus.OK
    assert record.result.data == {
        "open_deals": 3,
        "total_amount_toman": 2_350_000_000,
        "currency": "IRT",
    }
    assert connector.calls == [("summarize_pipeline", "bot-id")]
