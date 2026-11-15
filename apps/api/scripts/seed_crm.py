"""Seed the demo CRM schema for one authenticated subject.

The migration ``supabase/migrations/0002_crm_demo.sql`` is the source of truth for the
schema, the SELECT-only role and the fixed demo subjects. This script exists for the other
half of the job: giving a *real* Supabase login (a UUID subject) the same realistic data, so
the CRM tools answer something true in a demo without hand-written SQL.

Usage::

    cd apps/api
    python scripts/seed_crm.py --dsn "$CRM_SEED_DSN" --subject 8f3c...-uuid
    python scripts/seed_crm.py --dsn "$CRM_SEED_DSN" --subject demo-subject \\
        --name "شرکت نمونه" --industry "فناوری اطلاعات" --owner "سارا محمدی"
    python scripts/seed_crm.py --dsn "$CRM_SEED_DSN" --schema-only      # tables only

    # DSN may come from the environment instead of the flag (never echoed either way):
    CRM_SEED_DSN=... python scripts/seed_crm.py --subject <uuid>

Design rules, matching the rest of the project:

* **Idempotent.** The account is upserted on ``auth_subject``; deals and activities are only
  inserted when that account has none. Re-running changes nothing.
* **Explicit about destruction.** ``--replace-deals`` deletes this account's deals first,
  and it is the only flag that removes data.
* **Never echoes the DSN.** The target is printed as host/database only, and a driver error
  is scrubbed of the password before it is shown.
* **One writer.** The script uses an admin DSN on purpose: the API's ``crm_reader`` role is
  SELECT-only and must stay that way. Do not put this DSN in the API's environment.
* **No API import.** It talks to PostgreSQL directly, so it runs without the app's settings
  and cannot accidentally start the app's connection pool.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

#: Environment variable used when ``--dsn`` is omitted.
DSN_ENV = "CRM_SEED_DSN"

#: Schema objects, mirroring the tables in supabase/migrations/0002_crm_demo.sql. The role
#: and the fixed demo rows are the migration's job; this script only needs the shape.
SCHEMA_SQL = """
create schema if not exists crm;

create table if not exists crm.accounts (
    id            uuid primary key default gen_random_uuid(),
    auth_subject  text not null unique,
    name          text not null,
    industry      text not null,
    owner_name    text not null,
    created_at    timestamptz not null default now()
);

create table if not exists crm.deals (
    id              uuid primary key default gen_random_uuid(),
    account_id      uuid not null references crm.accounts (id) on delete cascade,
    title           text not null,
    stage           text not null
                    check (stage in ('lead', 'qualified', 'proposal', 'won', 'lost')),
    amount_toman    bigint not null check (amount_toman >= 0),
    currency        text not null default 'IRT',
    expected_close  text,
    updated_at      timestamptz not null default now()
);

create table if not exists crm.activities (
    id           uuid primary key default gen_random_uuid(),
    deal_id      uuid not null references crm.deals (id) on delete cascade,
    kind         text not null,
    summary      text not null,
    occurred_at  timestamptz not null default now()
);

