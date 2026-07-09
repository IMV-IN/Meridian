"""Public types for the usage metering subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MeterKey:
    """Identifies one cap dimension to check/increment."""

    scope_level: str    # "org" | "team" | "user"
    scope_id: str       # e.g. "acme", "acme/eng", "acme/eng/alice"
    period: str         # "daily" | "monthly"
    period_bucket: str  # UTC "YYYY-MM-DD" or "YYYY-MM"
    metric: str         # "tokens" | "requests"
    cap: float          # hard limit for this key


@dataclass
class Decision:
    allowed: bool
    blocked_key: Optional[MeterKey] = None
    retry_after_s: Optional[float] = None


@dataclass
class Usage:
    consumed: float
    cap: float
