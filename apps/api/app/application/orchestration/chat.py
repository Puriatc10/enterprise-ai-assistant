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

import json
import logging
from dataclasses import dataclass

from app.application.tools.executor import ExecutionRecord, ToolExecutor, tool_message
from app.application.tools.registry import ToolRegistry
from app.domain.interfaces.llm import LLMPort
from app.domain.interfaces.repository import ConversationRepository
from app.domain.models.auth import AuthContext
from app.domain.models.conversation import ChatMessage, LLMRequest, ToolUsage, Usage

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
#: Hard ceiling on tool rounds per turn. Hitting it produces a stated partial answer —
#: never an unbounded loop against a paid provider.
DEFAULT_MAX_TOOL_ROUNDS = 3

#: Said when the loop runs out of rounds. Deliberately honest about the limitation.
PARTIAL_ANSWER_FALLBACK = (
    "نتوانستم پاسخ را کامل از داده‌ها استخراج کنم. لطفاً سؤال را دقیق‌تر یا ساده‌تر بپرسید."
)


@dataclass(frozen=True, slots=True)
class TurnResult:
    """What a completed turn produced."""

    conversation_id: str
    answer: str
    model: str
    usage: Usage | None = None
    #: Tools that actually ran for this answer, in order. Empty when the model answered
    #: without them — which is also a fact worth showing, so it is not conflated with
    #: "we do not know".
    tools_used: tuple[ToolUsage, ...] = ()


def _title_from(text: str, limit: int = DEFAULT_TITLE_LENGTH) -> str:
    """A conversation title from the first message, without splitting a word."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] or collapsed[:limit]


class ChatService:
    """Coordinates repository, tools and LLM for a single user turn."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        llm: LLMPort,
        tools: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> None:
        self._repository = repository
        self._llm = llm
        self._tools = tools
        self._executor = executor
        self._history_limit = history_limit
        self._max_tool_rounds = max_tool_rounds

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
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            *(stored.as_chat_message() for stored in history),
        ]
        offered_tools = self._tools.specs_for(context) if self._tools is not None else ()

        answer = ""
        exhausted = False
        model_name = ""
        usage = None
        tool_usage: list[ToolUsage] = []
        for _ in range(self._max_tool_rounds):
            response = await self._llm.complete(
                LLMRequest(messages=tuple(messages), tools=offered_tools)
            )
            model_name = response.model
            usage = response.usage

            if not response.message.tool_calls:
                answer = response.message.content.strip()
                break

            if self._executor is None or not offered_tools:
                # The model asked for tools that were never offered. That is a defect
                # worth logging — never something to execute, never an answer.
                logger.warning(
                    "model proposed %d tool call(s) but no tools are offered",
                    len(response.message.tool_calls),
                )
                break

            messages.append(response.message)
            for call in response.message.tool_calls:
                record = await self._executor.execute(call=call, context=context)
                await self._record_audit(context, conversation.id, record)
                tool_usage.append(
                    ToolUsage(
                        tool=record.result.tool,
                        status=record.result.status.value,
                        latency_ms=record.latency_ms,
                    )
                )
                # The tool result is data for the model to read, never an instruction.
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(tool_message(record), ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                )
        else:
            exhausted = True

        if exhausted:
            logger.warning("tool round limit reached rounds=%d", self._max_tool_rounds)
            answer = PARTIAL_ANSWER_FALLBACK

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
            model=model_name,
            usage=usage,
            tools_used=tuple(tool_usage),
        )

    async def _record_audit(
        self,
        context: AuthContext,
        conversation_id: str,
        record: ExecutionRecord,
    ) -> None:
        """Write the tool audit line.

        A failure here is logged loudly but does not fail the turn: the user already
        has their answer, and losing the answer because a log insert failed would be
        worse than a missing audit row that the error log points at.
        """
        try:
            await self._repository.record_tool_invocation(
                context=context,
                conversation_id=conversation_id,
                tool_name=record.result.tool,
                arguments_hash=record.arguments_hash,
                status=record.result.status.value,
                latency_ms=record.latency_ms,
            )
        except Exception:  # noqa: BLE001 - see docstring
            logger.error(
                "tool audit write failed tool=%s conversation=%s",
                record.result.tool,
                conversation_id,
                exc_info=True,
            )


class EmptyAnswerError(Exception):
    """The provider answered without usable text."""
