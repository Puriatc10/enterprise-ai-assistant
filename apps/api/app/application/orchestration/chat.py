"""Conversation orchestration: one user turn, one model round, both persisted.

Deliberately small. Phase 3 adds the tool loop on top of this; what matters here is
that the *shape* is right from the start:

* the conversation is loaded through the repository with the caller's ``AuthContext``,
  so an unowned conversation is indistinguishable from a missing one;
* the turn is persisted before the model is called, so a provider failure still
  leaves an auditable record of what the user asked;
* the answer is persisted only if the model actually produced one;
* nothing in this layer knows which provider is behind ``LLMPort``.

History is trimmed by the repository (not here) and the system prompt is a constant,
so prompt changes are reviewable in one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.domain.interfaces.llm import LLMPort
from app.domain.interfaces.repository import ConversationRepository
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMRequest, Usage

logger = logging.getLogger(__name__)

#: Grounding rules the model must follow. Rule 4 exists because Phase 1 measured a
#: real failure: this model family leaks foreign-script words into Persian answers
#: (`которых`, `פוליס`), so the language constraint is stated explicitly rather than
#: assumed (docs/analysis/PHASE1-LLM.md §3).
SYSTEM_PROMPT = (
    "تو دستیار بیمه‌ای یک سازمان هستی و با کاربر فارسی‌زبان صحبت می‌کنی.\n"
    "قواعد الزامی:\n"
    "۱) فقط بر پایهٔ داده‌ای پاسخ بده که ابزارها در اختیارت می‌گذارند. هرگز عدد، تاریخ، "
    "مبلغ یا نام بیمه‌نامه از خودت نساز.\n"
    "۲) اگر داده‌ای در دسترس نیست، صریح بگو که نمی‌دانی؛ حدس نزن.\n"
    "۳) هویت کاربر و سطح دسترسی او را تعیین نکن؛ این‌ها از سمت سرور می‌آید.\n"
    "۴) پاسخ را فقط و فقط به خط و زبان فارسی بنویس و هیچ واژه یا نویسهٔ غیرفارسی به کار نبر.\n"
    "۵) کوتاه، دقیق و محترمانه پاسخ بده."
)

DEFAULT_HISTORY_LIMIT = 50
DEFAULT_TITLE_LENGTH = 60


@dataclass(frozen=True, slots=True)
class TurnResult:
    """What a completed turn produced."""

    conversation_id: str
    answer: str
    model: str
    usage: Usage | None = None


def _title_from(text: str, limit: int = DEFAULT_TITLE_LENGTH) -> str:
    """A conversation title from the first message, without splitting a word."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] or collapsed[:limit]


class ChatService:
    """Coordinates repository and LLM for a single user turn."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        llm: LLMPort,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        self._repository = repository
        self._llm = llm
        self._history_limit = history_limit

    async def send_message(
        self,
        *,
        context: AuthContext,
        message: str,
        conversation_id: str | None = None,
    ) -> TurnResult:
        """Append the user's message, ask the model, persist the answer.

        Args:
            context: Verified caller identity; the only source of scope.
            message: The user's text.
            conversation_id: Existing conversation, or ``None`` to start one.

        Raises:
            ValueError: the message is blank.
            ConversationNotFoundError: unknown id, or owned by another caller.
            LLMError: the provider failed; the user turn is already persisted.
        """
        text = message.strip()
        if not text:
            raise ValueError("Message must not be empty.")

        if conversation_id is None:
            conversation = await self._repository.create(context=context, title=_title_from(text))
        else:
            conversation = await self._repository.get(
                context=context, conversation_id=conversation_id
            )

        # Persist first: if the provider then fails, we still know what was asked.
        await self._repository.append_message(
            context=context,
            conversation_id=conversation.id,
            message=ChatMessage(role="user", content=text),
        )

        history = await self._repository.list_messages(
            context=context,
            conversation_id=conversation.id,
            limit=self._history_limit,
        )
        request = LLMRequest(
            messages=(
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                *(stored.as_chat_message() for stored in history),
            )
        )

        response = await self._llm.complete(request)

        if response.message.tool_calls:
            # No registry is wired yet (Phase 3). Record it rather than dropping the
            # call silently: a model inventing tool calls is worth seeing in logs.
            logger.warning(
                "model proposed %d tool call(s) but no registry is wired yet; ignoring",
                len(response.message.tool_calls),
            )

        answer = response.message.content.strip()
        if not answer:
            # Do not persist an empty assistant turn: it would replay as a blank
            # message and make the conversation look broken forever.
            raise EmptyAnswerError("The model returned an empty answer.")

        await self._repository.append_message(
            context=context,
            conversation_id=conversation.id,
            message=ChatMessage(role="assistant", content=answer),
        )

        return TurnResult(
            conversation_id=conversation.id,
            answer=answer,
            model=response.model,
            usage=response.usage,
        )


class EmptyAnswerError(Exception):
    """The provider answered without usable text."""
