"""Reusable external REST connector with a fixed operation allowlist.

Configuration names operations, it does not grant reach. An operation is a fixed
``GET`` on a fixed path; there is no method field, no path interpolation and no URL
taken from the caller, so a compromised or confused model cannot turn this into a
request forgery primitive. Adding a third-party service is a code change with a review,
not an environment variable.

Bounded by design: a per-call timeout, a hard response-size cap enforced while streaming
(so a hostile or broken endpoint cannot exhaust memory), and JSON-object responses only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from app.domain.interfaces.connector import (
    ConnectorHealth,
    ConnectorResult,
    ConnectorStatus,
    FetchSpec,
)
from app.domain.models.auth import AuthContext

logger = logging.getLogger(__name__)

#: Parameter types allowed through to a third-party endpoint.
_SCALAR_TYPES = (str, int, float, bool)


class RestApiConnector:
    """Read-only GET connector over an allowlisted set of operations."""

    name = "rest-api"

    def __init__(
        self,
        *,
        base_url: str,
        operations: Mapping[str, str],
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 200_000,
        bearer_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            msg = "base_url must be an http(s) URL"
            raise ValueError(msg)
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            # Credentials and customer data must not cross the wire in clear text.
            msg = "plain http is only allowed for localhost"
            raise ValueError(msg)
        if not operations:
            msg = "at least one operation must be configured"
            raise ValueError(msg)

        for operation, path in operations.items():
            if not path.startswith("/") or "://" in path or ".." in path:
                msg = f"operation {operation!r} must be a fixed absolute path"
                raise ValueError(msg)

        self._base_url = base_url.rstrip("/")
        self._operations = dict(operations)
        self._max_response_bytes = max_response_bytes
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
            # No redirects: a 302 to another origin would escape the allowlist.
            follow_redirects=False,
        )
        if bearer_token:
            self._client.headers["Authorization"] = f"Bearer {bearer_token}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            reachable=True, detail=f"{len(self._operations)} operation(s) configured"
        )

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        del context  # identity is added by the backend that owns the credential

        path = self._operations.get(spec.operation)
        if path is None:
            logger.warning("rest connector refused operation=%r", spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE, message="Unsupported operation."
            )

        params: dict[str, Any] = {}
        for key, value in spec.params.items():
            if isinstance(value, _SCALAR_TYPES) and not isinstance(value, bytes):
                params[key] = value
            else:
                return ConnectorResult(
                    status=ConnectorStatus.INVALID_RESPONSE,
                    message=f"Parameter {key!r} must be a scalar value.",
                )

        url = f"{self._base_url}{path}"
        try:
            async with self._client.stream("GET", url, params=params) as response:
                if response.status_code >= 400:
                    return self._from_status(response.status_code)
                if 300 <= response.status_code < 400:
                    # Redirects are not followed (see follow_redirects=False): a 302 to
                    # another origin would step outside the allowlist. Say so plainly
                    # instead of letting the empty body fail JSON parsing.
                    logger.warning("rest connector refused a redirect operation=%s", spec.operation)
                    return ConnectorResult(
                        status=ConnectorStatus.UPSTREAM_ERROR,
                        message="The service redirected the request.",
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        logger.warning("rest connector response too large url=%s", path)
                        return ConnectorResult(
                            status=ConnectorStatus.INVALID_RESPONSE,
                            message="The response was too large.",
                        )
        except httpx.TimeoutException:
            return ConnectorResult(
                status=ConnectorStatus.TIMEOUT, message="The service did not respond in time."
            )
        except httpx.HTTPError:
            logger.exception("rest connector transport failure operation=%s", spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR, message="The service could not be reached."
            )

        try:
            payload = json.loads(bytes(body))
        except (ValueError, UnicodeDecodeError):
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE, message="The response was not JSON."
            )

        if not isinstance(payload, dict):
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE, message="The response was not an object."
            )

        return ConnectorResult(status=ConnectorStatus.OK, data=payload)

    @staticmethod
    def _from_status(status_code: int) -> ConnectorResult:
        """Map a third-party status onto our taxonomy without leaking their wording."""
        if status_code in {401, 403}:
            return ConnectorResult(
                status=ConnectorStatus.UNAUTHORIZED, message="The service refused access."
            )
        if status_code == 404:
            return ConnectorResult(status=ConnectorStatus.NOT_FOUND, message="Not found.")
        if status_code in {408, 504}:
            return ConnectorResult(
                status=ConnectorStatus.TIMEOUT, message="The service did not respond in time."
            )
        if status_code == 429:
            return ConnectorResult(
                status=ConnectorStatus.UPSTREAM_ERROR, message="The service is rate limiting."
            )
        return ConnectorResult(
            status=ConnectorStatus.UPSTREAM_ERROR, message="The service returned an error."
        )
