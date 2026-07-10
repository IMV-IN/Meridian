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
    def adjust(
        self,
        keys: List[MeterKey],
        token_delta: float,
    ) -> None:
        """Post-hoc token reconciliation (estimate → actual).

        Applies ``token_delta`` to every key with ``metric == "tokens"``.
        Positive deltas charge more; negative refunds. Consumed is clamped
        to ≥ 0. Does **not** re-check caps (request already ran) and does
        **not** touch request counters.
        """

    @abc.abstractmethod
    def usage(self, key: MeterKey) -> Usage:
        """Current consumption for a single key."""
