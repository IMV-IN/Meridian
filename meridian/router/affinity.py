"""In-memory session -> backend mapping with sliding TTL.

Single responsibility, thread-safe. Used by the API layer to pin a session to
one backend while it stays healthy (KV-affinity lite). Time is injected as a
millisecond clock for testability; production passes ``now_ms``.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Tuple


class SessionStore:
    def __init__(
        self,
        ttl_ms: float,
        max_sessions: int,
        clock: Callable[[], float],
    ) -> None:
        self._ttl_ms = ttl_ms
        self._max = max_sessions
        self._clock = clock
        self._lock = threading.Lock()
        # session_id -> (backend_name, expiry_ms)
        self._map: Dict[str, Tuple[str, float]] = {}

    def get(self, session_id: str) -> Optional[str]:
        """Return the pinned backend name if live; slide its expiry on hit."""
        # Clock is read outside the lock intentionally: `now` is only used to
        # compute/compare an expiry, and TTL granularity dwarfs any scheduling
        # skew between this read and acquiring the lock. The expiry comparison
        # itself happens under the lock, so there is no check-then-act race.
        now = self._clock()
        with self._lock:
            entry = self._map.get(session_id)
            if entry is None:
                return None
            name, expiry = entry
            if now >= expiry:
                del self._map[session_id]
                return None
            self._map[session_id] = (name, now + self._ttl_ms)
            return name

    def put(self, session_id: str, backend_name: str) -> None:
        """Pin or refresh a session mapping; evict nearest-expiry if full."""
        now = self._clock()
        with self._lock:
            if session_id not in self._map and len(self._map) >= self._max:
                victim = min(self._map, key=lambda k: self._map[k][1])
                del self._map[victim]
            self._map[session_id] = (backend_name, now + self._ttl_ms)

    def sweep(self) -> None:
        """Drop all expired entries."""
        now = self._clock()
        with self._lock:
            expired = [k for k, (_, exp) in self._map.items() if now >= exp]
            for k in expired:
                del self._map[k]

    def size(self) -> int:
        """Return the current number of mapped sessions (live or not-yet-swept)."""
        with self._lock:
            return len(self._map)
