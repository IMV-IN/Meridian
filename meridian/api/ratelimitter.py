"""Token-bucket rate limiter and bounded per-key store."""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Tuple


class TokenBucket:
    """Token bucket: starts full at ``max_tokens``, refills at ``refill_rate``/s."""

    def __init__(self, max_tokens: float, refill_rate: float) -> None:
        assert max_tokens > 0, "max_tokens must be positive"
        assert refill_rate > 0, "refill_rate must be positive"

        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.tokens = max_tokens
        self.refilled_rate = time.time()
        self.lock = threading.Lock()

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self.refilled_rate
        if elapsed > 0:
            self.tokens = min(
                self.max_tokens,
                self.tokens + elapsed * self.refill_rate,
            )
            self.refilled_rate = now

    def allow_request(self, tokens: float = 1) -> bool:
        with self.lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def get_remaining(self) -> float:
        with self.lock:
            self._refill()
            return self.tokens

    def get_reset_time(self) -> float:
        with self.lock:
            self._refill()
            return self.refilled_rate


class RateLimitStore:
    """Bounded map of rate-limit keys → TokenBucket with idle TTL eviction.

    Mirrors SessionStore: sliding idle expiry on access, sweep drops expired
    entries, and when at ``max_keys`` the nearest-expiry entry is evicted.
    """

    def __init__(
        self,
        max_keys: int = 100_000,
        idle_ttl_s: float = 3600.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        if idle_ttl_s <= 0:
            raise ValueError("idle_ttl_s must be > 0")
        self._max = max_keys
        self._ttl = idle_ttl_s
        self._clock = clock or time.time
        self._lock = threading.Lock()
        # key -> (bucket, expiry_epoch_s)
        self._map: Dict[str, Tuple[TokenBucket, float]] = {}

    def get_or_create(
        self,
        key: str,
        max_tokens: float,
        refill_rate: float,
    ) -> TokenBucket:
        """Return the bucket for *key*, creating it if missing; slide idle TTL."""
        now = self._clock()
        with self._lock:
            entry = self._map.get(key)
            if entry is not None:
                bucket, expiry = entry
                if now < expiry:
                    self._map[key] = (bucket, now + self._ttl)
                    return bucket
                # Expired — drop and recreate below
                del self._map[key]

            if len(self._map) >= self._max:
                victim = min(self._map, key=lambda k: self._map[k][1])
                del self._map[victim]

            bucket = TokenBucket(max_tokens=max_tokens, refill_rate=refill_rate)
            self._map[key] = (bucket, now + self._ttl)
            return bucket

    def sweep(self) -> int:
        """Drop expired entries. Returns number removed."""
        now = self._clock()
        with self._lock:
            expired = [k for k, (_, exp) in self._map.items() if now >= exp]
            for k in expired:
                del self._map[k]
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._map.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._map)

    def __contains__(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            entry = self._map.get(key)
            if entry is None:
                return False
            return now < entry[1]
