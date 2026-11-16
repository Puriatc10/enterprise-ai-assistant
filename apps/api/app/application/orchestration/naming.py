"""Conversation titles written by the model.

A title is a *presentation* concern: nothing downstream reads it, nothing is authorized by
it. That is why every failure here degrades to the truncated first message instead of
failing a turn, and why the call is bounded by its own timeout rather than the turn's.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

from app.domain.interfaces.llm import LLMPort
from app.domain.models.conversation import ChatMessage, LLMRequest

logger = logging.getLogger(__name__)

#: Distinguishable on purpose: the scripted test adapter recognises a short, tool-free
#: request by its shape and must not consume a scripted turn for it.
TITLE_SYSTEM_PROMPT = (
    "تو فقط یک عنوان‌ساز هستی. برای پیام کاربر یک عنوان کوتاه فارسی بنویس که موضوع را "
    "برساند.\n"
    "قواعد: حداکثر ۶ واژه؛ فقط عنوان؛ بدون گیومه؛ بدون نقطه یا علامت پایان جمله؛ "
    "بدون توضیح؛ بدون برچسب مانند «عنوان:»."
)

MAX_TITLE_LENGTH = 60
MAX_TITLE_WORDS = 8
DEFAULT_TITLE_TIMEOUT_SECONDS = 15.0
#: Enough for a six-word Persian title and nothing else. A model that wants to explain
#: itself gets cut off, which `clean_title` then rejects.
#:
#: Not 32. Measured against the configured model: at 32 tokens the call returned
#: ``finish_reason="length"`` with the title truncated mid-word («تعداد بیمه‌نامه‌ها و
#: تیک»), which the adapter correctly treats as a failed answer. Reasoning models spend
#: part of the budget before writing, so 200 buys a complete title in ~1.5 s.
TITLE_MAX_TOKENS = 200


class ConversationNamer(Protocol):
    """The one thing the orchestrator needs from a namer."""

    async def suggest(self, text: str) -> str | None:
        """Return a short title for ``text``, or ``None`` to use the fallback."""
        ...


class LLMConversationNamer:
    """Ask the model for a title, once per conversation, and never block the answer."""

    def __init__(
        self,
        llm: LLMPort,
        *,
        timeout_seconds: float = DEFAULT_TITLE_TIMEOUT_SECONDS,
    ) -> None:
        self._llm = llm
        self._timeout_seconds = timeout_seconds

    async def suggest(self, text: str) -> str | None:
        cleaned = text.strip()
        if not cleaned:
            return None

        request = LLMRequest(
            messages=(
                ChatMessage(role="system", content=TITLE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=cleaned[:500]),
            ),
            temperature=0.3,
            max_tokens=TITLE_MAX_TOKENS,
        )
        try:
            response = await asyncio.wait_for(
                self._llm.complete(request), timeout=self._timeout_seconds
            )
        except Exception:  # noqa: BLE001 - cosmetic, see module docstring
            logger.info("title suggestion failed; falling back to the first message")
            return None

        return clean_title(response.message.content)


def clean_title(raw: str | None) -> str | None:
    """Normalise a model-written title, or reject it.

    Rejects rather than repairs: a title derived from the first message is always
    available, so there is no reason to ship a paragraph that begins with «عنوان:».
    """
    if not raw:
        return None

    first_line = raw.strip().splitlines()[0] if raw.strip() else ""
    stripped = first_line.strip(" \t\"'«»“”`*#:.-–—")
    # Models label their output even when told not to. Dropping the label is cheaper than
    # discarding an otherwise good title.
    labelled = re.sub(r"^(?:عنوان|title)\s*[:：]\s*", "", stripped, flags=re.IGNORECASE)
    collapsed = " ".join(labelled.split())
    if not collapsed:
        return None
    if len(collapsed.split()) > MAX_TITLE_WORDS:
        return None
    if len(collapsed) > MAX_TITLE_LENGTH:
        collapsed = collapsed[:MAX_TITLE_LENGTH].rstrip()
    return collapsed or None