create index if not exists accounts_auth_subject_idx on crm.accounts (auth_subject);
create index if not exists deals_account_idx on crm.deals (account_id);
create index if not exists deals_account_recent_idx on crm.deals (account_id, updated_at desc);
create index if not exists activities_deal_idx on crm.activities (deal_id, occurred_at desc);
"""

#: Defaults used when the operator names no account details.
DEFAULT_NAME = "شرکت نمونه"
DEFAULT_INDUSTRY = "فناوری اطلاعات"
DEFAULT_OWNER = "کاربر نمونه"

#: (title, stage, amount_toman, expected_close, updated_at). Five deals with mixed stages:
#: three open, one won, one lost — 2,350,000,000 toman of open pipeline.
DEAL_ROWS: tuple[tuple[str, str, int, str, datetime], ...] = (
    (
        "تمدید قرارداد پشتیبانی سالانه",
        "proposal",
        780_000_000,
        "1404/12/15",
        datetime.fromisoformat("2026-01-12T10:15:00+03:30"),
    ),
    (
        "استقرار سامانه باشگاه مشتریان",
        "qualified",
        1_250_000_000,
        "1405/02/01",
        datetime.fromisoformat("2026-01-09T14:40:00+03:30"),
    ),
    (
        "خرید لایسنس داشبورد تحلیل فروش",
        "lead",
        320_000_000,
        "1405/03/10",
        datetime.fromisoformat("2026-01-05T09:05:00+03:30"),
    ),
    (
        "بازطراحی وبسایت شرکتی",
        "won",
        450_000_000,
        "1404/11/20",
        datetime.fromisoformat("2025-12-28T16:20:00+03:30"),
    ),
    (
        "پروژه یکپارچه‌سازی با درگاه پرداخت",
        "lost",
        610_000_000,
        "1404/10/05",
        datetime.fromisoformat("2025-12-15T11:00:00+03:30"),
    ),
)

CURRENCY = "IRT"

#: (deal_title, kind, summary, occurred_at) — timeline rows for two of the deals.
ACTIVITY_ROWS: tuple[tuple[str, str, str, datetime], ...] = (
    (
        "تمدید قرارداد پشتیبانی سالانه",
        "call",
        "تماس با مدیر فنی درباره شرایط تمدید",
        datetime.fromisoformat("2026-01-12T09:30:00+03:30"),
    ),
    (
        "تمدید قرارداد پشتیبانی سالانه",
        "email",
        "ارسال پیشنهاد قیمت بازنگری‌شده",
        datetime.fromisoformat("2026-01-11T17:05:00+03:30"),
    ),
    (
        "استقرار سامانه باشگاه مشتریان",
        "meeting",
        "جلسه نیازسنجی با تیم بازاریابی",
        datetime.fromisoformat("2026-01-08T10:00:00+03:30"),
    ),
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one ``seed_subject`` call did, for the operator and for tests."""

    subject: str
    account_id: str
    account_created: bool
    deals_inserted: int
    deals_removed: int
    activities_inserted: int
    deals_now: int


def redact(text: str, dsn: str) -> str:
    """Remove the DSN, and its password if present, from anything we print."""
    password = urlsplit(dsn).password
    scrubbed = text.replace(dsn, "<dsn>") if dsn else text
    return scrubbed.replace(password, "***") if password else scrubbed


def describe_target(dsn: str) -> str:
    """``host=…/db=…`` — enough to confirm the target, without a credential in it."""
    parts = urlsplit(dsn)
    host = parts.hostname or "unknown-host"
    port = f":{parts.port}" if parts.port else ""
    database = (parts.path or "/").lstrip("/") or "(default)"
    return f"host={host}{port} database={database}"


async def ensure_schema(connection: Any) -> None:
    """Create the ``crm`` schema objects if they are not there yet.

    Multi-statement DDL through ``execute()`` with no parameters uses the simple query
    protocol, which PostgreSQL permits — and every statement is ``if not exists``.
    """
    await connection.execute(SCHEMA_SQL)


async def seed_subject(
    connection: Any,
    subject: str,
    *,
    name: str = DEFAULT_NAME,
    industry: str = DEFAULT_INDUSTRY,
    owner_name: str = DEFAULT_OWNER,
    replace_deals: bool = False,
) -> SeedReport:
    """Upsert one account and give it the demo deals, idempotently.

    The account row is upserted on ``auth_subject`` so a re-run updates the descriptive
    fields rather than failing; deals are inserted only when the account has none, so a
    re-run is a no-op unless ``replace_deals`` is set.
    """
    subject = subject.strip()
    if not subject:
        msg = "subject must be a non-empty string"
        raise ValueError(msg)

    async with connection.transaction():
        row = await connection.fetchrow(
            """
            insert into crm.accounts (auth_subject, name, industry, owner_name)
            values ($1, $2, $3, $4)
            on conflict (auth_subject) do update
               set name = excluded.name,
                   industry = excluded.industry,
                   owner_name = excluded.owner_name
            returning id, (xmax = 0) as inserted
            """,
            subject,
            name,
            industry,
            owner_name,
        )
        account_id = row["id"]
        account_created = bool(row["inserted"])  # xmax = 0 only on a fresh insert

        removed = 0
        if replace_deals:
            status = await connection.execute(
                "delete from crm.deals where account_id = $1", account_id
            )
            removed = int(status.rsplit(" ", 1)[-1] or 0)

        existing = await connection.fetchval(
            "select count(*) from crm.deals where account_id = $1", account_id
        )
        inserted = 0
        if existing == 0:
            await connection.executemany(
                """
                insert into crm.deals
                    (account_id, title, stage, amount_toman, currency, expected_close, updated_at)
                values ($1, $2, $3, $4, $5, $6, $7)
                """,
                [
                    (account_id, title, stage, amount, CURRENCY, expected_close, updated_at)
                    for title, stage, amount, expected_close, updated_at in DEAL_ROWS
                ],
            )
            inserted = len(DEAL_ROWS)

        activities = 0
        for deal_title, kind, summary, occurred_at in ACTIVITY_ROWS:
            status = await connection.execute(
                """
                insert into crm.activities (deal_id, kind, summary, occurred_at)
                select d.id, $3, $4, $5
                  from crm.deals d
                 where d.account_id = $1
                   and d.title = $2
                   and not exists (select 1 from crm.activities a where a.deal_id = d.id)
                """,
                account_id,
                deal_title,
                kind,
                summary,
                occurred_at,
            )
            activities += int(status.rsplit(" ", 1)[-1] or 0)

        deals_now = await connection.fetchval(
            "select count(*) from crm.deals where account_id = $1", account_id
        )

    return SeedReport(
        subject=subject,
        account_id=str(account_id),
        account_created=account_created,
        deals_inserted=inserted,
        deals_removed=removed,
        activities_inserted=activities,
        deals_now=int(deals_now or 0),
    )


