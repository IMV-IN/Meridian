"""Thread-safe prefix predictions with sliding TTL and feedback correction."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class Prediction:
    key: str
    backend: str
    trust: float


@dataclass
class Entry:
    backend: str
    expires_ms: float
    hits: int = 0
    misses: int = 0

    @property
    def trust(self) -> float:
        return (self.hits + 1) / (self.hits + self.misses + 2)


class PrefixIndex:
    def __init__(
        self, *, ttl_ms: float, max_entries: int, clock: Callable[[], float],
        hit_threshold: float = 0.5, min_trust: float = 0.15, feedback_correction: bool = True,
    ) -> None:
        if ttl_ms <= 0 or max_entries < 1:
            raise ValueError("ttl_ms and max_entries must be positive")
        self._ttl_ms = ttl_ms
        self._max = max_entries
        self._clock = clock
        self.hit_threshold = hit_threshold
        self.min_trust = min_trust
        self.feedback_correction = feedback_correction
        self._entries: OrderedDict[str, Entry] = OrderedDict()
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        # A monotonic clock and fixed TTL keep nearest-expiry entries first.
        while self._entries:
            key = next(iter(self._entries))
            if self._entries[key].expires_ms > now:
                break
            self._entries.popitem(last=False)

    def lookup(self, keys: Sequence[str]) -> Optional[Prediction]:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            for key in keys:
                entry = self._entries.get(key)
                if entry is not None:
                    entry.expires_ms = now + self._ttl_ms
                    self._entries.move_to_end(key)
                    return Prediction(key, entry.backend, entry.trust)
        return None

    def record(self, keys: Sequence[str], backend: str, hit_ratio: float) -> None:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            for key in reversed(keys):
                entry = self._entries.get(key)
                if entry is None or entry.backend != backend:
                    # A cold request warms the engine. Its initial zero cache
                    # hit is not evidence against a prediction we never made.
                    entry = Entry(backend, now + self._ttl_ms)
                elif self.feedback_correction:
                    if hit_ratio >= self.hit_threshold:
                        entry.hits += 1
                    else:
                        entry.misses += 1
                    if entry.trust < self.min_trust:
                        del self._entries[key]
                        continue
                entry.expires_ms = now + self._ttl_ms
                self._entries[key] = entry
                self._entries.move_to_end(key)
                if len(self._entries) > self._max:
                    self._entries.popitem(last=False)

    def sweep(self) -> None:
        with self._lock:
            self._sweep(self._clock())

    def size(self) -> int:
        with self._lock:
            self._sweep(self._clock())
            return len(self._entries)
