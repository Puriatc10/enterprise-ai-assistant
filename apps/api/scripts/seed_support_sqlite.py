"""Build the SQLite support demo database from ``data/support_demo.sql``.

Why a script instead of a migration: this database is demo data, not application
state. Building it is an operator action, the SQL is committed and reviewable, and the
output file is disposable — so ``--out`` lets a developer put a scratch copy anywhere
inside ``apps/api/data/`` and regenerate it at will.

Idempotent by construction: the table is dropped and recreated before the SQL is
applied, so running this twice produces the same file, not duplicate rows. (It is not
byte-identical — SQLite writes fresh pages — but the *data* is.)

The running application never calls this. It opens the result with ``mode=ro``; the
only writer is this script.

Usage:
    python scripts/seed_support_sqlite.py
    python scripts/seed_support_sqlite.py --out data/demo_support.sqlite3 --force
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

#: ``apps/api`` — the directory every path below is relative to.
API_ROOT = Path(__file__).resolve().parents[1]
#: The only directory this script will write a database into by default.
DATA_DIR = API_ROOT / "data"
DEFAULT_SQL = DATA_DIR / "support_demo.sql"
DEFAULT_OUT = DATA_DIR / "demo_support.sqlite3"

_TABLE = "support_tickets"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the read-only SQLite support demo database.",
    )
    parser.add_argument(
        "--sql",
        type=Path,
        default=DEFAULT_SQL,
        help=f"seed SQL to apply (default: {DEFAULT_SQL.relative_to(API_ROOT)})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"database file to write (default: {DEFAULT_OUT.relative_to(API_ROOT)})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow writing outside apps/api/data/ (refused otherwise)",
    )
    return parser.parse_args(argv)


def _resolve_out(raw: Path, *, force: bool) -> Path:
    """Resolve ``--out`` and refuse to write outside ``apps/api/data/``.

    The guard exists because the expected mistake is a typo like ``--out ../../db``
    that would drop a demo database into the repository root or, worse, over an
    unrelated file. Writing to a temporary directory from a test or a CI job is the
    one legitimate exception, and ``--force`` is how a caller asks for it explicitly.
    """
    out = (raw if raw.is_absolute() else API_ROOT / raw).resolve()
    data_dir = DATA_DIR.resolve()
    if not force and not out.is_relative_to(data_dir):
        raise SystemExit(
            f"refusing to write {out} outside {data_dir}; pass --force if that is intended"
        )
    return out


def _sql_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read seed SQL {path}: {exc.strerror or exc}") from exc


def seed(*, sql_path: Path, out_path: Path) -> dict[str, int]:
    """Drop, recreate and repopulate the table. Returns per-owner row counts.

    Drop and rebuild go through one ``executescript`` so a reader is very unlikely to
    see a half-applied file. That is not a transaction (``executescript`` commits), and
    it does not need to be: the file is disposable demo data, regenerated on demand,
    never written to by the app.
    """
    script = _sql_text(sql_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(out_path)
    try:
        connection.executescript(f"DROP TABLE IF EXISTS {_TABLE};\n{script}")
        connection.commit()
    finally:
        connection.close()

    return _row_counts(out_path)


def _row_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"SELECT customer_ref, count(*) AS total FROM {_TABLE} GROUP BY customer_ref "
            "ORDER BY customer_ref"
        ).fetchall()
        return {owner: int(total) for owner, total in rows}
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    sql_path = args.sql if args.sql.is_absolute() else API_ROOT / args.sql
    out_path = _resolve_out(args.out, force=args.force)

    counts = seed(sql_path=sql_path, out_path=out_path)

    total = sum(counts.values())
    print(f"seeded {out_path}")
    for owner, count in counts.items():
        print(f"  {owner:<10} {count} ticket(s)")
    print(f"  {'total':<10} {total} ticket(s)")
    if total == 0:
        # A silent empty database would look like a working connector answering nothing.
        print("warning: no rows were inserted; check the seed SQL", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