async def pipeline_state(connection: Any, subject: str) -> dict[str, Any] | None:
    """Read back what the API will see for this subject (scope included)."""
    row = await connection.fetchrow(
        """
        select count(d.id) as deals,
               count(d.id) filter (where d.stage not in ('won', 'lost')) as open_deals,
               coalesce(sum(d.amount_toman) filter (where d.stage not in ('won', 'lost')), 0)
                   as open_toman
          from crm.accounts a
          left join crm.deals d on d.account_id = a.id
         where a.auth_subject = $1
        """,
        subject,
    )
    return dict(row) if row is not None and row["deals"] else None


def _import_asyncpg() -> Any:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - depends on the optional extra
        raise SystemExit(
            "asyncpg is not installed. Install the extra: uv sync --extra postgres"
        ) from None
    return asyncpg


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_crm.py",
        description="Create the crm schema and seed one subject's demo CRM data.",
    )
    parser.add_argument(
        "--dsn",
        help=f"Admin DSN (writes DDL and rows). Falls back to ${DSN_ENV}. Never echoed.",
    )
    parser.add_argument("--subject", help="Verified JWT subject (auth_subject) to seed.")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Account name.")
    parser.add_argument("--industry", default=DEFAULT_INDUSTRY, help="Account industry.")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="Account owner name.")
    parser.add_argument(
        "--replace-deals",
        action="store_true",
        help=(
            "Delete this account's deals before seeding (destructive; the only flag that deletes)."
        ),
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Create the schema objects and stop; --subject then becomes optional.",
    )
    return parser


async def _run(arguments: argparse.Namespace, dsn: str) -> int:
    asyncpg = _import_asyncpg()

    print(f"seed_crm: target {describe_target(dsn)}")
    connection = await asyncpg.connect(dsn)
    try:
        await ensure_schema(connection)
        print("schema: crm ready (accounts, deals, activities)")

        if not arguments.subject:
            print("rows: none written (no --subject given)")
            return 0

        report = await seed_subject(
            connection,
            arguments.subject,
            name=arguments.name,
            industry=arguments.industry,
            owner_name=arguments.owner,
            replace_deals=arguments.replace_deals,
        )
        state = await pipeline_state(connection, report.subject)

        print(f"subject: {report.subject}")
        state_word = "created" if report.account_created else "updated"
        print(f"account: {report.account_id} ({state_word})")
        if report.deals_removed:
            print(f"deals: {report.deals_removed} removed (--replace-deals)")
        print(f"deals: {report.deals_inserted} inserted, {report.deals_now} now")
        print(f"activities: {report.activities_inserted} inserted")
        if state is not None:
            print(
                f"pipeline: {state['open_deals']} open deals, "
                f"{int(state['open_toman']):,} toman (won/lost excluded)"
            )
        print(
            "note: the read-only role and the fixed demo subjects come from "
            "supabase/migrations/0002_crm_demo.sql"
        )
    finally:
        await connection.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    dsn = (arguments.dsn or os.environ.get(DSN_ENV) or "").strip()
    if not dsn:
        print(
            f"error: no DSN. Pass --dsn <url> or set ${DSN_ENV}.",
            file=sys.stderr,
        )
        return 2
    if not arguments.subject and not arguments.schema_only:
        print(
            "error: --subject is required unless --schema-only is given.",
            file=sys.stderr,
        )
        return 2

    try:
        return asyncio.run(_run(arguments, dsn))
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 - an operator script reports, it does not trace
        print(f"error: {type(exc).__name__}: {redact(str(exc), dsn)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
