"""Request-path resilience: per-backend circuit breaker.

State machine per backend::

    closed ──(N consecutive failures)──▶ open
    open   ──(open_seconds elapsed)───▶ half-open (admits exactly one probe)
    half-open ──(probe success)──────▶ closed (counters reset)
    half-open ──(probe failure)──────▶ open (timer restarts)

While open, :func:`forward_non_stream` / :func:`forward_stream` raise
:class:`CircuitOpenError` and the request never touches the backend (the API
maps it to 503). Thread-safe like the rest of backend state.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from meridian.config.models import CircuitBreakerConfig


class CircuitOpenError(Exception):
    """Raised when a request is admitted while the backend's circuit is open."""

    def __init__(self, backend: str) -> None:
        super().__init__(f"Circuit open for backend {backend!r}")
        self.backend = backend


class CircuitBreaker:
    """Per-backend circuit breaker (closed → open → half-open)."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        config: CircuitBreakerConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = config.failure_threshold
        self.open_seconds = config.open_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self.state = self.CLOSED
        self.consecutive_failures = 0
        self._opened_at: float = 0.0
        self._probe_in_flight = False

    def allow_request(self) -> bool:
        """Admit a request? Performs the open → half-open transition eagerly."""
        with self._lock:
            if self.state == self.CLOSED:
                return True
            if self.state == self.OPEN:
                if self._clock() - self._opened_at >= self.open_seconds:
                    self.state = self.HALF_OPEN
                    self._probe_in_flight = True
                    return True  # this request IS the probe
                return False
            # half-open: only the probe may run
            return False

    def record_success(self) -> None:
        with self._lock:
            self.state = self.CLOSED
            self.consecutive_failures = 0
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self.consecutive_failures += 1
            if self.state == self.HALF_OPEN:
                self.state = self.OPEN
                self._opened_at = self._clock()
                self._probe_in_flight = False
            elif (
                self.state == self.CLOSED
                and self.consecutive_failures >= self.failure_threshold
            ):
                self.state = self.OPEN
                self._opened_at = self._clock()

    def reset(self) -> None:
        """Reset failure counters without changing state.

        Used when waking an idle backend — failures accumulated while
        the backend was scaled to zero are not meaningful for the new
        instance.
        """
        with self._lock:
            self.consecutive_failures = 0

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "consecutive_failures": self.consecutive_failures,
            }
