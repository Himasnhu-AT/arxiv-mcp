"""A single global rate limiter + single-flight deduplicator for outbound arXiv calls.

arXiv's terms of use ask for no more than one request every three seconds over a
single connection. This module enforces that across *every* tool in the server:
all outbound HTTP goes through :meth:`RateLimiter.acquire`, which serializes calls
and spaces them out. It also honors ``Retry-After`` back-off when a 503 is seen.

:class:`SingleFlight` collapses concurrent identical fetches (e.g. two agents
asking for the same paper) into one in-flight coroutine.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class RateLimiter:
    """Serializes outbound requests to at most one per ``min_interval`` seconds."""

    def __init__(self, min_interval: float = 3.0) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = asyncio.Lock()
        self._last = 0.0
        # When a server tells us to back off, no request runs before this time.
        self._backoff_until = 0.0

    async def acquire(self) -> None:
        """Block until it is polite to issue the next request."""
        async with self._lock:
            now = time.monotonic()
            wait = max(self._last + self.min_interval - now, self._backoff_until - now)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    def note_retry_after(self, seconds: float) -> None:
        """Record a server-requested back-off (e.g. from a 503 ``Retry-After``)."""
        self._backoff_until = max(self._backoff_until, time.monotonic() + max(0.0, seconds))


class SingleFlight:
    """Deduplicates concurrent calls sharing the same key.

    The first caller for a key runs the coroutine; concurrent callers await the
    same result instead of issuing their own request.
    """

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task] = {}

    async def do(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing
        task = asyncio.ensure_future(factory())
        self._inflight[key] = task
        try:
            return await task
        finally:
            # Only clear if we're still the owner (another may have replaced it).
            if self._inflight.get(key) is task:
                del self._inflight[key]
