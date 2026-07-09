"""Tests for rate limiting — TokenBucket unit tests and API integration."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meridian.api.ratelimitter import TokenBucket
from mock_backend.server import app as mock_app

# ── Find free port for mock backend ──────────────────────────────────────


def _find_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_mock_port = _find_free_port()
_config = uvicorn.Config(mock_app, host="127.0.0.1", port=_mock_port, log_level="error")
_server = uvicorn.Server(_config)
_thread = threading.Thread(target=_server.run, daemon=True)
_thread.start()

for _ in range(50):
    try:
        httpx.get(f"http://127.0.0.1:{_mock_port}/v1/models", timeout=0.5)
        break
    except Exception:
        time.sleep(0.1)

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
