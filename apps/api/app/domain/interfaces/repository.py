"""The conversation repository port.

One rule shapes every signature here: **scope is a parameter, identity is not.** No
method accepts ``user_id`` or ``tenant_id`` — they accept an ``AuthContext`` and the
adapter derives the scope from it. That makes "query the wrong user's data" a
compile-time impossibility rather than a review finding, and it means an adapter
cannot be called safely with a forged owner.

Adapters must implement the same guarantees:

* Reads return only rows belonging to ``context``; anything else is *not found*,
  never "forbidden" (existence is not a public fact — SECURITY.md §3).
* Writes stamp ``context``'s subject and tenant; those are never taken from the
  payload.
* Connection or credential failures surface as ``RepositoryError``; the orchestration
  layer turns those into something a user can read.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, Conversation, StoredMessage


class RepositoryError(Exception):
    """Storage failed in a way the caller cannot fix by asking again."""


class ConversationNotFoundError(RepositoryError):
    """No such conversation *for this caller*.

    Also raised when a row exists but belongs to someone else: the caller must not
    be able to distinguish the two.
    """


@runtime_checkable
class ConversationRepository(Protocol):
    """Persistence for conversations and their messages, scoped to the caller."""

    async def create(self, *, context: AuthContext, title: str | None = None) -> Conversation:
        """Create an empty conversation owned by ``context``."""
        ...

    async def get(self, *, context: AuthContext, conversation_id: str) -> Conversation:
        """Return one conversation the caller owns.

        Raises:
            ConversationNotFoundError: unknown id, or owned by another caller.
        """
        ...

    async def list_recent(self, *, context: AuthContext, limit: int = 20) -> list[Conversation]:
        """The caller's conversations, most recently updated first."""
        ...

    async def append_message(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        message: ChatMessage,
    ) -> StoredMessage:
        """Append one turn to a conversation the caller owns.

        Raises:
            ConversationNotFoundError: unknown id, or owned by another caller.
        """
        ...

    async def list_messages(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        limit: int = 100,
    ) -> list[StoredMessage]:
        """The conversation's turns in chronological order."""
        ...

    async def set_title_if_absent(
        self,
        *,
        context: AuthContext,
        conversation_id: str,
        title: str,
    ) -> None:
        """Name a conversation from its first message.

        Deliberately "if absent": a title the user chose is theirs, and a derived one
        must never overwrite it. Implementations must scope the write to the caller.
        """
        ...

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
        """Append one audit record for a tool call.

        Stores a hash of the arguments, never the arguments themselves: the trail
        answers "what ran, when, how long, what happened" without becoming a second
        copy of customer data (SECURITY.md §8).
        """
        ...
