"""In-memory conversation repository: tests, and local runs without Supabase.

It enforces the **same scoping rules as the real adapter on purpose**. A fake that
skips the ownership filter is worse than no fake: every test above it passes, and
the production adapter's scoping bug ships unnoticed.

Nothing here is suitable for production: state is per-process and lost on restart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.interfaces.repository import ConversationNotFoundError
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, Conversation, StoredMessage


@dataclass(slots=True)
class _ConversationRow:
    id: str
    user_id: str
    tenant_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class _MessageRow:
    id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    role: str
    content: str
    tool_call_id: str | None
    created_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InMemoryConversationRepository:
    """Dict-backed ``ConversationRepository`` with caller-scoped access."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._conversations: dict[str, _ConversationRow] = {}
        self._messages: list[_MessageRow] = []
        self._audit: list[dict[str, object]] = []

    # -- internals --------------------------------------------------------
    @staticmethod
    def _to_conversation(row: _ConversationRow) -> Conversation:
        return Conversation(
            id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
        )

    def _owned(self, context: AuthContext, conversation_id: str) -> _ConversationRow:
        row = self._conversations.get(conversation_id)
        # One error for "does not exist" and "belongs to someone else": the caller
        # must not be able to tell them apart (SECURITY.md §3).
        if row is None or row.user_id != context.subject or row.tenant_id != context.tenant_id:
            raise ConversationNotFoundError(conversation_id)
        return row

    # -- port -------------------------------------------------------------
    async def create(self, *, context: AuthContext, title: str | None = None) -> Conversation:
        now = self._clock()
        row = _ConversationRow(
            id=str(uuid4()),
            user_id=context.subject,
            tenant_id=context.tenant_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._conversations[row.id] = row
        return self._to_conversation(row)

    async def get(self, *, context: AuthContext, conversation_id: str) -> Conversation:
        return self._to_conversation(self._owned(context, conversation_id))

    async def list_recent(
        self,
        *,
        context: AuthContext,
        limit: int = 20,
        before: datetime | None = None,
    ) -> list[Conversation]:
        owned = [
            row
            for row in self._conversations.values()
            if row.user_id == context.subject and row.tenant_id == context.tenant_id
        ]
        if before is not None:
            owned = [row for row in owned if row.updated_at < before]
        owned.sort(key=lambda row: row.updated_at, reverse=True)
        return [self._to_conversation(row) for row in owned[:limit]]

    async def rename(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        title: str,
    ) -> Conversation:
        row = self._owned(context, conversation_id)
        row.title = title
        row.updated_at = self._clock()
        return self._to_conversation(row)

    async def delete(self, *, context: AuthContext, conversation_id: str) -> None:
        row = self._owned(context, conversation_id)
        # Messages and audit rows go with it: the database does this with ON DELETE
        # CASCADE, so the in-memory adapter must not quietly behave differently.
        self._messages = [
            message for message in self._messages if message.conversation_id != row.id
        ]
        self._audit = [entry for entry in self._audit if entry.get("conversation_id") != row.id]
        del self._conversations[row.id]

    async def append_message(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        message: ChatMessage,
    ) -> StoredMessage:
        conversation = self._owned(context, conversation_id)
        now = self._clock()
        row = _MessageRow(
            id=str(uuid4()),
            conversation_id=conversation.id,
            user_id=context.subject,
            tenant_id=context.tenant_id,
            role=message.role,
            content=message.content,
            tool_call_id=message.tool_call_id,
            created_at=now,
        )
        self._messages.append(row)
        # Mirrors the `touch_updated_at` trigger on the real table.
        conversation.updated_at = now
        return StoredMessage(
            id=row.id,
            role=row.role,  # type: ignore[arg-type]
            content=row.content,
            tool_call_id=row.tool_call_id,
            created_at=row.created_at,
        )

    async def list_messages(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        limit: int = 100,
    ) -> list[StoredMessage]:
        conversation = self._owned(context, conversation_id)
        rows = [
            row
            for row in self._messages
            if row.conversation_id == conversation.id
            and row.user_id == context.subject
            and row.tenant_id == context.tenant_id
        ]
        rows.sort(key=lambda row: row.created_at)
        return [
            StoredMessage(
                id=row.id,
                role=row.role,  # type: ignore[arg-type]
                content=row.content,
                tool_call_id=row.tool_call_id,
                created_at=row.created_at,
            )
            for row in rows[:limit]
        ]

    async def set_title_if_absent(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        title: str,
    ) -> None:
        row = self._owned(context, conversation_id)
        if row.title:
            return
        row.title = title
        row.updated_at = self._clock()

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
        self._owned(context, conversation_id)  # you may only audit inside your own thread
        self._audit.append(
            {
                "conversation_id": conversation_id,
                "user_id": context.subject,
                "tenant_id": context.tenant_id,
                "tool_name": tool_name,
                "arguments_hash": arguments_hash,
                "status": status,
                "latency_ms": latency_ms,
                "created_at": self._clock(),
            }
        )

    @property
    def audit_records(self) -> list[dict[str, object]]:
        """Exposed for tests: the audit trail as recorded."""
        return list(self._audit)
