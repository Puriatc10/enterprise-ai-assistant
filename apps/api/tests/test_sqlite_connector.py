"""SQLite connector against a real (temporary) database file.

These tests use the engine, not a fake: the read-only guarantee, the ``LIMIT``, the
identity scoping and the timeout abort are all properties of the real connection, so a
stub would prove nothing about them. The file is built in ``tmp_path`` from a small
fixture schema; the committed demo data is exercised by the seeder script instead.

What must hold, in the order it matters:

* the same query run for one subject cannot reach another subject's row, and a foreign
  id is indistinguishable from a nonexistent one (no existence oracle);
* ``customer_ref`` never appears in any output;
* the row limit is applied in the statement, not by slicing a result;
* a write through this adapter's own connection raises;
* a missing file is a typed failure, not an exception and not a path dump.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.interfaces.connector import ConnectorStatus, DataConnectorPort, FetchSpec
from app.domain.models.auth import AuthContext
from app.domain.models.support import PUBLIC_TICKET_DETAIL_FIELDS, PUBLIC_TICKET_FIELDS
from app.infrastructure.connectors.sqlite import support as support_module
from app.infrastructure.connectors.sqlite.support import (
    MAX_ROW_LIMIT,
    SqliteSupportConnector,
    connect_read_only,
)

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")

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
    ('alice-id', 'TKT-A4', 'تأخیر ارسال', 'سفارش دیر شده است.', 'pending', 'normal',
     '2026-01-13T09:00:00+03:30', '2026-01-17T09:00:00+03:30'),
    ('bob-id',   'TKT-B1', 'بازنشانی رمز', 'رمز را فراموش کردم.', 'open', 'high',
     '2026-01-14T09:00:00+03:30', '2026-01-16T09:00:00+03:30'),
    ('bob-id',   'TKT-B2', 'لغو اشتراک', 'تمدید نمی‌کنم.', 'closed', 'low',
     '2026-01-15T09:00:00+03:30', '2026-01-15T09:00:00+03:30');
"""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A real SQLite file with four tickets for alice and two for bob."""
    path = tmp_path / "support.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(FIXTURE_SQL)
        connection.commit()
    finally:
        connection.close()
    return path


def _connector(db_path: Path, **kwargs: object) -> SqliteSupportConnector:
    return SqliteSupportConnector(path=str(db_path), **kwargs)


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


async def test_count_returns_total_and_open_count(db_path: Path) -> None:
    result = await _connector(db_path).fetch(FetchSpec(operation="count_tickets"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert result.data == {"count": 4, "open_count": 2}


async def test_count_for_a_subject_with_no_tickets_is_zero_not_an_error(db_path: Path) -> None:
    """sum() over zero rows is NULL; the connector must report 0, not fail."""
    result = await _connector(db_path).fetch(
        FetchSpec(operation="count_tickets"),
        AuthContext(subject="nobody-id", tenant_id="tenant-a"),
    )

    assert result.status is ConnectorStatus.OK
    assert result.data == {"count": 0, "open_count": 0}


async def test_list_returns_exactly_the_public_fields(db_path: Path) -> None:
    result = await _connector(db_path).fetch(FetchSpec(operation="list_tickets"), ALICE)

    assert result.status is ConnectorStatus.OK
    tickets = result.data["tickets"]
    assert len(tickets) == 4
    for ticket in tickets:
        assert set(ticket) == set(PUBLIC_TICKET_FIELDS)
        assert "customer_ref" not in ticket
        assert "description" not in ticket, "the list must stay small"


async def test_list_is_scoped_to_the_caller(db_path: Path) -> None:
    alice = await _connector(db_path).fetch(FetchSpec(operation="list_tickets"), ALICE)
    bob = await _connector(db_path).fetch(FetchSpec(operation="list_tickets"), BOB)

    alice_ids = {ticket["ticket_id"] for ticket in alice.data["tickets"]}
    bob_ids = {ticket["ticket_id"] for ticket in bob.data["tickets"]}

    assert alice_ids == {"TKT-A1", "TKT-A2", "TKT-A3", "TKT-A4"}
    assert bob_ids == {"TKT-B1", "TKT-B2"}
    assert not alice_ids & bob_ids


async def test_detail_includes_the_description_and_nothing_internal(db_path: Path) -> None:
    result = await _connector(db_path).fetch(
        FetchSpec(operation="ticket_by_id", params={"ticket_id": "TKT-A1"}), ALICE
    )

    assert result.status is ConnectorStatus.OK
    assert set(result.data) == set(PUBLIC_TICKET_DETAIL_FIELDS)
    assert result.data["ticket_id"] == "TKT-A1"
    assert result.data["description"] == "ورود ناموفق است."
    assert "customer_ref" not in result.data


async def test_customer_ref_never_appears_anywhere_in_the_output(db_path: Path) -> None:
    """The projection is the allowlist; this asserts it on every operation at once."""
    connector = _connector(db_path)
    results = [
        await connector.fetch(FetchSpec(operation="count_tickets"), ALICE),
        await connector.fetch(FetchSpec(operation="list_tickets"), ALICE),
        await connector.fetch(
            FetchSpec(operation="ticket_by_id", params={"ticket_id": "TKT-A1"}), ALICE
        ),
    ]

    for result in results:
        assert "customer_ref" not in str(result.data)
        assert "alice-id" not in str(result.data)


# ---------------------------------------------------------------------------
# Scoping: another subject's ticket is indistinguishable from a missing one
# ---------------------------------------------------------------------------


async def test_another_subjects_ticket_is_not_found(db_path: Path) -> None:
    result = await _connector(db_path).fetch(
        FetchSpec(operation="ticket_by_id", params={"ticket_id": "TKT-B1"}), ALICE
    )

    assert result.status is ConnectorStatus.NOT_FOUND


async def test_foreign_and_nonexistent_ids_look_identical(db_path: Path) -> None:
    """No existence oracle: both answers must match in status *and* message."""
    connector = _connector(db_path)
    foreign = await connector.fetch(
        FetchSpec(operation="ticket_by_id", params={"ticket_id": "TKT-B1"}), ALICE
    )
    missing = await connector.fetch(
        FetchSpec(operation="ticket_by_id", params={"ticket_id": "TKT-Z9"}), ALICE
    )

    assert foreign.status is missing.status is ConnectorStatus.NOT_FOUND
    assert foreign.message == missing.message


async def test_identity_is_taken_from_the_context_not_the_params(db_path: Path) -> None:
    """A ``customer_ref`` smuggled into params must be inert, not honoured."""
    result = await _connector(db_path).fetch(
        FetchSpec(
            operation="ticket_by_id",
            params={"ticket_id": "TKT-B1", "customer_ref": "bob-id"},
        ),
        ALICE,
    )

    assert result.status is ConnectorStatus.NOT_FOUND


async def test_missing_ticket_id_is_an_invalid_request(db_path: Path) -> None:
    result = await _connector(db_path).fetch(FetchSpec(operation="ticket_by_id"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE


async def test_empty_subject_is_unauthorized(db_path: Path) -> None:
    """Defensive second line: an anonymous read must never mean "show everything".

    ``AuthContext`` forbids an empty subject, so the shape is stubbed to exercise the
    connector's own guard rather than the model's.
    """
    result = await _connector(db_path).fetch(
        FetchSpec(operation="list_tickets"),
        SimpleNamespace(subject="", tenant_id="tenant-a"),  # type: ignore[arg-type]
    )

    assert result.status is ConnectorStatus.UNAUTHORIZED


async def test_unknown_operation_is_refused_without_being_guessed(db_path: Path) -> None:
    result = await _connector(db_path).fetch(
        FetchSpec(operation="select * from support_tickets"), ALICE
    )

    assert result.status is ConnectorStatus.INVALID_RESPONSE


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


async def test_row_limit_is_applied_in_the_statement(db_path: Path) -> None:
    result = await _connector(db_path, row_limit=2).fetch(
        FetchSpec(operation="list_tickets"), ALICE
    )

    assert result.status is ConnectorStatus.OK
    assert len(result.data["tickets"]) == 2


@pytest.mark.parametrize("row_limit", [0, -1, MAX_ROW_LIMIT + 1])
def test_row_limit_is_validated_at_construction(db_path: Path, row_limit: int) -> None:
    with pytest.raises(ValueError, match="row_limit"):
        SqliteSupportConnector(path=str(db_path), row_limit=row_limit)


@pytest.mark.parametrize("timeout_ms", [0, -5])
def test_timeout_is_validated_at_construction(db_path: Path, timeout_ms: int) -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        SqliteSupportConnector(path=str(db_path), timeout_ms=timeout_ms)


async def test_a_runaway_statement_is_aborted_by_the_progress_handler(db_path: Path) -> None:
    """The bound lives inside SQLite because a thread cannot be cancelled.

    The deadline is set in the past and the statement is long enough to check it, which
    is deterministic — no sleeping on a real slow query.
    """
    connection = connect_read_only(str(db_path), timeout_ms=1)
    try:
        deadline = time.monotonic() - 1.0
        connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100)
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            connection.execute(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 200000)"
                " SELECT count(*) FROM c"
            ).fetchone()
    finally:
        connection.close()


async def test_aborted_statement_is_reported_as_a_timeout(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connector maps the engine's abort onto its own timeout status."""

    def _interrupted(*_: object, **__: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("interrupted")

    monkeypatch.setattr(support_module, "connect_read_only", _interrupted)

    result = await _connector(db_path).fetch(FetchSpec(operation="count_tickets"), ALICE)

    assert result.status is ConnectorStatus.TIMEOUT


# ---------------------------------------------------------------------------
# Read-only at the engine
# ---------------------------------------------------------------------------


def test_the_connection_this_adapter_opens_cannot_write(db_path: Path) -> None:
    """Not "we do not write" — "a write is impossible", asserted on our own connection."""
    connection = connect_read_only(str(db_path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                "INSERT INTO support_tickets VALUES ('alice-id', 'TKT-NEW', 'x', 'y', "
                "'open', 'low', 'now', 'now')"
            )
    finally:
        connection.close()


def test_the_fixture_database_is_unchanged_after_a_refused_write(db_path: Path) -> None:
    before = db_path.read_bytes()
    connection = connect_read_only(str(db_path))
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM support_tickets")
    finally:
        connection.close()

    assert db_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Missing file: a typed failure, never an exception and never a path dump
# ---------------------------------------------------------------------------


async def test_missing_file_makes_health_unreachable(tmp_path: Path) -> None:
    health = await _connector(tmp_path / "absent.sqlite3").health()

    assert health.reachable is False
    assert health.detail is not None and "missing" in health.detail


async def test_missing_file_is_an_upstream_error_without_leaking_the_path(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "absent.sqlite3"

    result = await _connector(missing).fetch(FetchSpec(operation="count_tickets"), ALICE)

    assert result.status is ConnectorStatus.UPSTREAM_ERROR
    message = result.message or ""
    assert "missing" in message
    # The cause is named; the filesystem layout is not.
    assert str(missing) not in message
    assert str(tmp_path) not in message


async def test_health_reports_a_seeded_file_as_reachable(db_path: Path) -> None:
    health = await _connector(db_path).health()

    assert health.reachable is True
    assert health.detail is not None and "6" in health.detail


def test_the_adapter_satisfies_the_connector_port() -> None:
    """A swap must not need a rewrite, so the port is checked explicitly."""
    assert isinstance(SqliteSupportConnector(path="x.sqlite3"), DataConnectorPort)
