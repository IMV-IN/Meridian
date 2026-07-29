"""Tests for rate limiting — TokenBucket unit tests and API integration."""

from __future__ import annotations

import os
import sys
import tempfile
import time

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meridian.api.ratelimitter import TokenBucket
from mock_backend.server import app as mock_app
from tests._mock_uvicorn import start_mock_server

# ── Find free port for mock backend ──────────────────────────────────────


def _find_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_mock_port = _find_free_port()
_server = start_mock_server(mock_app, "127.0.0.1", _mock_port)
_mock_url = f"http://127.0.0.1:{_mock_port}"

_tmpdir = tempfile.mkdtemp()
_jsonl_path = os.path.join(_tmpdir, "requests.jsonl")

from meridian.api.main import app as meridian_app  # noqa: E402
from meridian.api.main import get_state, init_app  # noqa: E402
from meridian.config.models import MeridianConfig  # noqa: E402

# ── TokenBucket unit tests ──────────────────────────────────────────────


def test_bucket_allows_first_request():
    b = TokenBucket(max_tokens=1, refill_rate=1)
    assert b.allow_request() is True


def test_bucket_blocks_after_exhaustion():
    b = TokenBucket(max_tokens=1, refill_rate=1)
    assert b.allow_request() is True
    assert b.allow_request() is False


def test_bucket_refills_over_time(monkeypatch):
    current_time = 100.0
    monkeypatch.setattr(time, "time", lambda: current_time)

    b = TokenBucket(max_tokens=1, refill_rate=10)
    assert b.allow_request() is True
    assert b.allow_request() is False  # exhausted

    current_time += 0.15
    assert b.allow_request() is True


def test_bucket_multiple_tokens():
    b = TokenBucket(max_tokens=3, refill_rate=1)
    assert b.allow_request(tokens=3) is True
    assert b.allow_request(tokens=1) is False


def test_bucket_get_remaining(monkeypatch):
    current_time = 100.0
    monkeypatch.setattr(time, "time", lambda: current_time)

    b = TokenBucket(max_tokens=5, refill_rate=1)
    assert b.get_remaining() == 5.0
    b.allow_request(tokens=2)
    assert b.get_remaining() == 3.0

    current_time += 0.5
    assert b.get_remaining() == 3.5


def test_bucket_does_not_overfill(monkeypatch):
    current_time = 100.0
    monkeypatch.setattr(time, "time", lambda: current_time)

    b = TokenBucket(max_tokens=2, refill_rate=100)
    b.allow_request(tokens=2)

    current_time += 1.0
    remaining = b.get_remaining()
    assert remaining == 2.0


# ── RateLimitStore.check_multi (M4: multi-scope atomicity) ──────────────


def test_check_multi_all_admit_consumes_each_scope():
    from meridian.api.ratelimitter import RateLimitStore

    store = RateLimitStore()
    scopes = [("global", 5.0, 1.0, "global"), ("model:x", 5.0, 1.0, "model")]
    assert store.check_multi(scopes) is None
    # Wall-clock refill drifts by ~microseconds between calls — compare with
    # tolerance rather than exact floats.
    assert store.get_or_create("global", 5.0, 1.0).get_remaining() == pytest.approx(4.0, abs=0.01)
    assert store.get_or_create("model:x", 5.0, 1.0).get_remaining() == pytest.approx(4.0, abs=0.01)


def test_check_multi_middle_scope_failure_consumes_nothing():
    """A model-scope rejection must leave the global and org buckets untouched
    (no burn, no partial consumption)."""
    from meridian.api.ratelimitter import RateLimitStore

    store = RateLimitStore()
    scopes = [
        ("global", 5.0, 1.0, "global"),
        ("model:x", 1.0, 0.000001, "model"),
        ("org:acme", 5.0, 1.0, "org"),
    ]
    assert store.check_multi(scopes) is None  # burn the single model token
    rejected = store.check_multi(scopes)
    assert rejected is not None
    assert rejected[0] == "model"
    # Global and org did not lose a token to the failed request — they still
    # hold exactly the 4 remaining from the one admitted call (3-scope consume).
    assert store.get_or_create("global", 5.0, 1.0).get_remaining() == pytest.approx(4.0, abs=0.01)
    assert store.get_or_create("org:acme", 5.0, 1.0).get_remaining() == pytest.approx(4.0, abs=0.01)


def test_check_multi_first_scope_failure_consumes_nothing():
    from meridian.api.ratelimitter import RateLimitStore

    store = RateLimitStore()
    scopes = [
        ("global", 1.0, 0.000001, "global"),
        ("org:acme", 5.0, 1.0, "org"),
    ]
    assert store.check_multi(scopes) is None
    rejected = store.check_multi(scopes)
    assert rejected is not None and rejected[0] == "global"
    # Org still holds the 4 left by the single admitted call — the global
    # rejection consumed nothing.
    assert store.get_or_create("org:acme", 5.0, 1.0).get_remaining() == pytest.approx(4.0, abs=0.01)


