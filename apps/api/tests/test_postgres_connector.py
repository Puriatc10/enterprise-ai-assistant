"""PostgreSQL connector, driven by a fake pool.

The point of these tests is the *controls*, not the SQL: that every call opens a READ
ONLY transaction with a statement timeout, that identity comes from the context rather
than the operation parameters, and that a database failure becomes a typed connector
status instead of an exception.

The live-server proof lives in ``tests/integration/test_postgres_live.py`` and runs in
CI against a real PostgreSQL service container.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.interfaces.connector import ConnectorStatus, FetchSpec
from app.domain.models.auth import AuthContext
from app.infrastructure.connectors.postgres.policies import PostgresPolicyConnector

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")

POLICY_ROW = {
    "policy_id": "POL-A-1",
    "type": "درمان تکمیلی",
    "status": "فعال",
    "insurer": "بیمه دی",
    "start_date": "1404/03/01",
    "end_date": "1405/03/01",
    "premium_toman": 4_800_000,
    # A column that must never reach the model, even if a query selects it.
    "national_id": "0012345678",
    "customer_ref": "alice-id",
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
        rows: list[dict] | None = None,
        *,
        row: dict | None = None,
        count: tuple[int, int] = (3, 2),
        fail: Exception | None = None,
    ) -> None:
        self._rows = rows or []
        self._row = row
        self._count = count
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
        return self._rows

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        self._log.append(("fetchrow", sql, args))
        if self._fail:
            raise self._fail
        if "count(*)" in sql:
            return {"total": self._count[0], "active": self._count[1]}
        return self._row

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


def _connector(connection: FakeConnection, **kwargs) -> tuple[PostgresPolicyConnector, list[tuple]]:
    log: list[tuple] = []
    connection.attach(log)
    pool = FakePool(connection)

    async def factory() -> FakePool:
        return pool

    connector = PostgresPolicyConnector(
        dsn="postgresql://readonly@example/db", pool_factory=factory, **kwargs
    )
    return connector, log


async def test_every_query_runs_read_only_with_a_statement_timeout() -> None:
    connector, log = _connector(FakeConnection([]), statement_timeout_ms=2_500)

    await connector.fetch(FetchSpec(operation="count_policies"), ALICE)

    assert ("transaction", True) in log, "queries must run in a READ ONLY transaction"
    timeout_calls = [
        entry for entry in log if entry[0] == "execute" and "statement_timeout" in entry[1]
    ]
    assert timeout_calls, "a statement timeout must be set inside the transaction"
    assert "2500" in timeout_calls[0][1]


async def test_count_returns_totals_from_the_database() -> None:
    connector, log = _connector(FakeConnection([], count=(3, 2)))

    result = await connector.fetch(FetchSpec(operation="count_policies"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert result.data == {"count": 3, "active_count": 2}
    fetchrow = [entry for entry in log if entry[0] == "fetchrow"][0]
    assert fetchrow[2][0] == "alice-id", "identity is bound from the verified context"


async def test_list_binds_the_row_limit_server_side() -> None:
    connector, log = _connector(FakeConnection([POLICY_ROW]), row_limit=25)

    await connector.fetch(FetchSpec(operation="list_policies"), ALICE)

    fetch = [entry for entry in log if entry[0] == "fetch"][0]
    assert fetch[2] == ("alice-id", 25)
    assert "LIMIT $2" in fetch[1]


async def test_rows_are_projected_down_to_public_fields() -> None:
    connector, _ = _connector(FakeConnection([POLICY_ROW]))

    result = await connector.fetch(FetchSpec(operation="list_policies"), ALICE)

    policy = result.data["policies"][0]
    assert "national_id" not in policy
    assert "customer_ref" not in policy
    assert set(policy) == {
        "policy_id",
        "type",
        "status",
        "insurer",
        "start_date",
        "end_date",
        "premium_toman",
    }


async def test_missing_policy_is_not_found_rather_than_empty() -> None:
    connector, _ = _connector(FakeConnection([], row=None))

    result = await connector.fetch(
        FetchSpec(operation="policy_by_id", params={"policy_id": "POL-other-9"}), ALICE
    )

    assert result.status is ConnectorStatus.NOT_FOUND


async def test_policy_id_must_be_supplied() -> None:
    connector, _ = _connector(FakeConnection([], row=POLICY_ROW))

    result = await connector.fetch(FetchSpec(operation="policy_by_id"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE


async def test_unknown_operation_is_refused_before_touching_the_pool() -> None:
    connector, log = _connector(FakeConnection([]))

    result = await connector.fetch(FetchSpec(operation="execute_sql"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    assert log == []


async def test_missing_identity_is_refused() -> None:
    """Defensive: a context with no subject must never mean "show everything".

    ``AuthContext`` itself forbids an empty subject, so this stubs the shape to exercise
    the connector's own guard — the second line of defence if a caller ever constructs a
    context some other way.
    """
    connector, log = _connector(FakeConnection([]))

    result = await connector.fetch(
        FetchSpec(operation="count_policies"),
        SimpleNamespace(subject="", tenant_id="tenant-a"),  # type: ignore[arg-type]
    )

    assert result.status is ConnectorStatus.UNAUTHORIZED
    assert log == []


async def test_statement_timeout_is_reported_as_a_timeout() -> None:
    connector, _ = _connector(FakeConnection([], fail=QueryCanceledError("canceled")))

    result = await connector.fetch(FetchSpec(operation="count_policies"), ALICE)

    assert result.status is ConnectorStatus.TIMEOUT


async def test_unexpected_database_error_becomes_an_upstream_error() -> None:
    connector, _ = _connector(FakeConnection([], fail=RuntimeError("relation does not exist")))

    result = await connector.fetch(FetchSpec(operation="count_policies"), ALICE)

    assert result.status is ConnectorStatus.UPSTREAM_ERROR
    assert "relation" not in (result.message or "")


@pytest.mark.parametrize("row_limit", [0, -1, 101])
def test_row_limit_is_validated_at_construction(row_limit: int) -> None:
    with pytest.raises(ValueError, match="row_limit"):
        PostgresPolicyConnector(dsn="postgresql://x", row_limit=row_limit)
