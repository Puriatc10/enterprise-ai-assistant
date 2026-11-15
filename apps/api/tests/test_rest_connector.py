"""External REST connector.

Every case here is a way an allowlisted call can go wrong — a refused operation, a
non-scalar parameter, an oversized body, a redirect, a third-party 5xx — and each must
arrive as a typed status rather than an exception or, worse, as data.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.interfaces.connector import ConnectorStatus, FetchSpec
from app.domain.models.auth import AuthContext
from app.infrastructure.connectors.external_api.rest import RestApiConnector

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")


def _connector(handler, **kwargs) -> RestApiConnector:
    return RestApiConnector(
        base_url="https://api.example.test",
        operations={"orders": "/v1/orders", "policy": "/v1/policies"},
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _json_handler(payload: dict, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler


async def test_json_object_is_returned_as_data() -> None:
    connector = _connector(_json_handler({"items": [1, 2]}))

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is ConnectorStatus.OK
    assert result.data == {"items": [1, 2]}
    await connector.aclose()


async def test_unlisted_operation_never_reaches_the_network() -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    connector = _connector(handler)

    result = await connector.fetch(FetchSpec(operation="delete_everything"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    assert called == []
    await connector.aclose()


async def test_non_scalar_parameter_is_refused() -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    connector = _connector(handler)

    result = await connector.fetch(
        FetchSpec(operation="orders", params={"filter": {"$ne": None}}), ALICE
    )

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    assert called == []
    await connector.aclose()


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, ConnectorStatus.UNAUTHORIZED),
        (403, ConnectorStatus.UNAUTHORIZED),
        (404, ConnectorStatus.NOT_FOUND),
        (408, ConnectorStatus.TIMEOUT),
        (429, ConnectorStatus.UPSTREAM_ERROR),
        (500, ConnectorStatus.UPSTREAM_ERROR),
        (503, ConnectorStatus.UPSTREAM_ERROR),
    ],
)
async def test_third_party_statuses_map_onto_our_taxonomy(
    status_code: int, expected: ConnectorStatus
) -> None:
    connector = _connector(lambda request: httpx.Response(status_code))

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is expected
    await connector.aclose()


async def test_redirect_is_not_followed() -> None:
    """A 302 to another origin would escape the allowlist."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    connector = _connector(handler)

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is ConnectorStatus.UPSTREAM_ERROR
    assert len(seen) == 1, "the client must not follow the redirect"
    assert "evil.example" not in " ".join(seen)
    await connector.aclose()


async def test_non_json_body_is_rejected() -> None:
    connector = _connector(lambda request: httpx.Response(200, text="<html>hi</html>"))

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    await connector.aclose()


async def test_json_array_is_rejected() -> None:
    connector = _connector(lambda request: httpx.Response(200, content=b"[1,2,3]"))

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    await connector.aclose()


async def test_oversized_response_is_cut_off() -> None:
    body = json.dumps({"blob": "x" * 5_000}).encode()
    connector = _connector(
        lambda request: httpx.Response(200, content=body), max_response_bytes=100
    )

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is ConnectorStatus.INVALID_RESPONSE
    assert "large" in (result.message or "")
    await connector.aclose()


async def test_read_timeout_becomes_a_timeout_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    connector = _connector(handler)

    result = await connector.fetch(FetchSpec(operation="orders"), ALICE)

    assert result.status is ConnectorStatus.TIMEOUT
    await connector.aclose()


def test_plain_http_is_only_allowed_for_localhost() -> None:
    with pytest.raises(ValueError, match="localhost"):
        RestApiConnector(base_url="http://api.example.test", operations={"a": "/a"})


def test_operation_paths_must_be_fixed_paths() -> None:
    with pytest.raises(ValueError, match="fixed absolute path"):
        RestApiConnector(
            base_url="https://api.example.test", operations={"a": "https://evil.example/a"}
        )
    with pytest.raises(ValueError, match="fixed absolute path"):
        RestApiConnector(base_url="https://api.example.test", operations={"a": "/../etc/passwd"})


def test_at_least_one_operation_is_required() -> None:
    with pytest.raises(ValueError, match="at least one operation"):
        RestApiConnector(base_url="https://api.example.test", operations={})
