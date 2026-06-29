"""Tests for the in-memory session affinity store (sliding TTL)."""

from meridian.router.affinity import SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0  # ms

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms


def test_put_then_get_returns_backend():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=10, clock=clk)
    store.put("s1", "backend-a")
    assert store.get("s1") == "backend-a"


def test_get_unknown_returns_none():
    store = SessionStore(ttl_ms=1000, max_sessions=10, clock=FakeClock())
    assert store.get("nope") is None


def test_entry_expires_after_ttl():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=10, clock=clk)
    store.put("s1", "backend-a")
    clk.advance(1001)
    assert store.get("s1") is None


def test_get_slides_expiry():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=10, clock=clk)
    store.put("s1", "backend-a")
    clk.advance(800)
    assert store.get("s1") == "backend-a"  # refreshes expiry to t=1800+1000
    clk.advance(800)
    assert store.get("s1") == "backend-a"  # would have expired without the slide


def test_sweep_evicts_expired():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=10, clock=clk)
    store.put("s1", "backend-a")
    clk.advance(1001)
    store.sweep()
    assert store.size() == 0


def test_max_sessions_evicts_nearest_expiry():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=2, clock=clk)
    store.put("s1", "a")          # expires at 2000
    clk.advance(10)
    store.put("s2", "b")          # expires at 2010
    clk.advance(10)
    store.put("s3", "c")          # full -> evict s1 (nearest expiry)
    assert store.get("s1") is None
    assert store.get("s2") == "b"
    assert store.get("s3") == "c"
    assert store.size() == 2
