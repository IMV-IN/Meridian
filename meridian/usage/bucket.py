"""Period bucket helpers — UTC, no external deps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def period_bucket(period: str, now: datetime) -> str:
    """Return the bucket string for *now* in UTC.

    daily   -> "YYYY-MM-DD"
    monthly -> "YYYY-MM"
    """
    utc = now.astimezone(timezone.utc)
    if period == "daily":
        return utc.strftime("%Y-%m-%d")
    if period == "monthly":
        return utc.strftime("%Y-%m")
    raise ValueError(f"Unknown period: {period!r}")


def retry_after_s(period: str, now: datetime) -> float:
    """Seconds from *now* (UTC) until the next period rollover."""
    utc = now.astimezone(timezone.utc)
    if period == "daily":
        next_midnight = (utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return (next_midnight - utc).total_seconds()
    if period == "monthly":
        # First day of next month at 00:00 UTC
        if utc.month == 12:
            first_next = utc.replace(year=utc.year + 1, month=1, day=1,
                                     hour=0, minute=0, second=0, microsecond=0)
        else:
            first_next = utc.replace(month=utc.month + 1, day=1,
                                     hour=0, minute=0, second=0, microsecond=0)
        return (first_next - utc).total_seconds()
    raise ValueError(f"Unknown period: {period!r}")
