"""Rate limit for the endpoints that spend money (the LLM calls)."""

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """At most `limit` hits in any `window_s` seconds.

    Kept in memory, so the budget is per process: right for the single-server deploy, where one
    uvicorn worker serves everything. With several workers or replicas each has its own budget,
    and the counter has to move to shared storage (Postgres or Redis).

    `hit` has no awaits, so on one event loop two requests can never interleave inside it.
    """

    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._hits: deque[float] = deque()

    def hit(self) -> float | None:
        """Count one request. None if it is allowed, else seconds until a slot frees up (a
        refused request is not counted, so hammering a closed limiter does not extend it)."""
        now = self._clock()
        while self._hits and self._hits[0] <= now - self._window_s:
            self._hits.popleft()
        if len(self._hits) >= self._limit:
            return self._hits[0] + self._window_s - now
        self._hits.append(now)
        return None
