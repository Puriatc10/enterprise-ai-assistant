"""Supabase (PostgREST) conversation repository.

The important property: every request carries **the caller's own access token**, not
a privileged key. PostgREST then applies the row-level policies from
`supabase/migrations/0001_init.sql`, so the database enforces the same scope the
backend does. We hold no `service_role` key, which means a compromised backend still
cannot read across tenants.

Error handling follows one rule: a storage refusal (401/403, or Postgres `42501`
surfacing through RLS) is reported as *not found*, never as *forbidden*. Leaking the
difference tells a caller that an id exists.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx

from app.domain.interfaces.repository import ConversationNotFoundError, RepositoryError
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, Conversation, StoredMessage

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    # PostgREST returns RFC 3339 with a trailing Z, which fromisoformat predates.
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _to_conversation(row: dict[str, Any]) -> Conversation:
    return Conversation(
        id=str(row["id"]),
        title=row.get("title"),
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
    )


def _to_message(row: dict[str, Any]) -> StoredMessage:
    return StoredMessage(
        id=str(row["id"]),
        role=row["role"],
        content=row.get("content") or "",
        tool_call_id=row.get("tool_call_id"),
        created_at=_parse_timestamp(row["created_at"]),
    )


class SupabaseConversationRepository:
    """PostgREST adapter, scoped by the caller's token.

    Args:
        base_url: Project URL, e.g. ``https://<ref>.supabase.co``.
        api_key: The publishable/anon key — PostgREST requires it as ``apikey``.
        access_token: The caller's verified JWT. This is what RLS evaluates.
        timeout_seconds: Per-request timeout.
        client: Injected ``httpx.AsyncClient`` (tests use a ``MockTransport``).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        access_token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds
        self._client = client

    # -- plumbing ---------------------------------------------------------
    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._api_key,
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            yield client

    def _url(self, table: str) -> str:
        return f"{self._base_url}/rest/v1/{table}"

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        # 401/403 from PostgREST means our token was refused — for a read this is
        # indistinguishable from "no such row", and that ambiguity is deliberate.
        if status in (401, 403):
            raise ConversationNotFoundError("storage refused the request")
        logger.warning("supabase request failed: status=%s table=%s", status, response.request.url)
        raise RepositoryError(f"Storage request failed with status {status}.")

    def _rows(self, response: httpx.Response) -> list[dict[str, Any]]:
        self._raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, list):
            raise RepositoryError("Storage returned an unexpected payload shape.")
        return payload

    # -- port -------------------------------------------------------------
    async def create(self, *, context: AuthContext, title: str | None = None) -> Conversation:
        payload = {
            "user_id": context.subject,
            "tenant_id": context.tenant_id,
            "title": title,
        }
        async with self._session() as client:
            response = await client.post(
                self._url("conversations"),
                json=payload,
                headers=self._headers(prefer="return=representation"),
            )
        rows = self._rows(response)
        if not rows:
            raise RepositoryError("Insert returned no row.")
        return _to_conversation(rows[0])

    async def get(self, *, context: AuthContext, conversation_id: str) -> Conversation:
        del context  # scope is enforced by RLS via the forwarded token
        params = {"select": "*", "id": f"eq.{conversation_id}", "limit": "1"}
        async with self._session() as client:
            response = await client.get(
                self._url("conversations"), params=params, headers=self._headers()
            )
        rows = self._rows(response)
        if not rows:
            raise ConversationNotFoundError(conversation_id)
        return _to_conversation(rows[0])

    async def list_recent(
        self,
        *,
        context: AuthContext,
        limit: int = 20,
        before: datetime | None = None,
    ) -> list[Conversation]:
        del context
        params = {"select": "*", "order": "updated_at.desc", "limit": str(limit)}
        if before is not None:
            # Keyset, not offset: a conversation updated mid-scroll must not shift the page.
            params["updated_at"] = f"lt.{before.isoformat()}"
        async with self._session() as client:
            response = await client.get(
                self._url("conversations"), params=params, headers=self._headers()
            )
        return [_to_conversation(row) for row in self._rows(response)]

    async def rename(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        title: str,
    ) -> Conversation:
        del context  # RLS scopes the update to the caller's rows
        async with self._session() as client:
            response = await client.patch(
                self._url("conversations"),
                params={"id": f"eq.{conversation_id}", "select": "*"},
                json={"title": title},
                headers=self._headers(prefer="return=representation"),
            )
        rows = self._rows(response)
        if not rows:
            # A rename that matched no row is "not yours" or "not there" — the caller must
            # not be able to tell which.
            raise ConversationNotFoundError("No such conversation.")
        return _to_conversation(rows[0])

    async def delete(self, *, context: AuthContext, conversation_id: str) -> None:
        del context  # RLS scopes the delete to the caller's rows
        async with self._session() as client:
            response = await client.delete(
                self._url("conversations"),
                params={"id": f"eq.{conversation_id}", "select": "id"},
                headers=self._headers(prefer="return=representation"),
            )
        if not self._rows(response):
            raise ConversationNotFoundError("No such conversation.")

    async def append_message(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        message: ChatMessage,
    ) -> StoredMessage:
        payload = {
            "conversation_id": conversation_id,
            "user_id": context.subject,
            "tenant_id": context.tenant_id,
            "role": message.role,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
        async with self._session() as client:
            response = await client.post(
                self._url("messages"),
                json=payload,
                headers=self._headers(prefer="return=representation"),
            )
        rows = self._rows(response)
        if not rows:
            raise RepositoryError("Insert returned no row.")
        return _to_message(rows[0])

    async def list_messages(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        limit: int = 100,
    ) -> list[StoredMessage]:
        del context
        params = {
            "select": "*",
            "conversation_id": f"eq.{conversation_id}",
            "order": "created_at.asc",
            "limit": str(limit),
        }
        async with self._session() as client:
            response = await client.get(
                self._url("messages"), params=params, headers=self._headers()
            )
        return [_to_message(row) for row in self._rows(response)]

    async def set_title_if_absent(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        title: str,
    ) -> None:
        # `title=is.null` is the PostgREST filter, not a Python default: the row is
        # only touched while it is still unnamed. RLS (conversations_update_own) and
        # the updated_at trigger do the rest.
        params = {"id": f"eq.{conversation_id}", "title": "is.null"}
        async with self._session() as client:
            response = await client.patch(
                self._url("conversations"),
                params=params,
                json={"title": title},
                headers=self._headers(prefer="return=minimal"),
            )
        self._raise_for_status(response)

    async def record_tool_invocation(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        tool_name: str,
        arguments_hash: str,
        status: str,
        latency_ms: int,
    ) -> None:
        payload = {
            "conversation_id": conversation_id,
            "user_id": context.subject,
            "tenant_id": context.tenant_id,
            "tool_name": tool_name,
            "arguments_hash": arguments_hash,
            "status": status,
            "latency_ms": latency_ms,
        }
        async with self._session() as client:
            response = await client.post(
                self._url("tool_invocations"),
                json=payload,
                headers=self._headers(prefer="return=minimal"),
            )
        # No body is returned; only the status matters here.
        self._raise_for_status(response)
