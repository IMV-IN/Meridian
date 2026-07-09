"""Milestone H: rate limiting keys on org identity when auth is enabled.

Backend points at a closed port, so a request that *passes* the limiter returns
502 (limiter ok, upstream dead) and a *blocked* request returns 429 — a clean
signal that isolates the limiter from routing.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.config.models import MeridianConfig

KEY_ACME = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_GLOBEX = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _client() -> httpx.AsyncClient:
    cfg = MeridianConfig.from_dict({
        "rate_limit": {"enabled": True, "token_capacity": 1, "token_refill_rate": 0.001},
        "auth": {"enabled": True, "keys": [
            {"key": KEY_ACME, "org_id": "acme"},
            {"key": KEY_GLOBEX, "org_id": "globex"},
        ]},
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    get_state().rate_limit.clear()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=meridian_app), base_url="http://test")


def _body() -> dict:
    return {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_same_org_shares_bucket():
    async with await _client() as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert first.status_code == 502   # passed limiter, upstream dead
    assert second.status_code == 429  # same org, bucket exhausted


@pytest.mark.asyncio
async def test_different_orgs_are_independent():
    async with await _client() as c:
        a = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_ACME}"}, json=_body())
        b = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_GLOBEX}"}, json=_body())
    assert a.status_code == 502  # acme's first
    assert b.status_code == 502  # globex has its own bucket, not blocked by acme


@pytest.mark.asyncio
async def test_org_key_ignores_source_ip():
    """Same org from two different forwarded IPs still shares one bucket."""
    async with await _client() as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        first = await c.post("/v1/chat/completions", headers={**h, "x-forwarded-for": "10.0.0.1"}, json=_body())
        second = await c.post("/v1/chat/completions", headers={**h, "x-forwarded-for": "10.0.0.2"}, json=_body())
    assert first.status_code == 502
    assert second.status_code == 429  # different IP, same org → still limited
