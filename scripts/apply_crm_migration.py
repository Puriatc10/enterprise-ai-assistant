"""Apply the CRM demo schema and seed rows against a real PostgreSQL database.

Two jobs, one command, because they always happen together and the second is useless
without the first:

1. apply ``supabase/migrations/0002_crm_demo.sql`` (schema, tables, the SELECT-only
   ``crm_reader`` role, grants) — the file is idempotent, so re-running is safe;
2. seed demo accounts/deals/activities for one auth subject via ``scripts/seed_crm.py``.

The DSN must be an *admin* connection (DDL + role creation). The connector itself uses the
narrow ``crm_reader`` DSN, which cannot do any of this — that separation is the point.

Usage:
    python scripts/apply_crm_migration.py --dsn "postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres" \
        --subject 00000000-0000-0000-0000-000000000000

    # schema only, no data
    python scripts/apply_crm_migration.py --dsn "..." --skip-seed
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "0002_crm_demo.sql"


async def apply(dsn: str, subject: str | None, skip_seed: bool) -> None:
    import asyncpg

    sql = MIGRATION.read_text(encoding="utf-8")

    # The migration ships a placeholder password so the repository never carries a real
    # one. Refuse to run it unchanged: that would create a role nobody can log in as.
    if "CHANGE_ME" in sql and "--allow-placeholder" not in sys.argv:
        raise SystemExit(
            "0002_crm_demo.sql still contains the placeholder password (CHANGE_ME).\n"
            "Either replace it locally (do not commit), or create the crm_reader role in\n"
            "Dashboard → Database → Roles first and remove the CREATE ROLE statement."
        )

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        print("connected:", await conn.fetchval("select current_database()"),
              "as", await conn.fetchval("select current_user"))
        await conn.execute(sql)
        print("migration applied:", MIGRATION.name)

        tables = await conn.fetch(
            "select table_name from information_schema.tables "
            "where table_schema = 'crm' order by table_name"
        )
        print("crm tables:", [row["table_name"] for row in tables])

        grants = await conn.fetch(
            "select table_name, privilege_type from information_schema.role_table_grants "
            "where grantee = 'crm_reader' and table_schema = 'crm' order by 1, 2"
        )
        print("crm_reader grants:", [(g["table_name"], g["privilege_type"]) for g in grants])
    finally:
        await conn.close()

    if skip_seed or not subject:
        print("skipping seed" + (" (no --subject given)" if not subject else ""))
        return

    # The seeder owns the demo rows; this script only decides when to call it.
    import subprocess

    seeder = ROOT / "apps" / "api" / "scripts" / "seed_crm.py"
    print("seeding demo CRM rows for subject", subject)
    subprocess.run(
        [sys.executable, str(seeder), "--dsn", dsn, "--subject", subject],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="admin DSN (never commit it)")
    parser.add_argument("--subject", help="auth subject uuid to bind the demo rows to")
    parser.add_argument("--skip-seed", action="store_true", help="schema only")
    parser.add_argument(
        "--reader-password",
        help="password for the crm_reader role; substituted in memory, never stored",
    )
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        help="run with the placeholder password (only if the role already exists)",
    )
    args = parser.parse_args()

    if not re.match(r"^postgres(ql)?://", args.dsn):
        raise SystemExit("--dsn must be a postgres:// or postgresql:// URI")

    asyncio.run(apply(args.dsn, args.subject, args.skip_seed, args.reader_password))


if __name__ == "__main__":
    main()
