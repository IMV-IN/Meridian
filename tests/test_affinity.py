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


def test_sweep_preserves_live_entries():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=10, clock=clk)
    store.put("dead", "a")  # expires at 2000
    clk.advance(500)
    store.put("live", "b")  # expires at 2500
    clk.advance(600)        # t=2100: "dead" expired, "live" still alive
    store.sweep()
    assert store.size() == 1
    assert store.get("live") == "b"
    assert store.get("dead") is None


def test_put_refresh_updates_backend_without_growing_or_evicting():
    clk = FakeClock()
    store = SessionStore(ttl_ms=1000, max_sessions=1, clock=clk)
    store.put("s1", "a")
    clk.advance(500)
    store.put("s1", "b")           # refresh same key: no eviction though map is full
    assert store.size() == 1
    assert store.get("s1") == "b"  # backend updated
    clk.advance(900)               # t=1400 < refreshed expiry (1500): still alive
    assert store.get("s1") == "b"  # TTL was reset on refresh


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
