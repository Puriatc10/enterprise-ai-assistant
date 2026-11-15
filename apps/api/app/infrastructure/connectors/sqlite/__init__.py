"""SQLite connector (read-only, file-backed) — a real engine with no credentials."""

from app.infrastructure.connectors.sqlite.support import SqliteSupportConnector

__all__ = ["SqliteSupportConnector"]
