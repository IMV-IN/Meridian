"""Backend runtime state and registry."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, List, Optional, Set

from meridian.config.models import BackendConfig, TimeoutConfig

if TYPE_CHECKING:
    from meridian.resilience import CircuitBreaker
    from meridian.telemetry.base import BackendTelemetry


class Backend:
    """Runtime state for a single backend."""

    def __init__(
        self,
        config: BackendConfig,
        *,
        timeouts: Optional[TimeoutConfig] = None,
        circuit: "Optional[CircuitBreaker]" = None,
    ) -> None:
        self.name = config.name
        self.url = config.url.rstrip("/")
        self.engine = config.engine
        self.model = config.model
        self.weight = config.weight
        self.tags: Set[str] = set(config.tags)
        self.health_endpoint = config.health_endpoint
        # Upstream credential only — never a Meridian client API key.
        self.auth_header: Optional[str] = config.auth_header
        # Effective upstream timeouts (global merged with per-backend override).
        self.timeouts = timeouts or TimeoutConfig()
        # Optional circuit breaker (None = resilience.circuit_breaker disabled).
        self.circuit = circuit
        # Scale-to-zero: None = never idle. ms values; activity stamped on admit.
        self.idle_timeout_ms: Optional[float] = (
            config.idle_timeout_min * 60_000.0
            if config.idle_timeout_min is not None
            else None
        )
        self.idle: bool = False
        self.last_activity_ms: float = 0.0

        # Runtime state (thread-safe via lock)
        self._lock = threading.Lock()
        self.healthy: bool = True
        self.inflight: int = 0
        self.inflight_cost: float = 0.0
        self.ewma_latency_ms: float = 0.0
        self._ewma_alpha: float = 0.3

        # Health check counters
        self.consecutive_failures: int = 0
        self.consecutive_successes: int = 0

        # Telemetry signals (from a TelemetryAdapter, optional). All None means
        # "no signal" — the router treats that as no penalty.
        self.queue_depth: Optional[int] = None
        self.tokens_per_sec: Optional[float] = None
        self.gpu_mem_util: Optional[float] = None

    def increment_inflight(self) -> None:
        with self._lock:
            self.inflight += 1

    def decrement_inflight(self) -> None:
        with self._lock:
            self.inflight = max(0, self.inflight - 1)

    def touch(self, now_ms: float) -> None:
        """Record request traffic (drives scale-to-zero idle detection)."""
        with self._lock:
            self.last_activity_ms = now_ms

    def mark_idle_if_expired(self, now_ms: float) -> bool:
        """Mark idle when the configured quiet period elapsed. True if newly idle.

        A backend that has never received traffic (``touch()`` never called) is
        never considered idle — ``last_activity_ms == 0`` acts as a sentinel for
        "not yet tracked".
        """
        with self._lock:
            if self.idle or self.idle_timeout_ms is None or self.last_activity_ms == 0.0:
                return False
            if now_ms - self.last_activity_ms < self.idle_timeout_ms:
                return False
            self.idle = True
            return True

    def wake(self, now_ms: float) -> None:
        """Return an idle backend to active service (scale-to-zero wake-up).

        Also resets health-tracked failure counters so the backend gets a
        clean slate instead of inheriting failures accumulated while it
        was scaled to zero.
        """
        with self._lock:
            self.idle = False
            self.last_activity_ms = now_ms
            self.consecutive_failures = 0
            self.consecutive_successes = 0
            if self.circuit is not None:
                self.circuit.reset()

    def add_inflight_cost(self, cost: float) -> None:
        with self._lock:
            self.inflight_cost += cost

    def subtract_inflight_cost(self, cost: float) -> None:
        with self._lock:
            self.inflight_cost = max(0.0, self.inflight_cost - cost)

    def update_latency(self, latency_ms: float) -> None:
        with self._lock:
            if self.ewma_latency_ms == 0.0:
                self.ewma_latency_ms = latency_ms
            else:
                self.ewma_latency_ms = (
                    self._ewma_alpha * latency_ms + (1 - self._ewma_alpha) * self.ewma_latency_ms
                )

    def record_health_success(self, success_threshold: int) -> None:
        with self._lock:
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            if self.consecutive_successes >= success_threshold:
                self.healthy = True

    def record_health_failure(self, fail_threshold: int) -> None:
        with self._lock:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            if self.consecutive_failures >= fail_threshold:
                self.healthy = False

    def set_telemetry(self, telemetry: BackendTelemetry) -> None:
        """Push fresh telemetry. Does not touch health state."""
        with self._lock:
            self.queue_depth = telemetry.queue_depth
            self.tokens_per_sec = telemetry.tokens_per_sec
            self.gpu_mem_util = telemetry.gpu_mem_util

    def clear_telemetry(self) -> None:
        """Drop telemetry signals (e.g. after a fetch failure). Health unchanged."""
        with self._lock:
            self.queue_depth = None
            self.tokens_per_sec = None
            self.gpu_mem_util = None

    def to_status_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "engine": self.engine,
            "model": self.model,
            "weight": self.weight,
            "tags": sorted(self.tags),
            "healthy": self.healthy,
            "idle": self.idle,
            "inflight": self.inflight,
            "inflight_cost": round(self.inflight_cost, 2),
            "ewma_latency_ms": round(self.ewma_latency_ms, 2),
            "queue_depth": self.queue_depth,
            "tokens_per_sec": self.tokens_per_sec,
            "gpu_mem_util": self.gpu_mem_util,
            "circuit": self.circuit.status() if self.circuit is not None else None,
        }


class BackendRegistry:
    """Registry of all backends with eligibility filtering."""

    def __init__(self, backends: Optional[List[Backend]] = None) -> None:
        self.backends: List[Backend] = backends or []
        self._by_name: dict[str, Backend] = {b.name: b for b in self.backends}

    def get(self, name: str) -> Optional[Backend]:
        return self._by_name.get(name)

    def eligible(self, model: str, tags: Optional[Set[str]] = None) -> List[Backend]:
        """Return active (healthy, non-idle) backends matching model and tags."""
        result = []
        for b in self.backends:
            if not b.healthy or b.idle:
                continue
            if b.model and b.model != model:
                continue
            if tags and not tags.issubset(b.tags):
                continue
            result.append(b)
        return result

    def idle_eligible(self, model: str, tags: Optional[Set[str]] = None) -> List[Backend]:
        """Return idle (but healthy) backends matching model and tags — wake pool."""
        result = []
        for b in self.backends:
            if not b.idle or not b.healthy:
                continue
            if b.model and b.model != model:
                continue
            if tags and not tags.issubset(b.tags):
                continue
            result.append(b)
        return result

    def all_backends(self) -> List[Backend]:
        return list(self.backends)
