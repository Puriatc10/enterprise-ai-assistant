"""Per-subject turn limiting.

The window maths is unit-tested because sliding windows are easy to get subtly wrong, and
the HTTP behaviour is tested because "429" alone is not a contract — the client needs
``Retry-After`` and the same error envelope as every other failure.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.dependencies import get_llm_adapter, get_rate_limiter
from app.domain.models.auth import AuthContext
from app.infrastructure.llm.fake import FakeLLMAdapter, text_response
from app.infrastructure.persistence.memory import InMemoryConversationRepository
from app.main import create_app
from app.security.auth import require_auth_context
from app.security.rate_limit import RateLimitExceeded, TurnRateLimiter

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Window maths
# ---------------------------------------------------------------------------


def test_the_window_slides_rather_than_resetting() -> None:
    clock = Clock()
    limiter = TurnRateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.check("a").allowed
    assert limiter.check("a").allowed
    blocked = limiter.check("a")
    assert not blocked.allowed
    assert blocked.retry_after_seconds == 60

    # One slot frees up after the first hit ages out — not after the whole window.
    clock.advance(61)
    assert limiter.check("a").allowed


def test_subjects_do_not_share_a_window() -> None:
    limiter = TurnRateLimiter(limit=1, window_seconds=60, clock=Clock())

    assert limiter.check("alice").allowed
    assert not limiter.check("alice").allowed
    assert limiter.check("bob").allowed


def test_a_blocked_caller_cannot_extend_their_own_penalty() -> None:
    clock = Clock()
    limiter = TurnRateLimiter(limit=1, window_seconds=60, clock=clock)
    limiter.check("a")

    for _ in range(5):
        assert not limiter.check("a").allowed

    clock.advance(61)
    assert limiter.check("a").allowed, "retries must not push the oldest hit forward"


def test_zero_limit_disables_limiting() -> None:
    limiter = TurnRateLimiter(limit=0)

    assert all(limiter.check("a").allowed for _ in range(50))


def test_the_remaining_count_is_reported() -> None:
    limiter = TurnRateLimiter(limit=3, window_seconds=60, clock=Clock())

    assert limiter.check("a").remaining == 2
    assert limiter.check("a").remaining == 1


def test_negative_limit_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="limit"):
        TurnRateLimiter(limit=-1)


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------


@pytest.fixture
def limited_client() -> Iterator[TestClient]:
    application = create_app(Settings(_env_file=None))
    application.state.conversation_repository = InMemoryConversationRepository()
    limiter = TurnRateLimiter(limit=2, window_seconds=3600, clock=Clock())

    application.dependency_overrides[require_auth_context] = lambda: ALICE
    application.dependency_overrides[get_llm_adapter] = lambda: FakeLLMAdapter(
        [text_response("پاسخ")]
    )
    application.dependency_overrides[get_rate_limiter] = lambda: limiter

    with TestClient(application) as client:
        yield client


def test_the_third_turn_in_a_window_is_a_429_with_retry_after(limited_client: TestClient) -> None:
    conversation_id = limited_client.post("/conversations", json={}).json()["id"]
    url = f"/conversations/{conversation_id}/messages"

    first = limited_client.post(url, json={"content": "یک"})
    second = limited_client.post(url, json={"content": "دو"})
    third = limited_client.post(url, json={"content": "سه"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429

    body = third.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["request_id"]
    assert int(third.headers["retry-after"]) > 0


def test_streaming_is_limited_before_any_model_work(limited_client: TestClient) -> None:
    """A limited caller must not cost a provider call, streamed or not."""
    conversation_id = limited_client.post("/conversations", json={}).json()["id"]
    url = f"/conversations/{conversation_id}/messages/stream"

    for _ in range(2):
        with limited_client.stream("POST", url, json={"content": "سلام"}) as response:
            assert response.status_code == 200
            list(response.iter_lines())

    refused = limited_client.post(url, json={"content": "سلام"})

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"


def test_rate_limit_exceeded_carries_a_positive_wait() -> None:
    assert RateLimitExceeded(0).retry_after_seconds == 1
    assert RateLimitExceeded(42).retry_after_seconds == 42
