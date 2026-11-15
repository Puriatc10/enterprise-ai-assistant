"""Live PostgreSQL proof — skipped unless ``TEST_POSTGRES_DSN`` is set.

These run in CI against a real ``postgres:16`` service container, because the point is
to prove the controls against a real server, not a fake: that the read-only role cannot
write, that a READ ONLY transaction refuses a write even for a privileged role, and that
the connector's queries return the right rows for the right customer.

Locally, without a database, the whole module skips with a clear reason instead of
failing — the project must stay testable on a machine with no Docker.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.domain.interfaces.connector import ConnectorStatus, FetchSpec
from app.domain.models.auth import AuthContext
from app.infrastructure.connectors.postgres.policies import PostgresPolicyConnector

DSN = os.environ.get("TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="TEST_POSTGRES_DSN not set; CI runs this against a postgres service"
)

asyncpg = pytest.importorskip("asyncpg")

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")

READER_PASSWORD = "reader"  # noqa: S105 - a throwaway role in a CI container

CREATE_TABLE = """
CREATE TABLE connector_policies (
    customer_ref   text    NOT NULL,
    policy_id      text    NOT NULL,
    type           text    NOT NULL,
    status         text    NOT NULL,
    insurer        text    NOT NULL,
    start_date     text    NOT NULL,
    end_date       text    NOT NULL,
    premium_toman  bigint  NOT NULL,
    national_id    text,
    PRIMARY KEY (customer_ref, policy_id)
)
"""

SEED = [
    (
        "alice-id",
        "POL-A-1",
        "درمان تکمیلی",
        "فعال",
        "بیمه دی",
        "1404/03/01",
        "1405/03/01",
        4_800_000,
    ),
    ("alice-id", "POL-A-2", "شخص ثالث", "فعال", "بیمه دی", "1404/07/15", "1405/07/15", 12_500_000),
    ("alice-id", "POL-A-3", "آتش‌سوزی", "منقضی", "بیمه دی", "1403/02/10", "1404/02/10", 2_100_000),
    ("bob-id", "POL-B-1", "عمر", "فعال", "بیمه دی", "1404/01/01", "1405/01/01", 9_000_000),
]


def _with_credentials(dsn: str, user: str, password: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    database = (parts.path or "/postgres").lstrip("/")
    return urlunsplit((parts.scheme, f"{user}:{password}@{host}{port}", f"/{database}", "", ""))


@pytest.fixture
def admin_dsn() -> str:
    assert DSN is not None
    return DSN


@pytest.fixture(autouse=True)
async def seeded(admin_dsn: str) -> None:
    """Create the table, seed synthetic rows, and a genuinely read-only role."""
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute("DROP TABLE IF EXISTS connector_policies")
        await connection.execute(CREATE_TABLE)
        await connection.executemany(
            "INSERT INTO connector_policies VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            [(*row, "0012345678") for row in SEED],
        )
        await connection.execute("DROP ROLE IF EXISTS connector_reader")
        await connection.execute(f"CREATE ROLE connector_reader LOGIN PASSWORD '{READER_PASSWORD}'")
        await connection.execute("GRANT SELECT ON connector_policies TO connector_reader")
    finally:
        await connection.close()
    yield


@pytest.fixture
async def connector(admin_dsn: str) -> PostgresPolicyConnector:
    reader = PostgresPolicyConnector(
        dsn=_with_credentials(admin_dsn, "connector_reader", READER_PASSWORD)
    )
    yield reader
    await reader.aclose()


async def test_count_reads_only_the_callers_rows(connector: PostgresPolicyConnector) -> None:
    result = await connector.fetch(FetchSpec(operation="count_policies"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert result.data == {"count": 3, "active_count": 2}


async def test_list_projects_public_fields_only(connector: PostgresPolicyConnector) -> None:
    result = await connector.fetch(FetchSpec(operation="list_policies"), ALICE)

    assert result.status is ConnectorStatus.OK
    policies = result.data["policies"]
    assert len(policies) == 3
    assert all("national_id" not in policy for policy in policies)
    assert {policy["policy_id"] for policy in policies} == {"POL-A-1", "POL-A-2", "POL-A-3"}


async def test_another_customers_policy_looks_nonexistent(
    connector: PostgresPolicyConnector,
) -> None:
    own = await connector.fetch(
        FetchSpec(operation="policy_by_id", params={"policy_id": "POL-A-1"}), ALICE
    )
    foreign = await connector.fetch(
        FetchSpec(operation="policy_by_id", params={"policy_id": "POL-B-1"}), ALICE
    )

    assert own.status is ConnectorStatus.OK
    assert foreign.status is ConnectorStatus.NOT_FOUND


async def test_the_read_only_role_cannot_write(admin_dsn: str) -> None:
    """Least privilege proven at the database, not asserted in a document."""
    connection = await asyncpg.connect(
        _with_credentials(admin_dsn, "connector_reader", READER_PASSWORD)
    )
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await connection.execute(
                "INSERT INTO connector_policies VALUES "
                "('alice-id','POL-X','t','فعال','i','1','2',1,'0')"
            )
    finally:
        await connection.close()


async def test_a_read_only_transaction_blocks_writes_even_for_a_privileged_role(
    admin_dsn: str,
) -> None:
    """The adapter's own guard, independent of the role's grants."""
    connection = await asyncpg.connect(admin_dsn)
    try:
        with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
            async with connection.transaction(readonly=True):
                await connection.execute(
                    "INSERT INTO connector_policies VALUES "
                    "('alice-id','POL-Y','t','فعال','i','1','2',1,'0')"
                )
    finally:
        await connection.close()