def test_check_multi_no_over_admission_under_threads():
    """50 threads racing a capacity-5 window must yield exactly 5 admissions:
    check+consume runs in one exclusive store-locked window, so the classic
    check-then-consumed-by-racer over-admission is impossible."""
    import threading

    from meridian.api.ratelimitter import RateLimitStore

    store = RateLimitStore()
    scopes = [("global", 5.0, 1e-9, "global")]
    results: list[bool] = []

    def worker() -> None:
        results.append(store.check_multi(scopes) is None)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 5


# ── Integration tests ───────────────────────────────────────────────────


@pytest.fixture
async def rl_client():
    """Client with rate limiting enabled: 1 token capacity, 1 token/sec refill."""
    cfg = MeridianConfig.from_dict({
        "gateway": {"host": "0.0.0.0", "port": 8080, "strategy": "least_inflight"},
        "health": {"interval_s": 60, "timeout_s": 2, "fail_threshold": 2, "success_threshold": 1},
        "logging": {"level": "DEBUG", "jsonl_path": _jsonl_path},
        "rate_limit": {"enabled": True, "token_capacity": 1, "token_refill_rate": 1},
        "backends": [
            {
                "name": "test-backend",
                "url": _mock_url,
                "engine": "mock",
                "model": "demo-model",
                "weight": 1,
                "tags": [],
                "health_endpoint": "/v1/models",
            }
        ],
    })
    await init_app(cfg, start_health=False)
    get_state().rate_limit.clear()

    transport = httpx.ASGITransport(app=meridian_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def rl_disabled_client():
    """Client with rate limiting disabled."""
    cfg = MeridianConfig.from_dict({
        "gateway": {"host": "0.0.0.0", "port": 8080, "strategy": "least_inflight"},
        "health": {"interval_s": 60, "timeout_s": 2, "fail_threshold": 2, "success_threshold": 1},
        "logging": {"level": "DEBUG", "jsonl_path": _jsonl_path},
        "rate_limit": {"enabled": False},
        "backends": [
            {
                "name": "test-backend",
                "url": _mock_url,
                "engine": "mock",
                "model": "demo-model",
                "weight": 1,
                "tags": [],
                "health_endpoint": "/v1/models",
            }
        ],
    })
    await init_app(cfg, start_health=False)
    get_state().rate_limit.clear()

    transport = httpx.ASGITransport(app=meridian_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def rl_burst_client():
    """Client with rate limiting: 3 token capacity, 0.1 token/sec refill (slow refill)."""
    cfg = MeridianConfig.from_dict({
        "gateway": {"host": "0.0.0.0", "port": 8080, "strategy": "least_inflight"},
        "health": {"interval_s": 60, "timeout_s": 2, "fail_threshold": 2, "success_threshold": 1},
        "logging": {"level": "DEBUG", "jsonl_path": _jsonl_path},
        "rate_limit": {"enabled": True, "token_capacity": 3, "token_refill_rate": 0.1},
        "backends": [
            {
                "name": "test-backend",
                "url": _mock_url,
                "engine": "mock",
                "model": "demo-model",
                "weight": 1,
                "tags": [],
                "health_endpoint": "/v1/models",
            }
        ],
    })
    await init_app(cfg, start_health=False)
    get_state().rate_limit.clear()

    transport = httpx.ASGITransport(app=meridian_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_rate_limit_blocks_second_request(rl_client):
    """First request passes, second immediate request gets 429."""
    resp1 = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp1.status_code == 200

    resp2 = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp2.status_code == 429
    assert resp2.json()["error"]["message"] == "Rate Limit Exceeded"
    assert "retry-after" in resp2.headers


@pytest.mark.asyncio
async def test_rate_limit_disabled_passes_all(rl_disabled_client):
    """With rate limiting disabled, multiple rapid requests all pass."""
    for _ in range(5):
        resp = await rl_disabled_client.post(
            "/v1/chat/completions",
            json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_uses_x_forwarded_for(rl_client):
    """Rate limit bucket is keyed on X-Forwarded-For header value."""
    # Request from IP-A consumes the bucket
    resp1 = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert resp1.status_code == 200

    # Same IP is blocked
    resp2 = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert resp2.status_code == 429

    # Different IP still passes
    resp3 = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert resp3.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_configurable_burst(rl_burst_client):
    """With capacity=3, three rapid requests pass, fourth is blocked."""
    for i in range(3):
        resp = await rl_burst_client.post(
            "/v1/chat/completions",
            json={"model": "demo-model", "messages": [{"role": "user", "content": f"msg {i}"}]},
        )
        assert resp.status_code == 200, f"Request {i+1} should pass"

    resp4 = await rl_burst_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "blocked"}]},
    )
    assert resp4.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_retry_after_header_value(rl_client):
    """Retry-After header reflects the refill interval."""
    resp = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "first"}]},
    )
    assert resp.status_code == 200

    resp2 = await rl_client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [{"role": "user", "content": "second"}]},
    )
    assert resp2.status_code == 429
    assert resp2.headers["retry-after"] == "1.0"
