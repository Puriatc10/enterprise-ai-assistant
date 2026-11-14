"""Tool executor: the single choke point between a proposal and a side effect.

The model's tool call is untrusted input. This module turns it into either a validated
result or a typed refusal, in this order — and the order matters, because each step
assumes the previous one held:

1. **Allowlist** — an unknown name stops here. No "best effort" lookup.
2. **Scope** — the caller must hold what the tool declares.
3. **Arguments** — validated against the tool's Pydantic model, which forbids extras,
   so an identity-shaped field cannot be smuggled in.
4. **Identity** — supplied from ``AuthContext``, never from the call. The handler
   receives the context first; there is no code path where model-supplied identity
   reaches a data source.
5. **Timeout** — every call is bounded.
6. **Output** — validated when the tool declares a result model.
7. **Audit** — the caller records it; the executor returns what to record.

Failures are returned as data (``ToolResult``), not raised, because the model needs to
explain them to a user. The one exception is programmer error, which would be a bug in
this file rather than a provider problem.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.application.tools.registry import ToolDefinition, ToolRegistry
from app.domain.models.auth import AuthContext
from app.domain.models.tools import ToolCall, ToolResult, ToolStatus

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 200


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Stable, non-reversible fingerprint for the audit trail.

    The audit table stores this instead of the arguments: enough to correlate and to
    detect a repeat, without becoming a copy of whatever the user asked about
    (docs/architecture/SECURITY.md §8).
    """
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _short(text: str) -> str:
    collapsed = " ".join(text.split())
    return (
        collapsed
        if len(collapsed) <= _MAX_MESSAGE_LENGTH
        else collapsed[: _MAX_MESSAGE_LENGTH - 1] + "…"
    )


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """What happened for one call — the unit of the audit trail."""

    call: ToolCall
    result: ToolResult
    latency_ms: int

    @property
    def arguments_hash(self) -> str:
        return hash_arguments(self.call.arguments)


class ToolRefusal(Exception):
    """Raised by a handler to report an expected, non-exceptional failure.

    A missing policy is not a bug, so it must not be logged as one or reported as an
    upstream error. Handlers raise this with the status the model should see.
    """

    def __init__(self, status: ToolStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ToolExecutor:
    """Executes validated, authorized tool calls with a hard timeout."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._registry = registry
        self._clock = clock

    async def execute(self, *, call: ToolCall, context: AuthContext) -> ExecutionRecord:
        """Run one call, returning a typed result rather than raising."""
        started = self._clock()

        def record(result: ToolResult) -> ExecutionRecord:
            return ExecutionRecord(
                call=call,
                result=result,
                latency_ms=int((self._clock() - started) * 1000),
            )

        tool = self._registry.get(call.name)
        if tool is None:
            # Do not echo the requested name back as an invitation to retry variants.
            logger.warning("rejected unknown tool call name=%r", call.name)
            return record(
                ToolResult(
                    call_id=call.id,
                    tool=call.name,
                    status=ToolStatus.INVALID_REQUEST,
                    message="No such tool is available.",
                )
            )

        if not context.has_scopes(tool.required_scopes):
            logger.warning(
                "rejected tool call for insufficient scope tool=%s subject=%s",
                tool.name,
                context.subject,
            )
            return record(
                ToolResult(
                    call_id=call.id,
                    tool=tool.name,
                    status=ToolStatus.FORBIDDEN,
                    message="You are not allowed to use this tool.",
                )
            )

        try:
            arguments = tool.arguments_model.model_validate(call.arguments)
        except ValidationError as exc:
            # Field names are safe to return; values are not echoed.
            fields = ", ".join(
                ".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()
            )
            return record(
                ToolResult(
                    call_id=call.id,
                    tool=tool.name,
                    status=ToolStatus.INVALID_REQUEST,
                    message=f"Invalid arguments: {fields}",
                )
            )

        try:
            raw = await asyncio.wait_for(
                tool.handler(context, arguments), timeout=tool.timeout_seconds
            )
        except ToolRefusal as refusal:
            # An expected outcome (not found, not permitted): a fact, not a fault.
            return record(
                ToolResult(
                    call_id=call.id,
                    tool=tool.name,
                    status=refusal.status,
                    message=refusal.message,
                )
            )
        except TimeoutError:
            logger.warning("tool timed out tool=%s timeout=%ss", tool.name, tool.timeout_seconds)
            return record(
                ToolResult(
                    call_id=call.id,
                    tool=tool.name,
                    status=ToolStatus.TIMEOUT,
                    message="The data source did not respond in time.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - a tool failure must not kill the turn
            logger.exception("tool handler failed tool=%s", tool.name, exc_info=exc)
            return record(
                ToolResult(
                    call_id=call.id,
                    tool=tool.name,
                    status=ToolStatus.UPSTREAM_ERROR,
                    message="The data source failed.",
                )
            )

        data = self._serialise(raw)
        if tool.result_model is not None:
            try:
                data = tool.result_model.model_validate(data).model_dump(mode="json")
            except ValidationError:
                logger.error("tool returned an invalid payload tool=%s", tool.name)
                return record(
                    ToolResult(
                        call_id=call.id,
                        tool=tool.name,
                        status=ToolStatus.INVALID_RESPONSE,
                        message="The data source returned an unexpected payload.",
                    )
                )

        return record(ToolResult(call_id=call.id, tool=tool.name, status=ToolStatus.OK, data=data))

    @staticmethod
    def _serialise(value: Any) -> Any:
        """Make a handler result JSON-safe for the model and the audit trail."""
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {key: ToolExecutor._serialise(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ToolExecutor._serialise(item) for item in value]
        return value


def tool_message(record: ExecutionRecord) -> dict[str, Any]:
    """Provider-neutral payload describing one executed call.

    Errors are described in words the model can relay ("not found", "timed out")
    rather than as an opaque failure, which is what keeps the assistant honest.
    """
    if record.result.ok:
        return {"status": "ok", "data": record.result.data}
    return {
        "status": record.result.status.value,
        "message": _short(record.result.message or "The tool failed."),
    }


__all__ = ["ExecutionRecord", "ToolExecutor", "ToolDefinition", "hash_arguments", "tool_message"]
