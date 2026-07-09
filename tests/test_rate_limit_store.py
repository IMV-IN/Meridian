"""Unit tests for RateLimitStore (Milestone K — bounded rate-limit map)."""

from __future__ import annotations

from meridian.api.ratelimitter import RateLimitStore


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_get_or_create_reuses_bucket():
    clock = FakeClock(0.0)
    store = RateLimitStore(max_keys=10, idle_ttl_s=100.0, clock=clock)
    a = store.get_or_create("ip:1", max_tokens=5, refill_rate=1)
    b = store.get_or_create("ip:1", max_tokens=5, refill_rate=1)
    assert a is b
    assert store.size() == 1


def test_idle_ttl_expires_bucket():
    clock = FakeClock(0.0)
    store = RateLimitStore(max_keys=10, idle_ttl_s=10.0, clock=clock)
    first = store.get_or_create("ip:1", max_tokens=5, refill_rate=1)
    first.allow_request()  # spend a token so we can tell recreate apart
    clock.t = 11.0
    second = store.get_or_create("ip:1", max_tokens=5, refill_rate=1)
    assert second is not first
    assert second.get_remaining() == 5.0  # fresh bucket


def test_sweep_removes_expired():
    clock = FakeClock(0.0)
    store = RateLimitStore(max_keys=10, idle_ttl_s=5.0, clock=clock)
    store.get_or_create("a", 1, 1)
    store.get_or_create("b", 1, 1)
    clock.t = 6.0
    removed = store.sweep()
    assert removed == 2
    assert store.size() == 0


def test_max_keys_evicts_nearest_expiry():
    clock = FakeClock(0.0)
    store = RateLimitStore(max_keys=2, idle_ttl_s=100.0, clock=clock)
    store.get_or_create("a", 1, 1)
    clock.t = 1.0
    store.get_or_create("b", 1, 1)
    clock.t = 2.0
    # "a" expires earliest (created at t=0 → expiry 100); insert "c" evicts a
    store.get_or_create("c", 1, 1)
    assert store.size() == 2
    assert "a" not in store
    assert "b" in store
    assert "c" in store


def test_access_slides_ttl():
    clock = FakeClock(0.0)
    store = RateLimitStore(max_keys=10, idle_ttl_s=10.0, clock=clock)
    store.get_or_create("ip:1", 1, 1)
    clock.t = 9.0
    store.get_or_create("ip:1", 1, 1)  # refresh
    clock.t = 18.0
    # Without slide, would have expired at 10; with slide at t=9 → expiry 19
    assert "ip:1" in store
    clock.t = 19.0
    assert "ip:1" not in store


def test_clear():
    store = RateLimitStore(max_keys=10, idle_ttl_s=10.0)
    store.get_or_create("x", 1, 1)
    store.clear()
    assert store.size() == 0
