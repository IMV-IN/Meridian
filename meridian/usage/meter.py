"""Abstract base for usage meters."""

from __future__ import annotations

import abc
from datetime import datetime
from typing import List, Optional

from meridian.usage.types import Decision, MeterKey, Usage


class UsageMeter(abc.ABC):
    @abc.abstractmethod
    def check_and_increment(
        self,
        keys: List[MeterKey],
        cost: float,
        requests: int = 1,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Atomically check all keys; increment all or none."""

    @abc.abstractmethod
    def usage(self, key: MeterKey) -> Usage:
        """Current consumption for a single key."""
