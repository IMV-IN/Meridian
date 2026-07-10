"""Behavior suite for UsageMeter implementations.

Parametrized over InMemoryUsageMeter and SqliteUsageMeter so both must
satisfy the same contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from meridian.usage import InMemoryUsageMeter, MeterKey, SqliteUsageMeter, UsageMeter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def daily_key(scope_id: str, cap: float, now: datetime) -> MeterKey:
    from meridian.usage.bucket import period_bucket
    return MeterKey(
        scope_level="user",
        scope_id=scope_id,
        period="daily",
        period_bucket=period_bucket("daily", now),
        metric="tokens",
        cap=cap,
    )


def monthly_key(scope_id: str, cap: float, now: datetime) -> MeterKey:
    from meridian.usage.bucket import period_bucket
    return MeterKey(
        scope_level="org",
        scope_id=scope_id,
        period="monthly",
        period_bucket=period_bucket("monthly", now),
        metric="tokens",
        cap=cap,
    )


def req_key(scope_id: str, cap: float, now: datetime) -> MeterKey:
    from meridian.usage.bucket import period_bucket
    return MeterKey(
        scope_level="team",
        scope_id=scope_id,
        period="daily",
        period_bucket=period_bucket("daily", now),
        metric="requests",
        cap=cap,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(params=["memory", "sqlite"])
def meter(request, tmp_path: Path) -> UsageMeter:
    if request.param == "memory":
        return InMemoryUsageMeter()
    else:
        return SqliteUsageMeter(str(tmp_path / "usage.db"))


NOW = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime(2024, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
NEXT_MONTH = datetime(2024, 7, 1, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Behavior tests
# ---------------------------------------------------------------------------

class TestAllowUnderCap:
    def test_single_key_allow(self, meter: UsageMeter) -> None:
        key = daily_key("alice", cap=100.0, now=NOW)
        d = meter.check_and_increment([key], cost=50.0, now=NOW)
        assert d.allowed is True
        assert d.blocked_key is None

    def test_usage_reflects_increment(self, meter: UsageMeter) -> None:
        key = daily_key("bob", cap=100.0, now=NOW)
        meter.check_and_increment([key], cost=30.0, now=NOW)
        u = meter.usage(key)
        assert u.consumed == pytest.approx(30.0)
        assert u.cap == pytest.approx(100.0)

    def test_multiple_increments_accumulate(self, meter: UsageMeter) -> None:
        key = daily_key("carol", cap=100.0, now=NOW)
        meter.check_and_increment([key], cost=20.0, now=NOW)
        meter.check_and_increment([key], cost=20.0, now=NOW)
        assert meter.usage(key).consumed == pytest.approx(40.0)


class TestDenyAtOrOverCap:
    def test_deny_exactly_at_cap(self, meter: UsageMeter) -> None:
        key = daily_key("dave", cap=50.0, now=NOW)
        meter.check_and_increment([key], cost=50.0, now=NOW)  # fills it
        d = meter.check_and_increment([key], cost=1.0, now=NOW)
        assert d.allowed is False

    def test_deny_over_cap(self, meter: UsageMeter) -> None:
        key = daily_key("eve", cap=50.0, now=NOW)
        d = meter.check_and_increment([key], cost=60.0, now=NOW)
        assert d.allowed is False

    def test_deny_reports_blocked_key(self, meter: UsageMeter) -> None:
        key = daily_key("frank", cap=10.0, now=NOW)
        d = meter.check_and_increment([key], cost=20.0, now=NOW)
        assert d.blocked_key is not None
        assert d.blocked_key.scope_id == "frank"

    def test_deny_does_not_increment(self, meter: UsageMeter) -> None:
        key = daily_key("grace", cap=10.0, now=NOW)
        meter.check_and_increment([key], cost=20.0, now=NOW)
        assert meter.usage(key).consumed == pytest.approx(0.0)


class TestAtomicity:
    """One over-cap key must block the whole request; no partial increment."""

    def test_all_or_nothing_on_deny(self, meter: UsageMeter) -> None:
        k1 = daily_key("h-k1", cap=100.0, now=NOW)
        k2 = daily_key("h-k2", cap=5.0, now=NOW)  # will exceed cap
        d = meter.check_and_increment([k1, k2], cost=10.0, now=NOW)
        assert d.allowed is False
        # k1 must NOT have been incremented
        assert meter.usage(k1).consumed == pytest.approx(0.0)
        assert meter.usage(k2).consumed == pytest.approx(0.0)

    def test_all_pass_all_increment(self, meter: UsageMeter) -> None:
        k1 = daily_key("i-k1", cap=100.0, now=NOW)
        k2 = daily_key("i-k2", cap=100.0, now=NOW)
        d = meter.check_and_increment([k1, k2], cost=10.0, now=NOW)
        assert d.allowed is True
        assert meter.usage(k1).consumed == pytest.approx(10.0)
        assert meter.usage(k2).consumed == pytest.approx(10.0)


class TestPeriodRollover:
    def test_daily_rollover_resets(self, meter: UsageMeter) -> None:
        key_today = daily_key("iris", cap=50.0, now=NOW)
        meter.check_and_increment([key_today], cost=50.0, now=NOW)
        # Same user, different day bucket
        key_tomorrow = daily_key("iris", cap=50.0, now=NEXT_DAY)
        assert meter.usage(key_tomorrow).consumed == pytest.approx(0.0)
        d = meter.check_and_increment([key_tomorrow], cost=10.0, now=NEXT_DAY)
        assert d.allowed is True

    def test_monthly_rollover_resets(self, meter: UsageMeter) -> None:
        key_june = monthly_key("jake", cap=50.0, now=NOW)
        meter.check_and_increment([key_june], cost=50.0, now=NOW)
        key_july = monthly_key("jake", cap=50.0, now=NEXT_MONTH)
        assert meter.usage(key_july).consumed == pytest.approx(0.0)
        d = meter.check_and_increment([key_july], cost=10.0, now=NEXT_MONTH)
        assert d.allowed is True


class TestRetryAfter:
    def test_retry_after_daily_is_sane(self, meter: UsageMeter) -> None:
        key = daily_key("ken", cap=1.0, now=NOW)
        d = meter.check_and_increment([key], cost=5.0, now=NOW)
        assert d.allowed is False
        assert d.retry_after_s is not None
        # NOW is 10:00 UTC on 2024-06-15; next midnight is 14 hours away
        assert 0 < d.retry_after_s <= 24 * 3600

    def test_retry_after_monthly_is_sane(self, meter: UsageMeter) -> None:
        # Use a date close to end of month for tighter bound
        eom = datetime(2024, 6, 30, 23, 0, 0, tzinfo=timezone.utc)
        key = monthly_key("lena", cap=1.0, now=eom)
        d = meter.check_and_increment([key], cost=5.0, now=eom)
        assert d.allowed is False
        assert d.retry_after_s is not None
        assert 0 < d.retry_after_s <= 32 * 24 * 3600  # at most ~1 month

    def test_retry_after_daily_approximately_correct(self, meter: UsageMeter) -> None:
        # NOW = 2024-06-15 10:00 UTC; next midnight = 14h = 50400s
        key = daily_key("mike", cap=1.0, now=NOW)
        d = meter.check_and_increment([key], cost=5.0, now=NOW)
        assert d.retry_after_s is not None
        expected = 14 * 3600  # 14 hours
        assert abs(d.retry_after_s - expected) < 5  # within 5 seconds

    def test_no_retry_after_on_allow(self, meter: UsageMeter) -> None:
        key = daily_key("nina", cap=100.0, now=NOW)
        d = meter.check_and_increment([key], cost=1.0, now=NOW)
        assert d.allowed is True
        assert d.retry_after_s is None


class TestUsageObservability:
    def test_usage_zero_for_unknown_key(self, meter: UsageMeter) -> None:
        key = daily_key("oscar", cap=100.0, now=NOW)
        u = meter.usage(key)
        assert u.consumed == pytest.approx(0.0)
        assert u.cap == pytest.approx(100.0)

    def test_usage_returns_correct_values(self, meter: UsageMeter) -> None:
        key = daily_key("paul", cap=200.0, now=NOW)
        meter.check_and_increment([key], cost=75.0, now=NOW)
        u = meter.usage(key)
        assert u.consumed == pytest.approx(75.0)
        assert u.cap == pytest.approx(200.0)

    def test_requests_metric(self, meter: UsageMeter) -> None:
        key = req_key("quinn", cap=10.0, now=NOW)
        meter.check_and_increment([key], cost=0.0, requests=3, now=NOW)
        assert meter.usage(key).consumed == pytest.approx(3.0)


class TestRemainingHeadroom:
    def test_min_across_token_keys(self, meter: UsageMeter) -> None:
        a = daily_key("head-a", cap=100.0, now=NOW)
        b = daily_key("head-b", cap=50.0, now=NOW)
        # rename scope so keys don't collide
        b = MeterKey(
            scope_level="org",
            scope_id="head-b",
            period="daily",
            period_bucket=a.period_bucket,
            metric="tokens",
            cap=50.0,
        )
        meter.check_and_increment([a], cost=10.0, now=NOW)
        meter.check_and_increment([b], cost=40.0, now=NOW)
        tok, req = meter.remaining_headroom([a, b])
        assert tok == pytest.approx(10.0)  # b: 50-40
        assert req is None

    def test_request_and_token(self, meter: UsageMeter) -> None:
        tok = daily_key("both-t", cap=100.0, now=NOW)
        req = req_key("both-r", cap=10.0, now=NOW)
        meter.check_and_increment([tok, req], cost=25.0, requests=1, now=NOW)
        t, r = meter.remaining_headroom([tok, req])
        assert t == pytest.approx(75.0)
        assert r == pytest.approx(9.0)


class TestAdjust:
    """Post-hoc token reconcile: charge more / refund / clamp / ignore requests."""

    def test_adjust_positive_charges_more(self, meter: UsageMeter) -> None:
        key = daily_key("adj-pos", cap=1000.0, now=NOW)
        meter.check_and_increment([key], cost=50.0, now=NOW)
        meter.adjust([key], token_delta=20.0)
        assert meter.usage(key).consumed == pytest.approx(70.0)

    def test_adjust_negative_refunds(self, meter: UsageMeter) -> None:
        key = daily_key("adj-neg", cap=1000.0, now=NOW)
        meter.check_and_increment([key], cost=50.0, now=NOW)
        meter.adjust([key], token_delta=-15.0)
        assert meter.usage(key).consumed == pytest.approx(35.0)

    def test_adjust_clamp_floor_zero(self, meter: UsageMeter) -> None:
        key = daily_key("adj-clamp", cap=1000.0, now=NOW)
        meter.check_and_increment([key], cost=10.0, now=NOW)
        meter.adjust([key], token_delta=-999.0)
        assert meter.usage(key).consumed == pytest.approx(0.0)

    def test_adjust_zero_is_noop(self, meter: UsageMeter) -> None:
        key = daily_key("adj-zero", cap=1000.0, now=NOW)
        meter.check_and_increment([key], cost=40.0, now=NOW)
        meter.adjust([key], token_delta=0.0)
        assert meter.usage(key).consumed == pytest.approx(40.0)

    def test_adjust_skips_request_keys(self, meter: UsageMeter) -> None:
        tok = daily_key("adj-tok", cap=1000.0, now=NOW)
        req = req_key("adj-req", cap=100.0, now=NOW)
        meter.check_and_increment([tok, req], cost=10.0, requests=1, now=NOW)
        meter.adjust([tok, req], token_delta=5.0)
        assert meter.usage(tok).consumed == pytest.approx(15.0)
        assert meter.usage(req).consumed == pytest.approx(1.0)

    def test_adjust_ignores_cap(self, meter: UsageMeter) -> None:
        """Request already ran — force charge even if over cap for next checks."""
        key = daily_key("adj-over", cap=50.0, now=NOW)
        meter.check_and_increment([key], cost=40.0, now=NOW)
        meter.adjust([key], token_delta=30.0)  # 70 > cap 50
        assert meter.usage(key).consumed == pytest.approx(70.0)
        d = meter.check_and_increment([key], cost=1.0, now=NOW)
        assert d.allowed is False

    def test_adjust_missing_key_inserts(self, meter: UsageMeter) -> None:
        key = daily_key("adj-new", cap=100.0, now=NOW)
        meter.adjust([key], token_delta=12.0)
        assert meter.usage(key).consumed == pytest.approx(12.0)

    def test_adjust_missing_key_negative_stays_zero(self, meter: UsageMeter) -> None:
        key = daily_key("adj-new-neg", cap=100.0, now=NOW)
        meter.adjust([key], token_delta=-5.0)
        assert meter.usage(key).consumed == pytest.approx(0.0)


class TestSqlitePersistence:
    """SQLite-only: verify that data survives closing and reopening the file."""

    def test_survives_reopen(self, tmp_path: Path) -> None:
        db = str(tmp_path / "persist.db")
        key = daily_key("rosa", cap=100.0, now=NOW)

        m1 = SqliteUsageMeter(db)
        m1.check_and_increment([key], cost=42.0, now=NOW)

        m2 = SqliteUsageMeter(db)
        u = m2.usage(key)
        assert u.consumed == pytest.approx(42.0)

    def test_deny_survives_reopen(self, tmp_path: Path) -> None:
        db = str(tmp_path / "persist2.db")
        key = daily_key("sam", cap=10.0, now=NOW)

        m1 = SqliteUsageMeter(db)
        m1.check_and_increment([key], cost=10.0, now=NOW)

        m2 = SqliteUsageMeter(db)
        d = m2.check_and_increment([key], cost=1.0, now=NOW)
        assert d.allowed is False
