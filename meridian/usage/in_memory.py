"""In-memory UsageMeter — dict-backed, thread-safe via Lock (matches registry idiom)."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from meridian.usage.bucket import retry_after_s as _retry_after_s
from meridian.usage.meter import UsageMeter
from meridian.usage.types import Decision, MeterKey, Usage


class InMemoryUsageMeter(UsageMeter):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key: (scope_level, scope_id, period_bucket, metric) -> consumed
        self._store: Dict[tuple, float] = defaultdict(float)

    def _store_key(self, key: MeterKey) -> tuple:
        return (key.scope_level, key.scope_id, key.period_bucket, key.metric)

    def check_and_increment(
        self,
        keys: List[MeterKey],
        cost: float,
        requests: int = 1,
        now: Optional[datetime] = None,
    ) -> Decision:
        if now is None:
            now = datetime.now(timezone.utc)
        with self._lock:
            # Check phase — find first violation
            for key in keys:
                amount = cost if key.metric == "tokens" else float(requests)
                consumed = self._store[self._store_key(key)]
                if consumed + amount > key.cap:
                    return Decision(
                        allowed=False,
                        blocked_key=key,
                        retry_after_s=_retry_after_s(key.period, now),
                    )
            # Increment phase — all passed
            for key in keys:
                amount = cost if key.metric == "tokens" else float(requests)
                self._store[self._store_key(key)] += amount
            return Decision(allowed=True)

    def usage(self, key: MeterKey) -> Usage:
        with self._lock:
            consumed = self._store[self._store_key(key)]
        return Usage(consumed=consumed, cap=key.cap)
