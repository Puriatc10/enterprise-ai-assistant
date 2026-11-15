"""Per-subject turn limiting.

One user must not be able to spend the shared free pool for everyone else. The limiter is
in-process on purpose (plan decision D6): the deployment is a single instance, and a
Supabase-backed counter would add a round-trip to every turn to buy a guarantee the
platform does not need yet. When there is a second instance, this is the file to revisit —
a dict pretending to be shared is how limits quietly double.

The window is sliding rather than fixed: a fixed window lets a user spend the full quota
at the end of one window and again at the start of the next, which is the classic way
rate limits look fine and behave badly.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimitExceeded(Exception):
    """Raised when a caller has spent their window.

    Carries the wait because ``Retry-After`` is the only part of a 429 a client can act
    on; without it the UI can only say "later".
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many turns in the current window.")
        self.retry_after_seconds = max(1, retry_after_seconds)


class TurnRateLimiter:
    """Sliding-window limiter keyed by verified subject.

    ``limit=0`` disables limiting, which is what tests and a local run without a provider
    key want.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 0:
            msg = "limit must be zero or positive"
            raise ValueError(msg)
        if window_seconds <= 0:
            msg = "window_seconds must be positive"
            raise ValueError(msg)
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    @property
    def limit(self) -> int:
        return self._limit

    def check(self, subject: str) -> RateLimitDecision:
        """Record a turn for ``subject`` if the window allows it.

        The hit is recorded only when allowed, so a blocked caller cannot extend their own
        penalty by retrying.
        """
        if self._limit == 0 or not subject:
            return RateLimitDecision(allowed=True, remaining=0, retry_after_seconds=0)

        now = self._clock()
        cutoff = now - self._window
        hits = self._hits[subject]
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if not hits:
            # Do not keep an entry per subject forever: a demo has more users than turns.
            self._hits.pop(subject, None)
            hits = self._hits[subject]

        if len(hits) >= self._limit:
            retry_after = math.ceil(hits[0] + self._window - now)
            return RateLimitDecision(
                allowed=False, remaining=0, retry_after_seconds=max(1, retry_after)
            )

        hits.append(now)
        return RateLimitDecision(
            allowed=True, remaining=self._limit - len(hits), retry_after_seconds=0
        )

    def reset(self, subject: str | None = None) -> None:
        """Forget history — tests only; production should let windows expire."""
        if subject is None:
            self._hits.clear()
        else:
            self._hits.pop(subject, None)
