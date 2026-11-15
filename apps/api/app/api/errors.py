"""One consistent failure shape, and a request id on every response.

Clients get ``{"error": {"code", "message", "request_id"}}`` and nothing else — no
provider JSON, no stack traces, no internals. The ``request_id`` is the only thing
that ties a user's screenshot to a log line, which is why it is echoed in the header
as well.

Status choices are deliberate, and each one means something different to the caller:

| Failure | Status | Why |
|---|---|---|
| model rate-limited | 429 | retryable, and the client should back off |
| model timed out | 504 | upstream took too long |
| model unavailable / bad response | 502 | upstream failed, not us |
| our provider credentials are wrong | 503 | configuration problem, not the caller's |
| conversation not found / not yours | 404 | existence is not a public fact |
| storage failed | 502 | upstream |
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.application.orchestration.chat import EmptyAnswerError
from app.domain.interfaces.llm import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.domain.interfaces.repository import ConversationNotFoundError, RepositoryError
from app.security.rate_limit import RateLimitExceeded

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request id: reuse the caller's, otherwise mint one."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """Attach `X-Response-Time` and log one line per request.

    The line carries the request id, so a user's screenshot resolves to a log entry that
    also says how long the backend took — the first question when someone reports "it
    hung". For a streaming response this measures time-to-headers, not the whole stream;
    the stream's own events are what describe the rest.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Response-Time"] = f"{elapsed_ms}ms"
        logger.info(
            "request method=%s path=%s status=%d duration_ms=%d request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            _request_id(request),
        )
        return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def _envelope(
    *,
    status_code: int,
    code: str,
    message: str,
    request: Request,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": _request_id(request),
        }
    }
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def _llm_failure(exc: LLMError) -> tuple[int, str]:
    """Map the port's taxonomy onto status + a stable code."""
    if isinstance(exc, LLMRateLimitError):
        return status.HTTP_429_TOO_MANY_REQUESTS, "llm_rate_limited"
    if isinstance(exc, LLMTimeoutError):
        return status.HTTP_504_GATEWAY_TIMEOUT, "llm_timeout"
    if isinstance(exc, LLMAuthError):
        # Our credential/entitlement problem: the caller did nothing wrong.
        return status.HTTP_503_SERVICE_UNAVAILABLE, "llm_not_configured"
    if isinstance(exc, (LLMUnavailableError, LLMInvalidResponseError)):
        return status.HTTP_502_BAD_GATEWAY, "llm_unavailable"
    return status.HTTP_502_BAD_GATEWAY, "llm_error"


def install_error_handlers(application: FastAPI) -> None:
    """Register the handlers that produce the envelope."""

    @application.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        # `detail` here is ours (we wrote it), never provider output.
        return _envelope(
            status_code=exc.status_code,
            code=f"http_{exc.status_code}",
            message=str(exc.detail),
            request=request,
            headers=dict(exc.headers) if exc.headers else None,
        )

    @application.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field-level detail is safe and useful; values are not echoed back.
        fields = ", ".join(
            ".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()
        )
        return _envelope(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message=f"Invalid request: {fields}",
            request=request,
        )

    @application.exception_handler(ConversationNotFoundError)
    async def _not_found(request: Request, exc: ConversationNotFoundError) -> JSONResponse:
        return _envelope(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message="No such conversation.",
            request=request,
        )

    @application.exception_handler(EmptyAnswerError)
    async def _empty_answer(request: Request, exc: EmptyAnswerError) -> JSONResponse:
        return _envelope(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="empty_answer",
            message="The assistant produced no answer. Please try again.",
            request=request,
        )

    @application.exception_handler(RepositoryError)
    async def _storage_failure(request: Request, exc: RepositoryError) -> JSONResponse:
        logger.error("storage failure request_id=%s", _request_id(request), exc_info=exc)
        return _envelope(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="storage_error",
            message="Storage is unavailable right now.",
            request=request,
        )

    @application.exception_handler(RateLimitExceeded)
    async def _rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        # Retry-After is the only actionable part of a 429; the body stays in the same
        # envelope so the client has one error shape to parse.
        return _envelope(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="rate_limited",
            message="Too many questions in a short time. Please wait a moment.",
            request=request,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @application.exception_handler(LLMError)
    async def _llm_failure_handler(request: Request, exc: LLMError) -> JSONResponse:
        status_code, code = _llm_failure(exc)
        # The provider's message is logged, not returned: it can contain account ids
        # and endpoint details.
        logger.warning(
            "llm failure request_id=%s code=%s detail=%s",
            _request_id(request),
            code,
            type(exc).__name__,
        )
        return _envelope(
            status_code=status_code,
            code=code,
            message="The assistant is unavailable right now. Please try again.",
            request=request,
        )
