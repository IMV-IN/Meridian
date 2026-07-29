"""Canary rollout controller (Phase 3, Track 2).

Walks a weight-shift schedule between the stable and canary backend tag
pools. Promotion is time-based (step durations); demotion is error-based
(auto-rollback to weight 0 on a rolling-window error-rate breach, guarded
by a minimum sample count so first requests can't flap it).

Thread-safe state; the clock is injectable for tests. The background loop
follows the HealthChecker start/stop pattern.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional, Tuple

from meridian.config.models import CanaryConfig
from meridian.metrics.collectors import CANARY_ROLLBACKS, CANARY_WEIGHT
from meridian.registry.backend import Backend

logger = logging.getLogger("meridian.canary")


class CanaryController:
    """Owns canary split state: current weight, schedule cursor, error window."""

    def __init__(
        self,
        config: CanaryConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = config
        self._clock = clock
        self._lock = threading.Lock()
        self._weight = config.start_weight
        # -1 → pre-schedule at start_weight; first tick enters step 0.
        self._step_idx = -1
        self._step_started = clock()
        self._done = not config.steps  # empty schedule ⇒ done immediately
        self._rolled_back = False
        # Rolling window of (monotonic_ts, ok) for canary-pool requests.
        self._window: Deque[Tuple[float, bool]] = deque()
        self._task: Optional[asyncio.Task[None]] = None
        # Publish the initial split immediately — tick() only re-sets it later.
        CANARY_WEIGHT.set(self._weight)

    # ── request path ────────────────────────────────────────────────────

    @property
    def weight(self) -> float:
        with self._lock:
            return self._weight

    @property
    def rolled_back(self) -> bool:
        with self._lock:
            return self._rolled_back

    def pick_pool(self, roll: float) -> str:
        """Per-request pool choice. ``roll`` in [0, 100) — random in prod."""
        return "canary" if roll < self.weight else "stable"

    def record_backend(self, backend: Backend, status_code: int) -> None:
        """Feed an outcome into the rollback window (canary pool only)."""
        if not set(self.cfg.canary_tags).issubset(backend.tags):
            return
        with self._lock:
            self._window.append((self._clock(), status_code < 500))

    # ── controller tick ─────────────────────────────────────────────────

    def _prune(self, now: float) -> None:
        cutoff = now - self.cfg.window_s
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def tick(self) -> None:
        """One controller cycle: prune → maybe rollback → maybe advance."""
        with self._lock:
            now = self._clock()
            self._prune(now)

            if not self._rolled_back:
                n = len(self._window)
                if n >= self.cfg.rollback_min_samples:
                    errs = sum(1 for _ts, ok in self._window if not ok)
                    rate = errs / n
                    if rate > self.cfg.rollback_error_rate:
                        self._rolled_back = True
                        self._weight = 0.0
                        CANARY_ROLLBACKS.inc()
                        logger.error(
                            "Canary auto-rollback: error rate %.2f over %d samples "
                            "in the last %.0fs — weight forced to 0",
                            rate, n, self.cfg.window_s,
                        )

            if not self._rolled_back and not self._done and self.cfg.steps:
                if self._step_idx < 0:
                    # Enter the first step on the first tick.
                    self._step_idx = 0
                    self._step_started = now
                    self._weight = self.cfg.steps[0].weight
                    if self.cfg.steps[0].duration_s is None:
                        self._done = True
                    logger.info("Canary step 0 → weight %.0f%%", self._weight)
                else:
                    step = self.cfg.steps[self._step_idx]
                    if step.duration_s is not None and (
                        now - self._step_started >= step.duration_s
                    ):
                        self._step_idx += 1
                        if self._step_idx >= len(self.cfg.steps):
                            self._done = True
                            logger.info(
                                "Canary schedule complete — holding weight %.0f%%",
                                self._weight,
                            )
                        else:
                            nxt = self.cfg.steps[self._step_idx]
                            self._step_started = now
                            self._weight = nxt.weight
                            if nxt.duration_s is None:
                                self._done = True
                            logger.info(
                                "Canary step %d → weight %.0f%%",
                                self._step_idx, self._weight,
                            )

            CANARY_WEIGHT.set(self._weight)

    # ── lifecycle ───────────────────────────────────────────────────────

    def merge_from(self, old: "CanaryController") -> None:
        """Carry rollback state over from a config-reload predecessor.

        Any edit to the canary section builds a fresh controller; without this,
        touching ``tick_s`` right after an auto-rollback would silently re-arm
        traffic onto the pool that just started failing. Only merge when the
        pool identity (tag sets) is unchanged — new tags describe a different
        rollout, whose clean slate is intentional.
        """
        with old._lock, self._lock:
            if (
                old._rolled_back
                and set(old.cfg.canary_tags) == set(self.cfg.canary_tags)
                and set(old.cfg.stable_tags) == set(self.cfg.stable_tags)
            ):
                self._rolled_back = True
                self._weight = 0.0
                self._window = old._window
                CANARY_WEIGHT.set(self._weight)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Canary controller started (start_weight=%.0f%%, %d step(s), tick=%.1fs)",
            self.cfg.start_weight, len(self.cfg.steps), self.cfg.tick_s,
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # The rollout is no longer routing anywhere — don't leave a stale
        # weight exported (a disabled canary and a rolled-back one at weight
        # 0 must not look identical-inverted).
        CANARY_WEIGHT.set(0.0)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            self.tick()

    def status(self) -> Dict[str, object]:
        """State view for /meridian/status."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            n = len(self._window)
            errs = sum(1 for _ts, ok in self._window if not ok)
            return {
                "enabled": True,
                "weight": self._weight,
                "step": self._step_idx,
                "rolled_back": self._rolled_back,
                "schedule_done": self._done,
                "window_samples": n,
                "window_error_rate": (errs / n) if n else None,
                "canary_tags": list(self.cfg.canary_tags),
                "stable_tags": list(self.cfg.stable_tags),
            }
