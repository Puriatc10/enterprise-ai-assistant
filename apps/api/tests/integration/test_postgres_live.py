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
from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.domain.interfaces.connector import ConnectorStatus, FetchSpec
from app.domain.models.auth import AuthContext
from app.domain.models.crm import PUBLIC_ACCOUNT_FIELDS, PUBLIC_DEAL_FIELDS
from app.infrastructure.connectors.postgres.crm import CrmConnector
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


# ===========================================================================
# CRM domain
#
# Appended for the crm schema. It exercises the real seeder script (imported locally, because
# scripts/ is not part of the app package) against a real server, which is worth more than a
# second hand-written seed.
# ===========================================================================

DEMO = AuthContext(subject="demo-subject", tenant_id="tenant-a")
OTHER = AuthContext(subject="other-subject", tenant_id="tenant-a")
NOBODY = AuthContext(subject="nobody-id", tenant_id="tenant-a")

#: The seeder gives every subject the same five-deal pipeline (3 open, 2,350,000,000 toman
#: open). Literals on purpose: the assertions are facts about the seeded data, not a
#: re-derivation of the seeder's maths.
DEMO_DEALS = 5
DEMO_OPEN_DEALS = 3
DEMO_OPEN_TOMAN = 2_350_000_000

#: An extra deal that exists for other-subject only. Without a row that is unique to one
#: subject, "demo cannot see other's deals" would pass even if the scoping were inverted.
OTHER_ONLY_TITLE = "فروش خط جدید بسته‌بندی"
OTHER_ONLY_TOMAN = 100_000_000
OTHER_DEALS = DEMO_DEALS + 1
OTHER_OPEN_DEALS = DEMO_OPEN_DEALS + 1
OTHER_OPEN_TOMAN = DEMO_OPEN_TOMAN + OTHER_ONLY_TOMAN


@pytest.fixture
async def crm_seeded(admin_dsn: str, seeded: None) -> AsyncIterator[None]:
    """Create the crm schema with the real seeder, then grant the read-only role SELECT.

    ``seeded`` is a declared dependency rather than an assumed autouse ordering: the
    ``connector_reader`` role must exist before anything is granted to it. The schema is
    dropped again afterwards, because the next test's ``seeded`` fixture drops that role and
    PostgreSQL refuses while privileges still depend on it.
    """
    from scripts.seed_crm import ensure_schema, seed_subject

    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute("drop schema if exists crm cascade")
        await ensure_schema(connection)
        await seed_subject(
            connection,
            "demo-subject",
            name="شرکت داده‌پردازی آریا",
            industry="فناوری اطلاعات",
            owner_name="سارا محمدی",
        )
        await seed_subject(
            connection,
            "other-subject",
            name="گروه صنعتی پارس",
            industry="تولید صنعتی",
            owner_name="امیر رضایی",
        )
        await connection.execute(
            "insert into crm.deals (account_id, title, stage, amount_toman, expected_close) "
            "select a.id, $1, 'qualified', $2, '1405/05/01' from crm.accounts a "
            "where a.auth_subject = 'other-subject'",
            OTHER_ONLY_TITLE,
            OTHER_ONLY_TOMAN,
        )

        await connection.execute("grant usage on schema crm to connector_reader")
        await connection.execute("grant select on all tables in schema crm to connector_reader")

        yield
    finally:
        # Privileges on the schema would otherwise block the next test's DROP ROLE.
        await connection.execute("drop schema if exists crm cascade")
        await connection.close()


@pytest.fixture
async def crm_connector(admin_dsn: str, crm_seeded: None) -> AsyncIterator[CrmConnector]:
    reader = CrmConnector(dsn=_with_credentials(admin_dsn, "connector_reader", READER_PASSWORD))
    yield reader
    await reader.aclose()


async def test_crm_lists_only_the_callers_deals(crm_connector: CrmConnector) -> None:
    result = await crm_connector.fetch(FetchSpec(operation="list_deals"), DEMO)

    assert result.status is ConnectorStatus.OK
    deals = result.data["deals"]
    assert len(deals) == DEMO_DEALS
    # Internals from the join (id, account_id, auth_subject) never leave the data layer.
    assert all(set(deal) <= set(PUBLIC_DEAL_FIELDS) for deal in deals)
    assert {"title", "stage", "amount_toman", "updated_at"} <= set(deals[0])
    assert not any("خط تولید" in deal["title"] for deal in deals), "other subject leaked"


async def test_crm_another_subjects_deal_is_invisible(crm_connector: CrmConnector) -> None:
    """The decisive isolation check: one row belongs to other-subject and to nobody else."""
    demo = await crm_connector.fetch(FetchSpec(operation="list_deals"), DEMO)
    other = await crm_connector.fetch(FetchSpec(operation="list_deals"), OTHER)

    demo_titles = {deal["title"] for deal in demo.data["deals"]}
    other_titles = {deal["title"] for deal in other.data["deals"]}

    assert len(demo_titles) == DEMO_DEALS
    assert len(other_titles) == OTHER_DEALS
    assert OTHER_ONLY_TITLE not in demo_titles, "other subject's deal leaked into demo"
    assert OTHER_ONLY_TITLE in other_titles, "the distinguishing row must exist for its owner"


async def test_crm_pipeline_is_scoped_per_subject_and_stays_integer(
    crm_connector: CrmConnector,
) -> None:
    demo = await crm_connector.fetch(FetchSpec(operation="summarize_pipeline"), DEMO)
    other = await crm_connector.fetch(FetchSpec(operation="summarize_pipeline"), OTHER)

    assert demo.status is ConnectorStatus.OK
    assert demo.data["open_deals"] == DEMO_OPEN_DEALS
    assert demo.data["total_amount_toman"] == DEMO_OPEN_TOMAN
    assert isinstance(demo.data["total_amount_toman"], int), "sum(bigint) must not leak Decimal"
    assert demo.data["currency"] == "IRT"

    # Same statement, different subject: neither total includes the other's deals.
    assert other.status is ConnectorStatus.OK
    assert other.data["open_deals"] == OTHER_OPEN_DEALS
    assert other.data["total_amount_toman"] == OTHER_OPEN_TOMAN


async def test_crm_an_unknown_subject_sees_nothing_rather_than_everything(
    crm_connector: CrmConnector,
) -> None:
    own = await crm_connector.fetch(FetchSpec(operation="account_by_subject"), DEMO)
    absent = await crm_connector.fetch(FetchSpec(operation="account_by_subject"), NOBODY)
    deals = await crm_connector.fetch(FetchSpec(operation="list_deals"), NOBODY)

    assert own.status is ConnectorStatus.OK
    assert set(own.data) == set(PUBLIC_ACCOUNT_FIELDS)
    assert absent.status is ConnectorStatus.NOT_FOUND
    assert deals.status is ConnectorStatus.OK
    assert deals.data == {"deals": []}


async def test_crm_reader_can_select_but_cannot_write(admin_dsn: str, crm_seeded: None) -> None:
    """SELECT-only proven at the database: the grant, not a promise in a document."""
    connection = await asyncpg.connect(
        _with_credentials(admin_dsn, "connector_reader", READER_PASSWORD)
    )
    try:
        assert await connection.fetchval("select count(*) from crm.accounts") == 2

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await connection.execute(
                "insert into crm.accounts (auth_subject, name, industry, owner_name) "
                "values ('mallory', 'x', 'x', 'x')"
            )

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await connection.execute("delete from crm.deals")
    finally:
        await connection.close()
