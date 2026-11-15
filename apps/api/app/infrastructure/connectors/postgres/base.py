"""Shared read-only PostgreSQL machinery for the connector adapters.

One connector per domain (policies, CRM) differs only in its named operations and its
``_run`` body. Everything security-relevant lives *here*, so a new domain cannot ship
without it by accident:

1. **Named operations only.** ``allowed_operations`` is a frozenset of strings and the
   operation name selects a constant statement. There is no ``execute_sql`` and no route
   for SQL text through ``FetchSpec``.
2. **Every query runs in a READ ONLY transaction** with ``SET LOCAL statement_timeout``.
   If the role is ever over-privileged by mistake the transaction still refuses the write,
   and a runaway query cannot pin a connection.
3. **Identity comes from the verified context.** The subject is bound as ``$1`` from
   ``AuthContext``, never from ``spec.params``, so a model cannot ask about another
   customer. An empty subject is refused rather than treated as "everything".
4. **A row limit is always present, server-side, in the SQL.** Subclasses pass
   ``$2 = self.row_limit`` to their list statements.
5. **Failures are values, not exceptions.** A connector never kills the turn; a database
   error surfaces as a typed ``ConnectorStatus``, and its detail is logged, not returned.

RLS does not apply to this connection, the WHERE clause is the control.

That sentence is the whole security model of a direct PostgreSQL connection. A table owner
or a BYPASSRLS role reads every row, and the policies in ``supabase/migrations/0001`` only
govern the PostgREST path — they do not exist down here. Every statement a subclass runs
must therefore be scoped by ``auth_subject = $1`` (or the equivalent join). Never select a
whole table and filter in Python, and never add an operation whose WHERE clause is optional.

The DSN is not stored on the instance: only the pool factory closes over it, so it cannot
leak through a ``repr`` of the connector.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.interfaces.connector import (
    ConnectorHealth,
    ConnectorResult,
    ConnectorStatus,
    FetchSpec,
)
from app.domain.models.auth import AuthContext

logger = logging.getLogger(__name__)

#: Hard ceiling on rows per call, regardless of what the caller asks for.
MAX_ROW_LIMIT = 100


def build_read_only_pool_factory(dsn: str) -> Callable[[], Awaitable[Any]]:
    """Return a lazy ``asyncpg`` pool factory for ``dsn``.

    ``asyncpg`` is imported inside the factory because the ``[postgres]`` extra is
    optional: this module must import on an installation with no driver, so a missing
    package fails the health check instead of the process.
    """

    async def factory() -> Any:
        import asyncpg  # optional extra: imported at first use, never at module import

        return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)

    return factory


class PostgresReadOnlyConnector:
    """Read-only PostgreSQL access with the controls every domain must share.

    ``pool_factory`` is injected so unit tests can drive a connector without a server;
    the CI integration job exercises the real ``asyncpg`` pool.
    """

    name: str = "postgres-readonly"

    #: Operations a subclass answers. Empty by default: a domain that forgets to declare
    #: its operations answers nothing rather than everything.
    allowed_operations: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        dsn: str,
        row_limit: int = 50,
        statement_timeout_ms: int = 5_000,
        pool_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        if row_limit <= 0 or row_limit > MAX_ROW_LIMIT:
            msg = f"row_limit must be between 1 and {MAX_ROW_LIMIT}"
            raise ValueError(msg)
        if statement_timeout_ms <= 0:
            msg = "statement_timeout_ms must be positive"
            raise ValueError(msg)

        self._row_limit = row_limit
        self._statement_timeout_ms = statement_timeout_ms
        self._pool_factory = pool_factory or build_read_only_pool_factory(dsn)
        self._pool: Any | None = None

    @property
    def row_limit(self) -> int:
        """The server-side LIMIT subclasses bind as their last parameter."""
        return self._row_limit

    @property
    def statement_timeout_ms(self) -> int:
        return self._statement_timeout_ms

    async def _get_pool(self) -> Any:
        if self._pool is None:
            self._pool = await self._pool_factory()
        return self._pool

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def health(self) -> ConnectorHealth:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as connection:
                await connection.fetchval("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - health must answer, not raise
            logger.warning("%s unreachable: %s", self.name, type(exc).__name__)
            return ConnectorHealth(reachable=False, detail="database unreachable")
        return ConnectorHealth(reachable=True, detail="read-only connection ok")

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        """Run one named operation, scoped to the caller, bounded, never raising."""
        if spec.operation not in self.allowed_operations:
            logger.warning("%s refused operation=%r", self.name, spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE,
                message="Unsupported operation.",
            )

        subject = getattr(context, "subject", None)
        if not isinstance(subject, str) or not subject.strip():
            # Defensive: an empty subject would match nothing, but a missing identity
            # must never be treated as "show everything".
            return ConnectorResult(
                status=ConnectorStatus.UNAUTHORIZED, message="Missing caller identity."
            )

        try:
            pool = await self._get_pool()
            # READ ONLY + statement timeout, both scoped to this transaction, so a pooled
            # connection can neither inherit nor leak them to the next caller.
            async with (
                pool.acquire() as connection,
                connection.transaction(readonly=True),
            ):
                await connection.execute(
                    f"SET LOCAL statement_timeout = {self._statement_timeout_ms}"
                )
                return await self._run(connection, spec, subject)
        except TimeoutError:
            return ConnectorResult(
                status=ConnectorStatus.TIMEOUT, message="The database did not respond in time."
            )
        except Exception as exc:  # noqa: BLE001 - a connector never crashes the turn
            # asyncpg raises QueryCanceledError when statement_timeout fires; matched by
            # name because the driver is an optional dependency this module must not import.
            if type(exc).__name__ == "QueryCanceledError":
                return ConnectorResult(
                    status=ConnectorStatus.TIMEOUT,
                    message="The database did not respond in time.",
                )
            logger.exception("%s failed operation=%s", self.name, spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR, message="The database query failed."
            )

    async def _run(self, connection: Any, spec: FetchSpec, subject: str) -> ConnectorResult:
        """Execute the operation inside an already-read-only transaction.

        Implementations receive a verified, non-empty ``subject`` and may assume the
        transaction and timeout are set. They must not re-validate identity.
        """
        msg = f"{type(self).__name__} does not implement _run"
        raise NotImplementedError(msg)
