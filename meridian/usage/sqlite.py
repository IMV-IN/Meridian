"""SQLite-backed UsageMeter — stdlib sqlite3, atomic via BEGIN IMMEDIATE."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from meridian.usage.bucket import retry_after_s as _retry_after_s
from meridian.usage.meter import UsageMeter
from meridian.usage.types import Decision, MeterKey, Usage

_CREATE = """
CREATE TABLE IF NOT EXISTS usage (
    scope_level  TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    period_bucket TEXT NOT NULL,
    metric       TEXT NOT NULL,
    consumed     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (scope_level, scope_id, period_bucket, metric)
)
"""

# ponytail: opportunistic cleanup of old buckets is trivial here — we DELETE
# rows whose bucket is strictly less than the caller's bucket. No cron needed;
# the next write for that scope cleans up the previous period automatically.
_CLEANUP = """
DELETE FROM usage
WHERE scope_level = ? AND scope_id = ? AND metric = ?
  AND period_bucket < ?
"""

_GET = """
SELECT consumed FROM usage
WHERE scope_level = ? AND scope_id = ? AND period_bucket = ? AND metric = ?
"""

_UPSERT = """
INSERT INTO usage (scope_level, scope_id, period_bucket, metric, consumed)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(scope_level, scope_id, period_bucket, metric)
DO UPDATE SET consumed = consumed + excluded.consumed
"""


class SqliteUsageMeter(UsageMeter):
    def __init__(self, path: str) -> None:
        # check_same_thread=False: single-process async gateway, reads/writes
        # all happen on one event-loop thread; False just silences SQLite's
        # thread-origin check. The BEGIN IMMEDIATE transaction provides atomicity.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(_CREATE)
        self._conn.commit()

    def check_and_increment(
        self,
        keys: List[MeterKey],
        cost: float,
        requests: int = 1,
        now: Optional[datetime] = None,
    ) -> Decision:
        if now is None:
            now = datetime.now(timezone.utc)

        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")

            # Check phase
            for key in keys:
                amount = cost if key.metric == "tokens" else float(requests)
                row = cur.execute(
                    _GET,
                    (key.scope_level, key.scope_id, key.period_bucket, key.metric),
                ).fetchone()
                consumed = row[0] if row else 0.0
                if consumed + amount > key.cap:
                    self._conn.rollback()
                    return Decision(
                        allowed=False,
                        blocked_key=key,
                        retry_after_s=_retry_after_s(key.period, now),
                    )

            # Increment phase — all passed
            for key in keys:
                amount = cost if key.metric == "tokens" else float(requests)
                cur.execute(
                    _UPSERT,
                    (key.scope_level, key.scope_id, key.period_bucket, key.metric, amount),
                )
                # Opportunistic cleanup of stale period rows for this scope+metric
                cur.execute(
                    _CLEANUP,
                    (key.scope_level, key.scope_id, key.metric, key.period_bucket),
                )

            self._conn.commit()
            return Decision(allowed=True)
        except Exception:
            self._conn.rollback()
            raise

    def usage(self, key: MeterKey) -> Usage:
        row = self._conn.execute(
            _GET,
            (key.scope_level, key.scope_id, key.period_bucket, key.metric),
        ).fetchone()
        consumed = row[0] if row else 0.0
        return Usage(consumed=consumed, cap=key.cap)
